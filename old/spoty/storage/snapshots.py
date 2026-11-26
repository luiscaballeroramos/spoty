import copy
import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from spoty.domain.listening import (
    ValidationError, canonical_json, parse_json, utc_text, validate_account,
)
from spoty.domain.taxonomy import validate_taxonomy
from spoty.storage.csv_codec import (
    MAX_CSV_BYTES, SCHEMAS, SNAPSHOT_SCHEMAS, Schema, decode_csv, encode_csv,
)
from spoty.storage.contracts import DataStore, GroupedReadStore, StorageError, validate_path
from spoty.services.coverage import coverage_projection
from spoty.services.metadata import METADATA_DATASETS


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


def snapshot_changes(
    base: "Snapshot", rows: dict[str, list[dict[str, Any]]], *,
    snapshot_id: str, published_at: str, max_bytes: int = MAX_CSV_BYTES,
    reuse_unchanged: bool = False,
    partitioned: dict[str, dict[str, Any]] | None = None,
) -> dict[str, bytes | None]:
    partitioned = partitioned or {}
    if partitioned and (
        not reuse_unchanged or max_bytes != MAX_CSV_BYTES or set(partitioned) & set(rows)
        or not set(partitioned) <= METADATA_DATASETS - {"smarterplaylists_import_runs"}
    ):
        raise ValidationError("Partitioned publication requires distinct provider datasets and reuse.")
    changed = set(rows) | set(partitioned)
    if reuse_unchanged:
        event_datasets = {"listening_events", "receipts"}
        catalog_datasets = {"tracks", "artists", "albums", "metadata_coverage", "track_audio"}
        metadata_required = {"receipts", "metadata_coverage"}
        metadata_allowed = metadata_required | METADATA_DATASETS | {"track_audio"}
        metadata_partial = (
            metadata_required <= changed <= metadata_allowed
            and ("track_audio" in changed) == ("smarterplaylists_track_metadata" in changed)
            and (not (changed & {f"smarterplaylists_{entity}_metadata" for entity in ("track", "artist", "album")})
                 or "smarterplaylists_import_runs" in rows)
        )
        if (base.manifest is None or base.manifest["schema_version"] != 6
                or not (changed <= event_datasets or changed == event_datasets | catalog_datasets
                        or metadata_partial)):
            raise ValidationError("Partial publication requires events/receipts, complete catalog projections "
                                  "or complete metadata projections on v6.")
    else:
        if set(rows) != set(SCHEMAS):
            raise ValidationError("Snapshot requires every current dataset.")
        rows = {**rows, "metadata_coverage": coverage_projection(rows, include_orphans=True)}
        validate_taxonomy(rows)
    prefix = f"accounts/{base.account}"
    changes: dict[str, bytes | None] = {
        path: None for path in base.store.list_paths(base.commit_id, f"{prefix}/datasets")
        if not reuse_unchanged or any(path.startswith(f"{prefix}/datasets/{dataset}/") for dataset in changed)
    }
    manifest = {
        "schema_version": 6, "account_key": base.account, "snapshot_id": snapshot_id,
        "parent_snapshot_id": base.manifest["snapshot_id"] if base.manifest else None,
        "published_at": published_at, "datasets": {},
    }
    for dataset, schema in SCHEMAS.items():
        if dataset in partitioned:
            candidate = {**base.manifest, "datasets": {**base.manifest["datasets"], dataset: partitioned[dataset]}}
            manifest["datasets"][dataset] = copy.deepcopy(
                Snapshot(base.store, base.account, base.commit_id, candidate).dataset_descriptor(dataset),
            )
            continue
        if dataset not in rows:
            # Preserve pinned bytes/descriptors; this is not a full integrity audit.
            manifest["datasets"][dataset] = copy.deepcopy(base.dataset_descriptor(dataset))
            continue
        descriptions = []
        for name, content in dataset_partitions(dataset, rows[dataset], max_bytes).items():
            relative = f"datasets/{dataset}/{name}"
            changes[f"{prefix}/{relative}"] = content
            descriptions.append(descriptor(relative, content, schema))
        manifest["datasets"][dataset] = {
            "schema_version": schema.version, "bucket_count": 16, "partitions": descriptions,
        }
    changes[f"{prefix}/manifest.json"] = (canonical_json(manifest) + "\n").encode("utf-8")
    return changes


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
        return list(self.iter_rows(dataset))

    def iter_rows(self, dataset: str):
        """Validate every consumed partition without retaining the whole dataset."""
        if dataset not in SCHEMAS:
            raise ValidationError("Unknown snapshot dataset.")
        if self.manifest is None:
            return
        if dataset not in self.manifest["datasets"]:
            return
        entry = self.dataset_descriptor(dataset)
        schema = SNAPSHOT_SCHEMAS[self.manifest["schema_version"]][dataset]
        seen = set()
        for part, data in self._partition_data(entry["partitions"]):
            if part["byte_size"] != len(data) or part["sha256"] != hashlib.sha256(data).hexdigest():
                raise ValidationError("Partition checksum or size mismatch.")
            rows = decode_csv(schema, data)
            if part["row_count"] != len(rows):
                raise ValidationError("Partition row count mismatch.")
            for row in rows:
                key = tuple(row[field] for field in SCHEMAS[dataset].keys)
                if key in seen:
                    raise ValidationError("Duplicate key across snapshot partitions.")
                seen.add(key)
                if dataset == "receipts":
                    if schema.version == 1:
                        row = {**row, "saved_metadata": 0, "duplicate_metadata": 0}
                    if schema.version < 3:
                        row = {**row, "saved_favorites": 0, "removed_favorites": 0}
                yield row

    def _partition_data(self, parts: list[dict[str, Any]]):
        if not isinstance(self.store, GroupedReadStore):
            for part in parts:
                yield part, self.store.read(self.commit_id, f"accounts/{self.account}/{part['path']}")
            return
        index = 0
        while index < len(parts):
            end, size = index, 0
            # Bound grouped output by declared bytes, not the whole raw dataset.
            while end < len(parts) and size + parts[end]["byte_size"] <= MAX_CSV_BYTES:
                size += parts[end]["byte_size"]
                end += 1
            group = parts[index:end]
            content = self.store.read_many(
                self.commit_id, [f"accounts/{self.account}/{part['path']}" for part in group],
            )
            if (not isinstance(content, list) or len(content) != len(group)
                    or any(not isinstance(data, bytes) for data in content)):
                raise StorageError("Grouped storage read returned an invalid result.")
            yield from zip(group, content)
            index = end

    def dataset_descriptor(self, dataset: str) -> dict[str, Any]:
        if self.manifest is None or dataset not in self.manifest["datasets"]:
            raise ValidationError("Dataset is not present in the snapshot.")
        entry = self.manifest["datasets"][dataset]
        schema = SNAPSHOT_SCHEMAS[self.manifest["schema_version"]][dataset]
        if (
            not isinstance(entry, dict) or type(entry.get("schema_version")) is not int
            or entry["schema_version"] != schema.version
            or entry.get("bucket_count") != 16
            or not isinstance(entry.get("partitions"), list) or not entry["partitions"]
        ):
            raise ValidationError("Invalid dataset descriptor.")
        seen_paths = set()
        for part in entry["partitions"]:
            if not isinstance(part, dict):
                raise ValidationError("Invalid partition descriptor.")
            path = part.get("path")
            validate_path(path)
            if not path.startswith(f"datasets/{dataset}/") or path in seen_paths:
                raise ValidationError("Partition path does not belong to its dataset.")
            seen_paths.add(path)
            if (
                type(part.get("byte_size")) is not int or not 0 < part["byte_size"] <= MAX_CSV_BYTES
                or not isinstance(part.get("sha256"), str)
                or not re.fullmatch(r"[a-f0-9]{64}", part["sha256"])
                or type(part.get("row_count")) is not int or part["row_count"] < 0
            ):
                raise ValidationError("Invalid partition integrity descriptor.")
        return entry

    def pending_batches(self, kind: str | None = None) -> int:
        if kind is not None and kind not in {"listening_capture", "metadata_capture", "favorites_capture"}:
            raise ValidationError("Unknown batch kind.")
        prefix = f"accounts/{self.account}/inbox"
        return len(self.store.list_paths(
            self.commit_id, f"{prefix}/{kind}" if kind else prefix,
        ))
