import csv
import io
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable

from spoty.domain.listening import (
    ListeningBatch, ValidationError, canonical_json, parse_json,
)
from spoty.domain.metadata import MetadataBatch
from spoty.domain.favorites import FavoritesBatch


MAX_CSV_BYTES = 20 * 1024 * 1024


class JsonNull(Enum):
    VALUE = "json-null"


JSON_NULL = JsonNull.VALUE


@dataclass(frozen=True)
class Field:
    name: str
    kind: str = "str"
    nullable: bool = False


@dataclass(frozen=True)
class Schema:
    fields: tuple[Field, ...]
    keys: tuple[str, ...]
    version: int = 1

    @property
    def header(self) -> tuple[str, ...]:
        return ("schema_version", *(field.name for field in self.fields))


SCHEMAS = {
    "artists": Schema((Field("id"), Field("name")), ("id",)),
    "albums": Schema((
        Field("id"), Field("name"), Field("artists", "json"), Field("tracks", "json", True),
        Field("total_tracks", "int"), Field("images", "json", True), Field("release_year", "int", True),
    ), ("id",)),
    "tracks": Schema((
        Field("id"), Field("name"), Field("duration_ms", "int"), Field("album_id"),
        Field("album_track", "int", True), Field("disc_number", "int", True),
        Field("artists_ids", "json"), Field("explicit", "int", True),
        Field("popularity", "int", True),
    ), ("id",)),
    "listening_events": Schema((
        Field("track_id"), Field("played_at", "int"), Field("context_source", nullable=True),
        Field("context_type", nullable=True), Field("context_id", nullable=True),
    ), ("track_id", "played_at")),
    "receipts": Schema((
        Field("batch_id"), Field("batch_kind"), Field("outcome"), Field("processed_at"),
        Field("snapshot_id"), Field("saved_events", "int"), Field("duplicate_events", "int"),
        Field("error_code", nullable=True),
    ), ("batch_id",)),
}
BATCH_SCHEMA = Schema((
    Field("account_key"), Field("batch_id"), Field("batch_kind"), Field("producer"),
    Field("captured_at"), Field("payload_json", "json"),
), ())

LEGACY_SCHEMAS = dict(SCHEMAS)
CORE_DATASETS = frozenset(SCHEMAS)
SCHEMAS["receipts"] = Schema((
    *LEGACY_SCHEMAS["receipts"].fields,
    Field("saved_metadata", "int"), Field("duplicate_metadata", "int"),
), ("batch_id",), version=2)
SCHEMAS["lastfm_track_metadata"] = Schema((
    Field("spotify_track_id"), Field("match_status"), Field("normalized_data", "json", True),
    Field("raw_response", "json", True), Field("fetched_at"),
), ("spotify_track_id",))
SCHEMAS["musicbrainz_recording_metadata"] = Schema((
    Field("spotify_track_id"), Field("match_status"), Field("recording_mbid", nullable=True),
    Field("normalized_data", "json", True), Field("raw_response", "json", True), Field("fetched_at"),
), ("spotify_track_id",))
SCHEMAS["acousticbrainz_analysis_metadata"] = Schema((
    Field("spotify_track_id"), Field("recording_mbid"), Field("analysis_level"), Field("match_status"),
    Field("normalized_data", "json", True), Field("raw_response", "json", True), Field("fetched_at"),
), ("spotify_track_id", "analysis_level"))
for _entity in ("track", "artist", "album"):
    SCHEMAS[f"smarterplaylists_{_entity}_metadata"] = Schema((
        Field(f"spotify_{_entity}_id"), Field("normalized_data", "json"), Field("field_metadata", "json"),
        Field("raw_response", "json", True), Field("missing_requested", "json"), Field("captured_at"),
        Field("last_attempt_status"), Field("last_attempt_at"), Field("last_success_at"),
        Field("next_attempt_at", nullable=True), Field("last_error_code", nullable=True),
        Field("last_success_run_id"),
    ), (f"spotify_{_entity}_id",))
SCHEMAS["smarterplaylists_import_runs"] = Schema((
    Field("capture_id"), Field("captured_at"), Field("imported_at"), Field("record_count", "int"),
    Field("details", "json"),
), ("capture_id",))
SCHEMAS["track_audio"] = Schema((
    Field("spotify_track_id"), Field("name", nullable=True), Field("audio", "json"),
), ("spotify_track_id",))

V2_SCHEMAS = dict(SCHEMAS)
SCHEMAS["receipts"] = Schema((
    *V2_SCHEMAS["receipts"].fields,
    Field("saved_favorites", "int"), Field("removed_favorites", "int"),
), ("batch_id",), version=3)
SCHEMAS["liked_tracks"] = Schema((Field("id"),), ("id",))
SCHEMAS["favorites_replica_state"] = Schema((
    Field("singleton_id", "int"), Field("applied_revision", "int"),
), ("singleton_id",))
SNAPSHOT_SCHEMAS = {1: LEGACY_SCHEMAS, 2: V2_SCHEMAS, 3: SCHEMAS}


def _encode(field: Field, value: Any) -> str:
    if value is None:
        if not field.nullable:
            raise ValidationError(f"Missing required field: {field.name}.")
        return "\\N"
    if field.kind == "int":
        if type(value) is not int:
            raise ValidationError(f"Expected integer: {field.name}.")
        text = str(value)
    elif field.kind == "json":
        text = canonical_json(None if value is JSON_NULL else value)
    else:
        if not isinstance(value, str):
            raise ValidationError(f"Expected string: {field.name}.")
        text = value
    return "\\" + text if text.startswith("\\") else text


def _decode(field: Field, text: str) -> Any:
    if text == "\\N":
        if not field.nullable:
            raise ValidationError(f"Unexpected null: {field.name}.")
        return None
    if text.startswith("\\\\"):
        text = text[1:]
    elif text.startswith("\\"):
        raise ValidationError(f"Invalid escape: {field.name}.")
    if field.kind == "json":
        value = parse_json(text)
        return JSON_NULL if value is None else value
    if field.kind == "int":
        if not re.fullmatch(r"-?(0|[1-9][0-9]*)", text):
            raise ValidationError(f"Invalid integer: {field.name}.")
        return int(text)
    return text


def encode_csv(schema: Schema, rows: Iterable[dict[str, Any]]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(schema.header)
    required = {field.name for field in schema.fields}
    for row in rows:
        if set(row) != required:
            raise ValidationError("CSV row has unexpected or missing columns.")
        writer.writerow([str(schema.version), *(_encode(field, row[field.name]) for field in schema.fields)])
    return output.getvalue().encode("utf-8")


def decode_csv(schema: Schema, data: bytes, *, max_bytes: int = MAX_CSV_BYTES) -> list[dict[str, Any]]:
    if len(data) > max_bytes:
        raise ValidationError("CSV exceeds the size limit.")
    try:
        text = data.decode("utf-8")
        # The standard default is too small for provider JSON cells.
        csv.field_size_limit(max(csv.field_size_limit(), max_bytes))
        reader = csv.reader(io.StringIO(text, newline=""), strict=True)
        if tuple(next(reader, ())) != schema.header:
            raise ValidationError("Unsupported CSV header.")
        result = []
        for number, cells in enumerate(reader, 2):
            if len(cells) != len(schema.header) or cells[0] != str(schema.version):
                raise ValidationError(f"Invalid CSV shape/version on row {number}.")
            result.append({
                field.name: _decode(field, cell)
                for field, cell in zip(schema.fields, cells[1:])
            })
        return result
    except (UnicodeError, csv.Error) as error:
        raise ValidationError("Invalid UTF-8 CSV.") from error


def encode_batch(batch: ListeningBatch | MetadataBatch | FavoritesBatch) -> bytes:
    # Revalidate mutable nested payloads before computing identity or publishing.
    batch = type(batch)(batch.account_key, batch.producer, batch.captured_at, batch.payloads)
    batch_id = batch.batch_id
    data = encode_csv(BATCH_SCHEMA, ({
        "account_key": batch.account_key, "batch_id": batch_id,
        "batch_kind": batch.batch_kind, "producer": batch.producer,
        "captured_at": batch.captured_at, "payload_json": payload,
    } for payload in batch.payloads))
    if len(data) > MAX_CSV_BYTES:
        raise ValidationError("Batch exceeds the size limit.")
    return data


def decode_batch(data: bytes) -> ListeningBatch | MetadataBatch | FavoritesBatch:
    rows = decode_csv(BATCH_SCHEMA, data)
    if not rows:
        raise ValidationError("Empty batch.")
    first = rows[0]
    envelope = ("account_key", "batch_id", "batch_kind", "producer", "captured_at")
    batch_types = {
        "listening_capture": ListeningBatch, "metadata_capture": MetadataBatch,
        "favorites_capture": FavoritesBatch,
    }
    if first["batch_kind"] not in batch_types or any(
        any(row[key] != first[key] for key in envelope) for row in rows
    ):
        raise ValidationError("Inconsistent batch envelope.")
    batch_type = batch_types[first["batch_kind"]]
    batch = batch_type(
        first["account_key"], first["producer"], first["captured_at"],
        tuple(row["payload_json"] for row in rows),
    )
    if batch.batch_id != first["batch_id"]:
        raise ValidationError("Batch identity mismatch.")
    return batch
