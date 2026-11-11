from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from spoty.domain.listening import (
    ListeningBatch, ValidationError, catalog_from_item,
)
from spoty.domain.metadata import MetadataBatch
from spoty.domain.favorites import FavoritesBatch
from spoty.services.favorites import (
    FavoriteRevisionBlocked, apply_favorites, validate_pending_revisions, verify_revision,
)
from spoty.services.metadata import apply_metadata, audio_projection
from spoty.services.metadata_consolidation import consolidate_metadata
from spoty.services.coverage import extend_catalog_projection
from spoty.storage.csv_codec import MAX_CSV_BYTES, SCHEMAS, decode_batch
from spoty.storage.contracts import DataStore, PublicationConflict, batch_path
from spoty.storage.snapshots import Snapshot, snapshot_changes


@dataclass(frozen=True)
class ConsolidationResult:
    commit_id: str
    accepted_batches: int
    rejected_batches: int
    saved_events: int
    duplicate_events: int
    saved_metadata: int = 0
    duplicate_metadata: int = 0
    saved_favorites: int = 0
    removed_favorites: int = 0


def consolidate(
    store: DataStore, account: str, *, max_bytes: int = MAX_CSV_BYTES,
    attempts: int = 3, batch_limit: int = 100,
) -> ConsolidationResult:
    if attempts < 1 or batch_limit < 1:
        raise ValidationError("Consolidation limits must be positive.")
    for _ in range(attempts):
        base = Snapshot.load(store, account)
        prefix = f"accounts/{account}"
        base_paths = set(store.list_paths(base.commit_id))
        inbox = store.list_paths(base.commit_id, f"{prefix}/inbox")[:batch_limit]
        validate_pending_revisions(
            store, base.commit_id, account, base.rows("favorites_replica_state"), base_paths,
        )
        if not inbox:
            return ConsolidationResult(base.commit_id, 0, 0, 0, 0)
        listening_only = (
            base.manifest is not None and base.manifest["schema_version"] == 6
            and max_bytes == MAX_CSV_BYTES
            and all(path.split("/")[3] == "listening_capture" for path in inbox)
        )
        metadata_only = (
            base.manifest is not None and base.manifest["schema_version"] == 6
            and max_bytes == MAX_CSV_BYTES
            and all(path.split("/")[3] == "metadata_capture" for path in inbox)
        )
        if metadata_only:
            snapshot_id = uuid4().hex
            published_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            try:
                commit, accepted, rejected, saved, duplicates = consolidate_metadata(
                    base, inbox, base_paths, snapshot_id=snapshot_id, published_at=published_at,
                )
            except PublicationConflict:
                continue
            return ConsolidationResult(commit, accepted, rejected, 0, 0, saved, duplicates)
        datasets = (
            {"tracks", "artists", "albums", "listening_events", "receipts"}
            if listening_only else set(SCHEMAS)
        )
        rows = {dataset: base.rows(dataset) for dataset in datasets}
        maps = {
            dataset: {
                tuple(row[key] for key in SCHEMAS[dataset].keys): row
                for row in entries
            } for dataset, entries in rows.items()
        }
        changes: dict[str, bytes | None] = {}
        snapshot_id = uuid4().hex
        published_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        accepted = rejected = saved_total = duplicate_total = 0
        metadata_total = duplicate_metadata_total = 0
        favorites_total = removed_favorites_total = 0
        catalog_added: dict[str, list[dict]] = {"tracks": [], "artists": [], "albums": []}
        for path in inbox:
            data, batch = store.read(base.commit_id, path), None
            error_code = None
            staged_metadata = None
            saved_metadata = duplicate_metadata = 0
            saved_favorites = removed_favorites = 0
            kind = path.split("/")[3]
            try:
                batch = batch or decode_batch(data)
                expected_path = batch_path(batch)
                if batch.account_key != account or path != expected_path:
                    raise ValidationError("Batch account/path mismatch.")
                if isinstance(batch, FavoritesBatch):
                    verify_revision(store, base.commit_id, batch, data)
                    staged_metadata, saved_favorites, removed_favorites = apply_favorites(batch, maps)
                elif isinstance(batch, MetadataBatch):
                    staged_metadata, saved_metadata, duplicate_metadata = apply_metadata(
                        batch, maps, published_at,
                    )
                else:
                    event_candidates = {}
                    for payload in batch.payloads:
                        event = {key: payload[key] for key in (
                            "track_id", "played_at", "context_source", "context_type", "context_id",
                        )}
                        key = (event["track_id"], event["played_at"])
                        previous = event_candidates.get(key, maps["listening_events"].get(key))
                        if previous is not None and previous != event:
                            raise ValidationError("Conflicting listening event.")
                        event_candidates[key] = event
            except ValidationError as error:
                if kind == "favorites_capture" or isinstance(batch, FavoritesBatch):
                    raise FavoriteRevisionBlocked(
                        "Favorites revision is invalid or missing; preserve inbox and repair before consolidation."
                    ) from error
                error_code = {
                    "listening_capture": "invalid_listening_batch",
                    "metadata_capture": "invalid_metadata_batch",
                }.get(kind, "invalid_capture_kind")
            batch_id = path.rsplit("/", 1)[-1].removesuffix(".csv")
            if (batch_id,) in maps["receipts"]:
                raise ValidationError("An acknowledged batch unexpectedly remains in inbox.")
            saved = duplicates = 0
            if error_code is None and batch is not None:
                if isinstance(batch, ListeningBatch):
                    for payload in batch.payloads:
                        for dataset, entries in catalog_from_item(payload["spotify_item"]).items():
                            for entry in entries:
                                if (entry["id"],) not in maps[dataset]:
                                    catalog_added[dataset].append(entry)
                                maps[dataset].setdefault((entry["id"],), entry)
                        key = (payload["track_id"], payload["played_at"])
                        if key in maps["listening_events"]:
                            duplicates += 1
                        else:
                            maps["listening_events"][key] = event_candidates[key]
                            saved += 1
                elif staged_metadata is not None:
                    maps = staged_metadata
                accepted += 1
                destination = path.replace("/inbox/", "/archive/", 1)
            else:
                rejected += 1
                destination = path.replace("/inbox/", "/quarantine/", 1)
            if destination in base_paths:
                raise ValidationError("Processed capture destination already exists.")
            changes[destination] = data
            changes[path] = None
            maps["receipts"][(batch_id,)] = {
                "batch_id": batch_id, "batch_kind": kind,
                "outcome": "rejected" if error_code else "consolidated",
                "processed_at": published_at, "snapshot_id": snapshot_id,
                "saved_events": saved, "duplicate_events": duplicates, "error_code": error_code,
                "saved_metadata": saved_metadata, "duplicate_metadata": duplicate_metadata,
                "saved_favorites": saved_favorites, "removed_favorites": removed_favorites,
            }
            saved_total += saved
            duplicate_total += duplicates
            metadata_total += saved_metadata
            duplicate_metadata_total += duplicate_metadata
            favorites_total += saved_favorites
            removed_favorites_total += removed_favorites
        if listening_only and any(catalog_added.values()):
            projection = extend_catalog_projection(
                {dataset: list(maps[dataset].values()) for dataset in catalog_added},
                catalog_added, base.rows("metadata_coverage"),
            )
            maps["metadata_coverage"] = {
                (row["entity_type"], row["entity_id"], row["source"]): row for row in projection
            }
            maps["track_audio"] = {
                (row["spotify_track_id"],): row for row in base.rows("track_audio")
            }
            for track in catalog_added["tracks"]:
                audio = maps["track_audio"].get((track["id"],))
                if audio is not None:
                    audio["name"] = track["name"] or audio["name"]
        if not listening_only:
            maps["track_audio"] = audio_projection(maps)
        published = {
            dataset: list(entries.values()) for dataset, entries in maps.items()
            if (not listening_only or any(catalog_added.values()) or dataset in {"listening_events", "receipts"})
        }
        changes.update(snapshot_changes(
            base, published,
            snapshot_id=snapshot_id, published_at=published_at, max_bytes=max_bytes,
            reuse_unchanged=listening_only,
        ))
        try:
            commit = store.publish(base.commit_id, changes, "Consolidate capture batches")
        except PublicationConflict:
            continue
        return ConsolidationResult(
            commit, accepted, rejected, saved_total, duplicate_total,
            metadata_total, duplicate_metadata_total,
            favorites_total, removed_favorites_total,
        )
    raise PublicationConflict("Consolidation exhausted conflict retries.")
