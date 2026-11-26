import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from services.smarterplaylists import CaptureBatch, normalize_capture
from spoty.domain.listening import (
    ValidationError, batch_identity, canonical_json, parse_json,
    reject_secrets, utc_text, validate_account,
)


def validate_record(record: dict[str, Any]) -> None:
    if not isinstance(record, dict):
        raise ValidationError("Expected a metadata record.")
    provider = record.get("provider")
    if provider == "smarterplaylists":
        if set(record) != {"provider", "capture", "allow_unregistered_track"}:
            raise ValidationError("Unexpected SmarterPlaylists capture fields.")
        if not isinstance(record["capture"], dict) or type(record["allow_unregistered_track"]) is not bool:
            raise ValidationError("Invalid SmarterPlaylists capture.")
        utc_text(record["capture"].get("captured_at"))
        normalized_capture(record)
        return
    statuses = {
        "lastfm": {"matched", "not_found"},
        "musicbrainz": {"matched", "not_found", "ambiguous"},
        "acousticbrainz": {"found", "not_found"},
    }
    if not isinstance(provider, str) or provider not in statuses:
        raise ValidationError("Unknown metadata provider.")
    allowed = {
        "provider", "spotify_track_id", "match_status", "normalized_data",
        "raw_response", "fetched_at",
    }
    if provider in {"musicbrainz", "acousticbrainz"}:
        allowed.add("recording_mbid")
    if provider == "acousticbrainz":
        allowed.add("analysis_level")
    if set(record) != allowed:
        raise ValidationError("Unexpected or missing metadata record fields.")
    track_id = record["spotify_track_id"]
    if not isinstance(track_id, str) or not re.fullmatch(r"[A-Za-z0-9]{22}", track_id):
        raise ValidationError("Invalid metadata Spotify identifier.")
    status = record["match_status"]
    if not isinstance(status, str) or status not in statuses[provider]:
        raise ValidationError("Invalid provider match status.")
    matched = status in {"matched", "found"}
    normalized = record["normalized_data"]
    if (matched and not isinstance(normalized, dict)) or (not matched and normalized is not None):
        raise ValidationError("Metadata content disagrees with match status.")
    if record["raw_response"] is not None and not isinstance(record["raw_response"], dict):
        raise ValidationError("Invalid original provider response.")
    if utc_text(record["fetched_at"]) != record["fetched_at"]:
        raise ValidationError("Metadata timestamp must use canonical UTC.")
    if provider in {"musicbrainz", "acousticbrainz"}:
        mbid = record["recording_mbid"]
        required = matched or provider == "acousticbrainz"
        if required:
            if not isinstance(mbid, str):
                raise ValidationError("Recording MBID is required.")
            try:
                UUID(mbid)
            except ValueError as error:
                raise ValidationError("Invalid recording MBID.") from error
        elif mbid is not None:
            raise ValidationError("Unmatched MusicBrainz records must not claim an MBID.")
        if provider == "musicbrainz" and matched and normalized.get("recording_mbid") != mbid:
            raise ValidationError("Normalized recording MBID disagrees with record.")
    if provider == "acousticbrainz":
        level = record["analysis_level"]
        if level not in ("low-level", "high-level"):
            raise ValidationError("Invalid AcousticBrainz analysis level.")
        key = "lowlevel" if level == "low-level" else "highlevel"
        if matched and (set(normalized) != {key} or not isinstance(normalized[key], dict)):
            raise ValidationError("Normalized analysis disagrees with analysis level.")


def normalized_capture(record: dict[str, Any]) -> CaptureBatch:
    try:
        capture = normalize_capture(record["capture"])
    except (ValueError, KeyError, TypeError) as error:
        raise ValidationError("Invalid SmarterPlaylists capture content.") from error
    if record["allow_unregistered_track"] and (
        len(capture.records) != 1 or capture.records[0].entity != "track"
    ):
        raise ValidationError("Live capture must contain exactly one track.")
    return capture


@dataclass(frozen=True)
class MetadataBatch:
    account_key: str
    producer: str
    captured_at: str
    payloads: tuple[dict[str, Any], ...]

    @property
    def batch_kind(self) -> str:
        return "metadata_capture"

    def __post_init__(self):
        validate_account(self.account_key)
        if not isinstance(self.producer, str) or not self.producer:
            raise ValidationError("Metadata producer is required.")
        if utc_text(self.captured_at) != self.captured_at or not self.payloads:
            raise ValidationError("Metadata batches need a canonical timestamp and records.")
        payloads = parse_json(canonical_json(sorted(self.payloads, key=canonical_json)))
        reject_secrets(payloads)
        for record in payloads:
            validate_record(record)
        object.__setattr__(self, "payloads", tuple(payloads))

    @classmethod
    def from_records(cls, account: str, producer: str, captured_at: str, records: list[dict[str, Any]]):
        return cls(account, producer, utc_text(captured_at), tuple(records))

    @classmethod
    def from_enrichment_records(
        cls, account: str, producer: str, fetched_at: str, records: list[dict[str, Any]],
    ):
        timestamp = utc_text(fetched_at)
        payloads = []
        for record in records:
            payload = {
                "normalized_data": None, "raw_response": None,
                "fetched_at": timestamp, **record,
            }
            if payload.get("provider") in ("musicbrainz", "acousticbrainz"):
                payload.setdefault("recording_mbid", None)
            payloads.append(payload)
        return cls.from_records(account, producer, timestamp, payloads)

    @classmethod
    def from_smarterplaylists(
        cls, account: str, producer: str, captured_at: str,
        captures: list[dict[str, Any]], *, live_track: bool = False,
    ):
        return cls.from_records(account, producer, captured_at, [{
            "provider": "smarterplaylists", "capture": capture,
            "allow_unregistered_track": live_track,
        } for capture in captures])

    @property
    def batch_id(self) -> str:
        return batch_identity(
            self.account_key, self.batch_kind, self.producer, self.captured_at, self.payloads,
        )
