import hashlib
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from services.genre_taxonomy import normalize_label, validate_seed
from spoty.domain.listening import ValidationError, canonical_json
from spoty.domain.taxonomy import TAXONOMY_DATASETS, validate_taxonomy
from spoty.storage.contracts import DataStore, PublicationConflict
from spoty.storage.csv_codec import SCHEMAS
from spoty.storage.snapshots import Snapshot, snapshot_changes


class GenreTaxonomyRepository:
    """Explicit additive edits published atomically; provider captures stay intact."""

    def __init__(self, store: DataStore, account: str, *, attempts: int = 3):
        if type(attempts) is not int or attempts < 1:
            raise ValidationError("Taxonomy publication attempts must be positive integers.")
        self.store = store
        self.account = account
        self.attempts = attempts

    def _publish(
        self, base: Snapshot, taxonomy: dict[str, list[dict[str, Any]]], now: str,
        extra: dict[str, bytes], message: str,
    ) -> None:
        rows = {dataset: taxonomy[dataset] if dataset in taxonomy else base.rows(dataset)
                for dataset in SCHEMAS}
        changes = snapshot_changes(base, rows, snapshot_id=uuid4().hex, published_at=now)
        changes.update(extra)
        self.store.publish(base.commit_id, changes, message)

    def import_seed(self, seed: dict) -> dict[str, int]:
        validate_seed(seed)
        data = (canonical_json(seed) + "\n").encode("utf-8")
        digest = hashlib.sha256(data).hexdigest()
        for _ in range(self.attempts):
            base = Snapshot.load(self.store, self.account)
            taxonomy = {dataset: base.rows(dataset) for dataset in TAXONOMY_DATASETS}
            validate_taxonomy(taxonomy)
            now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            origin = f"seed:{seed['version']}"
            maps = {
                dataset: {tuple(row[key] for key in SCHEMAS[dataset].keys): row for row in rows}
                for dataset, rows in taxonomy.items()
            }
            counts = {"genres_added": 0, "relations_added": 0, "mappings_added": 0}
            for genre in seed["genres"]:
                key = (genre["id"],)
                if key not in maps["genres"]:
                    maps["genres"][key] = {
                        "id": genre["id"], "name": genre["name"], "introduced_by": origin,
                        "created_at": now,
                    }
                    counts["genres_added"] += 1
                for parent in genre["parents"]:
                    key = (parent, genre["id"])
                    if key not in maps["genre_relations"]:
                        maps["genre_relations"][key] = {
                            "parent_id": parent, "child_id": genre["id"], "introduced_by": origin,
                        }
                        counts["relations_added"] += 1
                for label in genre["labels"]:
                    key = ("smarterplaylists", "genres", normalize_label(label))
                    previous = maps["genre_label_mappings"].get(key)
                    if previous is None or previous["status"] == "pending":
                        maps["genre_label_mappings"][key] = {
                            "source": key[0], "field": key[1], "normalized_label": key[2],
                            "genre_id": genre["id"], "status": "approved", "introduced_by": origin,
                            "created_at": previous["created_at"] if previous else now,
                        }
                        counts["mappings_added"] += 1
            taxonomy = {dataset: list(rows.values()) for dataset, rows in maps.items()}
            validate_taxonomy(taxonomy)
            path = f"accounts/{self.account}/taxonomy/imports/{digest}.json"
            paths = self.store.list_paths(base.commit_id, f"accounts/{self.account}/taxonomy/imports")
            extra = {}
            if path in paths:
                if self.store.read(base.commit_id, path) != data:
                    raise ValidationError("Archived taxonomy seed checksum mismatch.")
            else:
                extra[path] = data
            if not any(counts.values()) and not extra:
                return counts
            try:
                self._publish(base, taxonomy, now, extra, "Import taxonomy seed")
            except PublicationConflict:
                continue
            return counts
        raise PublicationConflict("Taxonomy import exhausted conflict retries.")

    def discover_labels(self, apply: bool = False) -> dict[str, Any]:
        if type(apply) is not bool:
            raise ValidationError("Taxonomy discovery apply must be a boolean.")
        for _ in range(self.attempts):
            base = Snapshot.load(self.store, self.account)
            taxonomy = {dataset: base.rows(dataset) for dataset in TAXONOMY_DATASETS}
            validate_taxonomy(taxonomy)
            known = {(row["source"], row["field"], row["normalized_label"])
                     for row in taxonomy["genre_label_mappings"]}
            artists = {row["id"] for row in base.rows("artists")}
            labels = set()
            for row in base.rows("smarterplaylists_artist_metadata"):
                metadata = row["normalized_data"]
                values = metadata.get("genres") if isinstance(metadata, dict) else None
                if row["spotify_artist_id"] in artists and isinstance(values, list):
                    labels.update(value.strip(" ").lower() for value in values
                                  if isinstance(value, str) and value.strip(" "))
            unknown = sorted(label for label in labels if ("smarterplaylists", "genres", label) not in known)
            result = {"unknown_labels": unknown, "pending_added": 0, "applied": apply}
            if not apply or not unknown:
                return result
            now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            taxonomy["genre_label_mappings"].extend({
                "source": "smarterplaylists", "field": "genres", "normalized_label": label,
                "genre_id": None, "status": "pending", "introduced_by": "discovery", "created_at": now,
            } for label in unknown)
            try:
                self._publish(base, taxonomy, now, {}, "Discover pending taxonomy labels")
            except PublicationConflict:
                continue
            result["pending_added"] = len(unknown)
            return result
        raise PublicationConflict("Taxonomy discovery exhausted conflict retries.")
