import re
from dataclasses import dataclass
from typing import Any

from spoty.domain.listening import (
    ValidationError, batch_identity, canonical_json, parse_json,
    reject_secrets, utc_text, utc_timestamp, validate_account,
)


def request_id(value: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{32}", value):
        raise ValidationError("Expected a stable hexadecimal favorite request ID.")


def spotify_id(value: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9]{22}", value):
        raise ValidationError("Invalid favorite Spotify identifier.")


def counter(value: int, *, minimum: int = 0) -> None:
    if type(value) is not int or not minimum <= value < 10**20:
        raise ValidationError("Invalid favorites coordination counter.")


def snapshot_ids(pages: list[dict[str, Any]]) -> list[str]:
    """Only a complete, consistent traversal may replace the favorite replica."""
    if not isinstance(pages, list) or not pages:
        raise ValidationError("A favorite snapshot needs complete pagination.")
    offset = 0
    expected_total = None
    ids = []
    for index, page in enumerate(pages):
        if not isinstance(page, dict) or not isinstance(page.get("items"), list):
            raise ValidationError("Invalid favorite page.")
        limit, total = page.get("limit"), page.get("total")
        if (
            type(page.get("offset")) is not int or page["offset"] != offset
            or type(limit) is not int or not 1 <= limit <= 50
            or type(total) is not int or total < 0
            or len(page["items"]) > limit
        ):
            raise ValidationError("Invalid favorite pagination metadata.")
        if expected_total is None:
            expected_total = total
        elif total != expected_total:
            raise ValidationError("Favorite total changed during pagination.")
        offset += len(page["items"])
        has_more = offset < total
        if (
            offset > total
            or (has_more and (not page["items"] or not isinstance(page.get("next"), str) or not page["next"]))
            or (not has_more and page.get("next") is not None)
            or has_more != (index < len(pages) - 1)
        ):
            raise ValidationError("Incomplete favorite pagination.")
        for item in page["items"]:
            track = item.get("track") if isinstance(item, dict) else None
            if not isinstance(track, dict):
                raise ValidationError("Favorite page contains an unavailable track.")
            track_id = track.get("id")
            spotify_id(track_id)
            ids.append(track_id)
    if len(ids) != len(set(ids)):
        raise ValidationError("Favorite pagination contains duplicate tracks.")
    return ids


@dataclass(frozen=True)
class FavoritesBatch:
    account_key: str
    producer: str
    captured_at: str
    payloads: tuple[dict[str, Any], ...]

    @property
    def batch_kind(self) -> str:
        return "favorites_capture"

    @property
    def revision(self) -> int:
        return self.payloads[0]["revision"]

    def __post_init__(self):
        validate_account(self.account_key)
        if not isinstance(self.producer, str) or not self.producer or len(self.payloads) != 1:
            raise ValidationError("Favorites need a producer and one ordered operation.")
        if utc_text(self.captured_at) != self.captured_at:
            raise ValidationError("Favorite timestamp must use canonical UTC.")
        payload = parse_json(canonical_json(self.payloads[0]))
        reject_secrets(payload)
        if not isinstance(payload, dict):
            raise ValidationError("Expected a favorite operation.")
        common = {"revision", "request_id", "operation", "started_at"}
        operation = payload.get("operation")
        if operation == "change":
            required = common | {"track_id", "liked", "desired_liked", "resolution"}
            spotify_id(payload.get("track_id"))
            if type(payload.get("liked")) is not bool or type(payload.get("desired_liked")) is not bool:
                raise ValidationError("Favorite observations must be boolean.")
            if payload.get("resolution") not in ("confirmed", "reconciled"):
                raise ValidationError("Unknown favorite resolution.")
            if payload["resolution"] == "confirmed" and payload["liked"] != payload["desired_liked"]:
                raise ValidationError("Confirmation disagrees with the requested favorite state.")
        elif operation == "snapshot":
            required = common | {"generation", "base_revision", "pages"}
            counter(payload.get("generation"), minimum=1)
            counter(payload.get("base_revision"))
            snapshot_ids(payload.get("pages"))
            if payload.get("revision") != payload["base_revision"] + 1:
                raise ValidationError("Favorite snapshot revision is not consecutive.")
        else:
            raise ValidationError("Unknown favorite operation.")
        if set(payload) != required:
            raise ValidationError("Unexpected favorite operation fields.")
        counter(payload["revision"], minimum=1)
        request_id(payload["request_id"])
        if utc_text(payload["started_at"]) != payload["started_at"] or (
            utc_timestamp(payload["started_at"]) > utc_timestamp(self.captured_at)
        ):
            raise ValidationError("Invalid favorite operation time interval.")
        object.__setattr__(self, "payloads", (payload,))

    @property
    def batch_id(self) -> str:
        return batch_identity(
            self.account_key, self.batch_kind, self.producer, self.captured_at, self.payloads,
        )
