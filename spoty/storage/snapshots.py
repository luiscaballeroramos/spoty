import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from spoty.domain.listening import (
    ValidationError, canonical_json, parse_json, utc_text, validate_account,
)
from spoty.storage.csv_codec import (
    MAX_CSV_BYTES, SCHEMAS, SNAPSHOT_SCHEMAS, Schema, decode_csv, encode_csv,
)
from spoty.storage.contracts import DataStore, validate_path


def partitions(schema: Schema, rows: list[dict[str, Any]], max_bytes: int = MAX_CSV_BYTES):
    if not 1 <= max_bytes <= MAX_CSV_BYTES:
        raise ValidationError("Invalid CSV partition size.")
    buckets: dict[int, list[dict[str, Any]]] = {}
    seen = set()
    for row in rows:
        key = tuple(row[field] for field in schema.keys)
        if key in seen:
            raise ValidationError("Duplicate dataset key.")
        seen.add(key)
        bucket = hashlib.sha256(canonical_json(key).encode("utf-8")).digest()[0] % 16
        buckets.setdefault(bucket, []).append(row)
    result = {}
    header = encode_csv(schema, [])
    if len(header) > max_bytes:
        raise ValidationError("CSV header exceeds the partition size.")
    for bucket, entries in sorted(buckets.items()):
        entries.sort(key=lambda row: tuple(row[field] for field in schema.keys))
        content = bytearray(header)
        part = 0
        for row in entries:
            row_data = encode_csv(schema, [row])[len(header):]
            if len(header) + len(row_data) > max_bytes:
                raise ValidationError("A CSV row exceeds the partition size.")
            if len(content) + len(row_data) > max_bytes:
                result[f"bucket-{bucket:02d}/part-{part:04d}.csv"] = bytes(content)
                part += 1
                content = bytearray(header)
            content.extend(row_data)
        result[f"bucket-{bucket:02d}/part-{part:04d}.csv"] = bytes(content)
    if not rows:
        result["bucket-00/part-0000.csv"] = header
    return result


def descriptor(path: str, data: bytes, schema: Schema) -> dict[str, Any]:
    validate_path(path)
    return {
        "path": path, "sha256": hashlib.sha256(data).hexdigest(),
        "byte_size": len(data), "row_count": len(decode_csv(schema, data)),
    }


def dataset_partitions(
    dataset: str, rows: list[dict[str, Any]], max_bytes: int = MAX_CSV_BYTES,
) -> dict[str, bytes]:
    if dataset not in SCHEMAS:
        raise ValidationError("Unknown partition dataset.")
    if dataset not in {"listening_events", "receipts"} or not rows:
        return partitions(SCHEMAS[dataset], rows, max_bytes)
    periods: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if dataset == "listening_events":
            try:
                period = datetime.fromtimestamp(row["played_at"], timezone.utc).strftime("%Y-%m")
            except (ValueError, OverflowError, OSError) as error:
                raise ValidationError("Listening timestamp is outside the supported range.") from error
        else:
            period = utc_text(row["processed_at"])[:7]
        periods.setdefault(period, []).append(row)
    return {
        f"{period}/{name}": data
        for period, entries in sorted(periods.items())
        for name, data in partitions(SCHEMAS[dataset], entries, max_bytes).items()
    }


@dataclass(frozen=True)
class Snapshot:
    store: DataStore
    account: str
    commit_id: str
    manifest: dict[str, Any] | None

    @classmethod
    def load(cls, store: DataStore, account: str, commit: str | None = None):
        validate_account(account)
        commit = commit or store.head()
        path = f"accounts/{account}/manifest.json"
        paths = store.list_paths(commit, f"accounts/{account}")
        if path not in paths:
            if any(
                folder in entry
                for entry in paths for folder in ("/datasets/", "/archive/", "/quarantine/")
            ):
                raise ValidationError("Consolidated data exists without a manifest.")
            return cls(store, account, commit, None)
        try:
            manifest = parse_json(store.read(commit, path).decode("utf-8"))
        except UnicodeError as error:
            raise ValidationError("Invalid manifest encoding.") from error
        if (
            not isinstance(manifest, dict) or type(manifest.get("schema_version")) is not int
            or manifest["schema_version"] not in SNAPSHOT_SCHEMAS
            or manifest.get("account_key") != account
            or not isinstance(manifest.get("datasets"), dict)
            or set(manifest["datasets"]) != set(SNAPSHOT_SCHEMAS[manifest["schema_version"]])
            or not isinstance(manifest.get("snapshot_id"), str)
            or not re.fullmatch(r"[a-f0-9]{32}", manifest["snapshot_id"])
        ):
            raise ValidationError("Invalid snapshot manifest.")
        parent = manifest.get("parent_snapshot_id")
        if parent is not None and (
            not isinstance(parent, str) or not re.fullmatch(r"[a-f0-9]{32}", parent)
        ):
            raise ValidationError("Invalid parent snapshot identity.")
        published_at = manifest.get("published_at")
        if utc_text(published_at) != published_at:
            raise ValidationError("Invalid snapshot publication timestamp.")
        return cls(store, account, commit, manifest)

    def rows(self, dataset: str) -> list[dict[str, Any]]:
        if dataset not in SCHEMAS:
            raise ValidationError("Unknown snapshot dataset.")
        if self.manifest is None:
            return []
        if dataset not in self.manifest["datasets"]:
            return []
        entry = self.manifest["datasets"][dataset]
        schema = SNAPSHOT_SCHEMAS[self.manifest["schema_version"]][dataset]
        if (
            not isinstance(entry, dict) or type(entry.get("schema_version")) is not int
            or entry["schema_version"] != schema.version
            or entry.get("bucket_count") != 16
            or not isinstance(entry.get("partitions"), list) or not entry["partitions"]
        ):
            raise ValidationError("Invalid dataset descriptor.")
        result = []
        seen_paths = set()
        for part in entry["partitions"]:
            if not isinstance(part, dict):
                raise ValidationError("Invalid partition descriptor.")
            path = part.get("path")
            validate_path(path)
            if not path.startswith(f"datasets/{dataset}/") or path in seen_paths:
                raise ValidationError("Partition path does not belong to its dataset.")
            seen_paths.add(path)
            data = self.store.read(self.commit_id, f"accounts/{self.account}/{path}")
            if (
                type(part.get("byte_size")) is not int or part["byte_size"] != len(data)
                or part.get("sha256") != hashlib.sha256(data).hexdigest()
            ):
                raise ValidationError("Partition checksum or size mismatch.")
            rows = decode_csv(schema, data)
            if type(part.get("row_count")) is not int or part["row_count"] != len(rows):
                raise ValidationError("Partition row count mismatch.")
            result.extend(rows)
        keys = [tuple(row[field] for field in SCHEMAS[dataset].keys) for row in result]
        if len(set(keys)) != len(keys):
            raise ValidationError("Duplicate key across snapshot partitions.")
        if dataset == "receipts":
            if schema.version == 1:
                result = [{**row, "saved_metadata": 0, "duplicate_metadata": 0} for row in result]
            if schema.version < 3:
                result = [{**row, "saved_favorites": 0, "removed_favorites": 0} for row in result]
        return result

    def pending_batches(self, kind: str | None = None) -> int:
        if kind is not None and kind not in {"listening_capture", "metadata_capture", "favorites_capture"}:
            raise ValidationError("Unknown batch kind.")
        prefix = f"accounts/{self.account}/inbox"
        return len(self.store.list_paths(
            self.commit_id, f"{prefix}/{kind}" if kind else prefix,
        ))
