import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


class ValidationError(ValueError):
    """Invalid input; messages identify the rule, never the provider payload."""


def canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise ValidationError("Unsupported JSON value.") from error


def parse_json(value: str) -> Any:
    def unique_keys(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                raise ValidationError("Duplicate JSON key.")
            result[key] = item
        return result

    def invalid_constant(_value):
        raise ValidationError("Nonfinite JSON number.")

    try:
        result = json.loads(
            value, object_pairs_hook=unique_keys, parse_constant=invalid_constant,
        )
        canonical_json(result)
        return result
    except (TypeError, ValueError) as error:
        raise ValidationError("Invalid JSON.") from error


def validate_account(account: str) -> None:
    if not isinstance(account, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", account):
        raise ValidationError("Invalid account key.")


def utc_timestamp(value: str) -> datetime:
    if not isinstance(value, str):
        raise ValidationError("Timestamp must be a string.")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError as error:
        raise ValidationError("Invalid timestamp.") from error
    if parsed.utcoffset() is None:
        raise ValidationError("Timestamp requires a timezone.")
    return parsed.astimezone(timezone.utc)


def utc_text(value: str) -> str:
    return utc_timestamp(value).isoformat().replace("+00:00", "Z")


def reject_secrets(value: Any) -> None:
    forbidden = {
        "access_token", "refresh_token", "client_secret", "password",
        "authorization", "cookie", "cookies",
    }
    if isinstance(value, dict):
        for key, item in value.items():
            if key.lower() in forbidden:
                raise ValidationError("Credentials are forbidden in capture data.")
            reject_secrets(item)
    elif isinstance(value, list):
        for item in value:
            reject_secrets(item)


def _object(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValidationError("Expected an object.")
    return value


def _string(value: Any, *, identifier: bool = False) -> str:
    if not isinstance(value, str) or not value:
        raise ValidationError("Expected a nonempty string.")
    if identifier and not re.fullmatch(r"[A-Za-z0-9]{22}", value):
        raise ValidationError("Invalid Spotify identifier.")
    return value


def _integer(value: Any, *, nullable: bool = False, maximum: int | None = None):
    if value is None and nullable:
        return None
    if type(value) is not int or value < 0 or (maximum is not None and value > maximum):
        raise ValidationError("Invalid integer field.")
    return value


def catalog_from_item(item: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    track = _object(item.get("track"))
    album = _object(track.get("album"))
    artists = {}
    artist_ids = []
    album_artist_ids = []
    for source, ids in ((track, artist_ids), (album, album_artist_ids)):
        entries = source.get("artists")
        if not isinstance(entries, list) or not entries:
            raise ValidationError("Expected artist records.")
        for entry in entries:
            entry = _object(entry)
            artist_id = _string(entry.get("id"), identifier=True)
            name = _string(entry.get("name"))
            artists.setdefault(artist_id, {"id": artist_id, "name": name})
            ids.append(artist_id)
    images = album.get("images", [])
    if not isinstance(images, list):
        raise ValidationError("Invalid album images.")
    image_urls = [_string(_object(image).get("url")) for image in images]
    explicit = track.get("explicit")
    if explicit is not None and type(explicit) is not bool:
        raise ValidationError("Invalid explicit flag.")
    release = album.get("release_date")
    precision = album.get("release_date_precision")
    release_year = None
    if release is not None:
        formats = {"year": "%Y", "month": "%Y-%m", "day": "%Y-%m-%d"}
        if precision not in formats or not isinstance(release, str):
            raise ValidationError("Invalid release date precision.")
        try:
            release_year = datetime.strptime(release, formats[precision]).year
        except ValueError as error:
            raise ValidationError("Invalid release date.") from error
    track_id = _string(track.get("id"), identifier=True)
    album_id = _string(album.get("id"), identifier=True)
    return {
        "artists": list(artists.values()),
        "albums": [{
            "id": album_id, "name": _string(album.get("name")),
            "artists": album_artist_ids, "tracks": [track_id],
            "total_tracks": _integer(album.get("total_tracks")),
            "images": image_urls, "release_year": release_year,
        }],
        "tracks": [{
            "id": track_id, "name": _string(track.get("name")),
            "duration_ms": _integer(track.get("duration_ms")), "album_id": album_id,
            "album_track": _integer(track.get("track_number"), nullable=True),
            "disc_number": _integer(track.get("disc_number"), nullable=True),
            "artists_ids": artist_ids,
            "explicit": None if explicit is None else int(explicit),
            "popularity": _integer(track.get("popularity"), nullable=True, maximum=100),
        }],
    }


def listening_payload(item: dict[str, Any]) -> dict[str, Any]:
    item = _object(parse_json(canonical_json(item)))
    reject_secrets(item)
    catalog = catalog_from_item(item)
    played_at = int(utc_timestamp(item.get("played_at")).timestamp())
    context = item.get("context")
    if context is not None and not isinstance(context, dict):
        raise ValidationError("Invalid listening context.")
    uri = (context or {}).get("uri")
    if uri is not None and not isinstance(uri, str):
        raise ValidationError("Invalid context URI.")
    parts = uri.split(":") if uri else []
    context_parts = parts if len(parts) == 3 else [None, None, None]
    return {
        "track_id": catalog["tracks"][0]["id"], "played_at": played_at,
        "context_source": context_parts[0], "context_type": context_parts[1],
        "context_id": context_parts[2], "spotify_item": item,
    }


def batch_identity(account: str, kind: str, producer: str, captured_at: str, payloads) -> str:
    identity = {
        "schema_version": 1, "account_key": account, "batch_kind": kind,
        "producer": producer, "captured_at": captured_at, "payloads": payloads,
    }
    return hashlib.sha256(canonical_json(identity).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ListeningBatch:
    account_key: str
    producer: str
    captured_at: str
    payloads: tuple[dict[str, Any], ...]

    @property
    def batch_kind(self) -> str:
        return "listening_capture"

    def __post_init__(self):
        validate_account(self.account_key)
        _string(self.producer)
        if self.captured_at != utc_text(self.captured_at):
            raise ValidationError("Batch timestamp must use canonical UTC.")
        if not self.payloads:
            raise ValidationError("Empty batches are not published.")
        for payload in self.payloads:
            if (
                not isinstance(payload, dict)
                or canonical_json(payload) != canonical_json(listening_payload(payload.get("spotify_item")))
            ):
                raise ValidationError("Listening payload does not match the Spotify item.")
        ordered = sorted(self.payloads, key=canonical_json)
        object.__setattr__(self, "payloads", tuple(parse_json(canonical_json(ordered))))

    @classmethod
    def from_items(cls, account: str, producer: str, captured_at: str, items: list[dict[str, Any]]):
        return cls(account, producer, utc_text(captured_at), tuple(map(listening_payload, items)))

    @property
    def batch_id(self) -> str:
        return batch_identity(
            self.account_key, self.batch_kind, self.producer, self.captured_at, self.payloads,
        )
