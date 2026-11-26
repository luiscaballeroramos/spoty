from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from spoty.domain.listening import ValidationError
from spoty.domain.favorites import FavoritesBatch
from spoty.domain.metadata import MetadataBatch, normalized_capture
from spoty.services.coverage import provider_projection, refresh_provider_projection
from spoty.services.metadata import PROVIDER_DATASETS, apply_metadata, audio_projection, metadata_datasets
from spoty.services.favorites import FavoriteRevisionBlocked
from spoty.storage.contracts import batch_path
from spoty.storage.csv_codec import SCHEMAS, decode_batch
from spoty.storage.snapshots import Snapshot, snapshot_changes
from spoty.storage.staging import DiskRows, FileChanges, staging_database


def _targets(batch: MetadataBatch) -> dict[str, set[tuple]]:
    result: dict[str, set[tuple]] = {}
    for payload in batch.payloads:
        if payload["provider"] == "smarterplaylists":
            for record in normalized_capture(payload).records:
                result.setdefault(f"smarterplaylists_{record.entity}_metadata", set()).add((record.spotify_id,))
        else:
            key = (payload["spotify_track_id"],)
            if payload["provider"] == "acousticbrainz":
                key += (payload["analysis_level"],)
            result.setdefault(PROVIDER_DATASETS[payload["provider"]], set()).add(key)
    return result


def consolidate_metadata(
    base: Snapshot, inbox: list[str], base_paths: set[str], *, snapshot_id: str, published_at: str,
) -> tuple[str, int, int, int, int]:
    """Stage providers on temporary disk; publish only after every validation."""
    with TemporaryDirectory(prefix="spoty-metadata-") as directory, \
            closing(staging_database(Path(directory) / "rows.sqlite")) as database:
        changes = FileChanges(Path(directory) / "changes")
        core = {dataset: {tuple(row[key] for key in SCHEMAS[dataset].keys): row for row in base.rows(dataset)}
                for dataset in ("tracks", "artists", "albums", "receipts", "metadata_coverage")}
        datasets: dict[str, DiskRows] = {}
        coverage_updates = {}
        imports = None
        audio = None
        accepted = rejected = saved = duplicates = 0
        for path in inbox:
            data = base.store.read(base.commit_id, path)
            batch = None
            staged = None
            saved_batch = duplicate_batch = 0
            try:
                batch = decode_batch(data)
                if not isinstance(batch, MetadataBatch) or batch.account_key != base.account or batch_path(batch) != path:
                    raise ValidationError("Batch account/path mismatch.")
            except ValidationError as error:
                if isinstance(batch, FavoritesBatch):
                    raise FavoriteRevisionBlocked(
                        "Favorites revision is invalid or missing; preserve inbox and repair before consolidation."
                    ) from error
                invalid = True
            else:
                invalid = False
                affected = metadata_datasets(batch)
                if "smarterplaylists_import_runs" in affected and imports is None:
                    imports = {(row["capture_id"],): row for row in base.rows("smarterplaylists_import_runs")}
                if "smarterplaylists_track_metadata" in affected and audio is None:
                    audio = {(row["spotify_track_id"],): row for row in base.rows("track_audio")}
                targets = _targets(batch)
                for dataset in targets:
                    if dataset not in datasets:
                        disk = DiskRows(database, dataset)
                        coverage_updates[dataset] = {}
                        if dataset == "smarterplaylists_track_metadata":
                            audio = {}
                        for row in base.iter_rows(dataset):
                            disk.put(row)
                            key = tuple(row[field] for field in disk.schema.keys)
                            coverage_updates[dataset][key] = list(provider_projection(dataset, [row]))
                            if dataset == "smarterplaylists_track_metadata" and audio is not None:
                                audio.update(audio_projection({
                                    "tracks": core["tracks"], "smarterplaylists_track_metadata": {key: row},
                                }))
                        datasets[dataset] = disk
                maps = {**core}
                if imports is not None:
                    maps["smarterplaylists_import_runs"] = imports
                for dataset, keys in targets.items():
                    maps[dataset] = {}
                    for key in keys:
                        row = datasets[dataset].get(key)
                        if row is not None:
                            maps[dataset][key] = row
                try:
                    staged, saved_batch, duplicate_batch = apply_metadata(batch, maps, published_at)
                except ValidationError:
                    invalid = True
            batch_id = path.rsplit("/", 1)[-1].removesuffix(".csv")
            if (batch_id,) in core["receipts"]:
                raise ValidationError("An acknowledged batch unexpectedly remains in inbox.")
            if not invalid and staged is not None:
                for dataset in datasets:
                    if dataset in staged:
                        for row in staged[dataset].values():
                            datasets[dataset].put(row, replace=True)
                            key = tuple(row[field] for field in datasets[dataset].schema.keys)
                            coverage_updates[dataset][key] = list(provider_projection(dataset, [row]))
                if audio is not None and "smarterplaylists_track_metadata" in staged:
                    audio.update(audio_projection({
                        "tracks": core["tracks"],
                        "smarterplaylists_track_metadata": staged["smarterplaylists_track_metadata"],
                    }))
                if imports is not None:
                    imports = staged["smarterplaylists_import_runs"]
                accepted += 1
                saved += saved_batch
                duplicates += duplicate_batch
                destination = path.replace("/inbox/", "/archive/", 1)
            else:
                rejected += 1
                saved_batch = duplicate_batch = 0
                destination = path.replace("/inbox/", "/quarantine/", 1)
            if destination in base_paths:
                raise ValidationError("Processed capture destination already exists.")
            changes[destination] = data
            changes[path] = None
            core["receipts"][(batch_id,)] = {
                "batch_id": batch_id, "batch_kind": "metadata_capture",
                "outcome": "rejected" if invalid else "consolidated",
                "processed_at": published_at, "snapshot_id": snapshot_id,
                "saved_events": 0, "duplicate_events": 0,
                "error_code": "invalid_metadata_batch" if invalid else None,
                "saved_metadata": saved_batch, "duplicate_metadata": duplicate_batch,
                "saved_favorites": 0, "removed_favorites": 0,
            }
        projection_rows = {dataset: list(entries.values()) for dataset, entries in core.items()}
        projection = refresh_provider_projection(
            projection_rows, projection_rows["metadata_coverage"],
            {dataset: (row for entries in values.values() for row in entries)
             for dataset, values in coverage_updates.items()},
        )
        light_rows = {"receipts": projection_rows["receipts"], "metadata_coverage": projection}
        if imports is not None:
            light_rows["smarterplaylists_import_runs"] = list(imports.values())
        if audio is not None:
            light_rows["track_audio"] = list(audio.values())
        partitioned = {dataset: disk.publish_parts(changes, base.account) for dataset, disk in datasets.items()}
        light_changes = snapshot_changes(
            base, light_rows, snapshot_id=snapshot_id, published_at=published_at,
            reuse_unchanged=True, partitioned=partitioned,
        )
        for path, content in light_changes.items():
            if content is not None or path not in changes:
                changes[path] = content
        commit = base.store.publish(base.commit_id, changes, "Consolidate capture batches")
        return commit, accepted, rejected, saved, duplicates
