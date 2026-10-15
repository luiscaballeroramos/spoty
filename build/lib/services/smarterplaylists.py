"""Validate offline Smarter Playlists captures without network or database access."""

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


ENTITIES = ("track", "artist", "album")
UNIT_FIELDS = {
    "acousticness", "danceability", "energy", "instrumentalness", "liveness",
    "speechiness", "valence",
}
COMMON_FIELDS = {"id", "name", "uri", "popularity", "images"}
ENTITY_FIELDS = {
    "track": COMMON_FIELDS | UNIT_FIELDS | {
        "artist", "artist_id", "artists", "album", "album_id", "release_date",
        "release_year", "release_decade", "duration_ms", "explicit", "isrc",
        "key", "mode", "loudness", "tempo", "time_signature", "camelot",
        "camelot_num", "disc_number", "track_number", "is_playable",
    },
    "artist": COMMON_FIELDS | {
        "followers", "genres", "mb_genres", "artist_country", "artist_area",
        "artist_type", "artist_gender", "artist_start_year", "artist_end_year",
    },
    "album": COMMON_FIELDS | {
        "artist", "artist_id", "album_type", "label", "release_date", "total_tracks",
    },
}
INTEGER_FIELDS = {
    "popularity", "duration_ms", "release_year", "release_decade", "key", "mode",
    "time_signature", "camelot_num", "disc_number", "track_number", "followers",
    "artist_start_year", "artist_end_year", "total_tracks",
}
BOOLEAN_FIELDS = {"explicit", "is_playable"}
LIST_FIELDS = {"artists", "genres", "mb_genres"}
SECRET_FIELDS = {"access_token", "refresh_token", "client_secret", "password",
                 "authorization", "cookie", "cookies"}


@dataclass(frozen=True)
class CaptureRecord:
    entity: str
    spotify_id: str
    normalized_data: dict[str, Any]
    field_metadata: dict[str, Any]
    raw_response: dict[str, Any]
    missing_requested: list[str]
    status: str


@dataclass(frozen=True)
class CaptureBatch:
    capture_id: str
    captured_at: datetime
    records: tuple[CaptureRecord, ...]


def _timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("Capture timestamps must be ISO strings with a timezone.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("Invalid capture timestamp.") from error
    if parsed.utcoffset() is None:
        raise ValueError("Capture timestamps must include a timezone.")
    return parsed.astimezone(timezone.utc)


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False)


def _reject_secrets(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key.lower() in SECRET_FIELDS:
                raise ValueError("Captures must not contain credentials or session data.")
            _reject_secrets(item)
    elif isinstance(value, list):
        for item in value:
            _reject_secrets(item)


def _validate_field(field: str, value: Any) -> None:
    if field in UNIT_FIELDS | {"tempo", "loudness"}:
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError(f"Invalid numeric field: {field}")
        if field in UNIT_FIELDS and not 0 <= value <= 1:
            raise ValueError(f"Out-of-range field: {field}")
        if field == "tempo" and value < 0:
            raise ValueError("Tempo must be nonnegative.")
    elif field in INTEGER_FIELDS:
        if type(value) is not int or value < 0:
            raise ValueError(f"Invalid integer field: {field}")
        if field != "followers" and value > 2147483647:
            raise ValueError(f"Integer exceeds PostgreSQL INTEGER: {field}")
        if field == "popularity" and value > 100:
            raise ValueError("Popularity must be between 0 and 100.")
        if field == "key" and value > 11:
            raise ValueError("Key must be between 0 and 11.")
        if field == "mode" and value not in (0, 1):
            raise ValueError("Mode must be 0 or 1.")
        if field == "followers" and value > 9223372036854775807:
            raise ValueError("Followers exceed PostgreSQL BIGINT.")
    elif field in BOOLEAN_FIELDS:
        if type(value) is not bool:
            raise ValueError(f"Invalid boolean field: {field}")
    elif field in LIST_FIELDS:
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise ValueError(f"Invalid string list: {field}")
    elif field == "images":
        if not isinstance(value, list) or any(
            not isinstance(image, dict) or not isinstance(image.get("url"), str)
            for image in value
        ):
            raise ValueError("Invalid image list.")
    elif not isinstance(value, str):
        raise ValueError(f"Invalid string field: {field}")
    if field in {"id", "artist_id", "album_id"} and not re.fullmatch(r"[A-Za-z0-9]{22}", value):
        raise ValueError(f"Invalid Spotify identifier: {field}")
    if field == "release_date" and not re.fullmatch(r"\d{4}(-\d{2}(-\d{2})?)?", value):
        raise ValueError("Invalid release date precision.")
    if field == "release_date":
        date.fromisoformat(value + ("-01-01" if len(value) == 4 else "-01" if len(value) == 7 else ""))


def _fields(values: dict[str, Any], prefix: str = ""):
    for field, value in values.items():
        path = f"{prefix}.{field}" if prefix else field
        if isinstance(value, dict):
            yield from _fields(value, path)
        else:
            yield path, value


def _related_data(values: dict[str, Any]) -> dict[str, Any]:
    related = {}
    mappings = {
        "related_artist": {"artist_id": "id", "artist": "name", "artist_popularity": "popularity",
                           "artist_followers": "followers", "mb_genres": "mb_genres",
                           **{field: field for field in ENTITY_FIELDS["artist"] if field.startswith("artist_")}},
        "related_album": {"album_id": "id", "album": "name", "album_popularity": "popularity",
                          "album_release_date": "release_date", "album_type": "album_type",
                          "label": "label", "total_tracks": "total_tracks"},
    }
    for section, mapping in mappings.items():
        fields = {}
        for original, normalized in mapping.items():
            if original in values:
                _validate_field(normalized, values[original])
                fields[normalized] = values[original]
        if fields:
            related[section] = fields
    return related


def normalize_capture(payload: Any) -> CaptureBatch:
    if not isinstance(payload, dict):
        raise ValueError("Expected a capture object.")
    _reject_secrets(payload)
    captured_at = _timestamp(payload.get("captured_at"))
    entities = payload.get("entities")
    if not isinstance(entities, dict) or not entities or set(entities) - set(ENTITIES):
        raise ValueError("Expected track, artist and/or album capture entities.")
    records = []
    for entity, result in sorted(entities.items()):
        if not isinstance(result, dict) or result.get("conflicts") != {}:
            raise ValueError(f"Invalid or conflicting {entity} capture.")
        raw = result.get("raw")
        if not isinstance(raw, dict) or raw.get("status") != "complete" or raw.get("playlist_uri"):
            raise ValueError("Only completed captures without playlist output are accepted.")
        rows = raw.get("tracks")
        if not isinstance(rows, list) or not rows:
            raise ValueError("Empty results are not proof that an entity does not exist.")
        values: dict[str, Any] = {}
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("Invalid result row.")
            spotify_id = row.get("id")
            _validate_field("id", spotify_id)
            if row.get("uri") != f"spotify:{entity}:{spotify_id}":
                raise ValueError("Spotify ID and entity URI disagree.")
            for field, value in row.items():
                if value is None:
                    continue
                if field in values and _canonical(values[field]) != _canonical(value):
                    raise ValueError(f"Conflicting values for {entity}.{field}")
                values[field] = value
        if _canonical(values) != _canonical(result.get("values")):
            raise ValueError("Summary differs from the original response.")
        missing = result.get("missing_requested")
        if not isinstance(missing, list) or any(not isinstance(field, str) for field in missing):
            raise ValueError("Expected a list of missing requested fields.")
        if set(missing) & values.keys():
            raise ValueError("A field cannot be present and missing simultaneously.")
        normalized = {}
        for field in sorted(ENTITY_FIELDS[entity] & values.keys()):
            value = values[field]
            if field == "key" and value == -1:
                continue
            _validate_field(field, value)
            normalized[field] = value
        if not normalized.get("name"):
            raise ValueError("Entity names must be nonempty.")
        if entity == "track":
            normalized.update(_related_data(values))
        if values.get("key") == -1 and "key" not in missing:
            missing = [*missing, "key"]
        records.append(CaptureRecord(
            entity=entity,
            spotify_id=values["id"],
            normalized_data=normalized,
            field_metadata={field: {
                "captured_at": captured_at.isoformat(),
                "resource": "smarterplaylists",
                "reported_source": "musicbrainz" if field.endswith("mb_genres") else "unspecified",
                "upstream_fetched_at": None,
            } for field, _value in _fields(normalized)},
            raw_response=raw,
            missing_requested=missing,
            status="partial" if missing else "matched",
        ))
    identity = {"captured_at": captured_at.isoformat(), "records": [
        {"entity": record.entity, "raw": record.raw_response,
         "missing_requested": record.missing_requested}
        for record in records
    ]}
    capture_id = hashlib.sha256(_canonical(identity).encode("utf-8")).hexdigest()
    return CaptureBatch(capture_id, captured_at, tuple(records))


def merge_metadata(existing: dict[str, Any], record: CaptureRecord, capture_id: str):
    values = json.loads(_canonical(existing.get("normalized_data") or {}))
    metadata = dict(existing.get("field_metadata") or {})
    existing_fields = dict(_fields(values))
    for field, value in _fields(record.normalized_data):
        incoming = {**record.field_metadata[field], "capture_id": capture_id}
        previous = metadata.get(field)
        if previous:
            old_time = _timestamp(previous["captured_at"])
            new_time = _timestamp(incoming["captured_at"])
            if new_time < old_time:
                continue
            if new_time == old_time and _canonical(existing_fields.get(field)) != _canonical(value):
                raise ValueError(f"Conflicting captures at the same time: {field}")
        target = values
        parts = field.split(".")
        for part in parts[:-1]:
            target = target.setdefault(part, {})
        target[parts[-1]] = value
        metadata[field] = incoming
    return values, metadata


def load_capture(path: str | Path) -> CaptureBatch:
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON keys are not allowed.")
            result[key] = value
        return result

    def invalid_number(_value):
        raise ValueError("Nonfinite JSON numbers are not allowed.")

    with Path(path).open("rb") as source:
        content = source.read(20_000_001)
    if len(content) > 20_000_000:
        raise ValueError("Capture exceeds the 20 MB import limit.")
    payload = json.loads(content.decode("utf-8-sig"), object_pairs_hook=unique_object,
                         parse_constant=invalid_number)
    return normalize_capture(payload)


def import_capture(path, *, dry_run=True, database_url=None, repository=None):
    batch = load_capture(path)
    result = {
        "operation": "smarterplaylists_import",
        "capture_id": batch.capture_id,
        "captured_at": batch.captured_at.isoformat(),
        "entities": [{"entity": record.entity, "spotify_id": record.spotify_id,
                      "status": record.status, "fields": len(record.field_metadata),
                      "missing_requested": record.missing_requested}
                     for record in batch.records],
    }
    if dry_run:
        return {**result, "status": "validated", "records_saved": 0,
                "local_identity_check": "not_performed", "dry_run": True}
    if database_url is None:
        raise ValueError("An explicit database URL is required to apply an import.")
    if repository is None:
        from register.smarterplaylists import SmarterPlaylistsRepository

        repository = SmarterPlaylistsRepository()
    return {**result, **repository.import_batch(database_url, batch),
            "local_identity_check": "passed", "dry_run": False}