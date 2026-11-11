import copy
import hashlib
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from services.genre_taxonomy import load_seed
from spoty.domain.listening import ValidationError, canonical_json, parse_json
from spoty.domain.taxonomy import validate_taxonomy
from spoty.services.consolidation import consolidate
from spoty.services.genre_taxonomy import GenreTaxonomyRepository
from spoty.services.summary_queries import SummaryQueryService
from spoty.storage.contracts import PublicationConflict, StorageError
from spoty.storage.csv_codec import SNAPSHOT_SCHEMAS, encode_csv
from spoty.storage.local_git import LocalGitStore
from spoty.storage.snapshots import Snapshot, descriptor
from tests.test_csv_git_prototype import batch, item
from tests import test_csv_summary_queries as summary_fixtures
from tests.test_csv_metadata_prototype import ARTIST, capture, smarter


ACCOUNT = "test-account"
DATE = "2026-11-09T10:00:00Z"


def seed():
    return {"version": 1, "genres": [
        {"id": "rock", "name": "Rock", "parents": [], "labels": ["rock"]},
        {"id": "folk", "name": "Folk", "parents": [], "labels": ["folk"]},
        {"id": "pop", "name": "Pop", "parents": [], "labels": ["pop"]},
        {"id": "folk-rock", "name": "Folk Rock", "parents": ["folk", "rock"], "labels": ["folk rock"]},
        {"id": "grunge", "name": "Grunge", "parents": ["rock"], "labels": ["grunge", "indie rock"]},
    ]}


class WritableMemoryStore(summary_fixtures.MemoryStore):
    def __init__(self):
        super().__init__()
        self.commits[self.current] = {}
        self.publications = 0

    def publish(self, expected, changes, message):
        if self.current != expected:
            raise PublicationConflict("Concurrent synthetic publication.")
        files = dict(self.commits[expected])
        for path, data in changes.items():
            if data is None:
                files.pop(path, None)
            else:
                files[path] = data
        self.publications += 1
        self.current = hashlib.sha1(f"{expected}:{message}:{self.publications}".encode()).hexdigest()
        self.commits[self.current] = files
        return self.current


class CsvGenreTaxonomyTest(unittest.TestCase):
    def setUp(self):
        self.fixture = summary_fixtures.CsvSummaryQueriesTest()
        self.fixture.setUp()
        self.store = WritableMemoryStore()
        self.fixture.store = self.store
        self.repository = GenreTaxonomyRepository(self.store, ACCOUNT)

    def snapshot(self):
        return Snapshot.load(self.store, ACCOUNT)

    def test_seed_import_archive_idempotency_and_old_commit(self):
        before = self.snapshot()
        self.assertEqual(self.repository.import_seed(seed()), {
            "genres_added": 5, "relations_added": 3, "mappings_added": 6,
        })
        current = self.snapshot()
        self.assertEqual(current.manifest["schema_version"], 6)
        paths = self.store.list_paths(current.commit_id, f"accounts/{ACCOUNT}/taxonomy/imports")
        self.assertEqual(len(paths), 1)
        self.assertEqual(parse_json(self.store.read(current.commit_id, paths[0]).decode()), seed())
        self.assertEqual(before.rows("genres"), [])
        self.assertEqual(self.repository.import_seed(seed()), {
            "genres_added": 0, "relations_added": 0, "mappings_added": 0,
        })
        self.assertEqual(self.snapshot().commit_id, current.commit_id)
        self.assertEqual(self.snapshot().rows("genres"), current.rows("genres"))

    def test_packaged_seed_import_uses_existing_validation_and_all_relations(self):
        original = load_seed()
        result = self.repository.import_seed(original)
        self.assertEqual(result["genres_added"], len(original["genres"]))
        self.assertEqual(result["relations_added"], sum(len(row["parents"]) for row in original["genres"]))
        self.assertEqual(result["mappings_added"], sum(len(row["labels"]) for row in original["genres"]))
        self.assertTrue(all(value == 0 for value in self.repository.import_seed(original).values()))

    def populate_listens(self):
        self.fixture.seed()
        self.fixture.datasets["tracks"].append({
            **self.fixture.datasets["tracks"][0], "id": "track-d", "artists_ids": ["artist-d"],
        })
        self.fixture.datasets["artists"].extend([
            {"id": "artist-c", "name": "Artist C"}, {"id": "artist-d", "name": "Artist D"},
        ])
        self.fixture.datasets["tracks"][1]["artists_ids"] = ["artist-c"]
        self.fixture.datasets["tracks"][2]["artists_ids"] = []
        self.fixture.datasets["listening_events"] = [
            {"track_id": track_id, "played_at": when,
             "context_source": None, "context_type": None, "context_id": None}
            for track_id, plays in (("track-a", 3), ("track-b", 1), ("track-c", 1), ("track-d", 1))
            for when in range(plays)
        ]
        self.fixture.datasets["smarterplaylists_artist_metadata"] = [
            self.fixture.metadata("artist", artist_id, {"genres": labels})
            for artist_id, labels in (
                ("artist-a", ["indie rock", "grunge", "folk rock", "new style"]),
                ("artist-b", ["grunge", " ROCK "]), ("artist-c", ["new style"]),
                ("artist-d", ["noise-label"]), ("uncataloged", ["orphan label"]),
            )
        ]
        self.fixture.snapshot()

    def mapping(self, label, status="excluded", genre_id=None):
        return {
            "source": "smarterplaylists", "field": "genres", "normalized_label": label,
            "genre_id": genre_id, "status": status, "introduced_by": "manual", "created_at": DATE,
        }

    def test_family_counts_match_existing_sql_example_and_keep_overlap(self):
        self.populate_listens()
        self.fixture.datasets["genre_label_mappings"] = [self.mapping("noise-label")]
        self.fixture.snapshot()
        self.repository.import_seed(seed())
        result = SummaryQueryService(self.snapshot()).top_listened_genres(grouping="families")
        self.assertEqual(result, {
            "total_listens": 6, "classified_listens": 3, "listened_tracks": 4,
            "classified_tracks": 1, "genres": [
                {"genre": "Folk", "weighted_listens": 3.0, "share_percent": 100.0},
                {"genre": "Rock", "weighted_listens": 3.0, "share_percent": 100.0},
            ],
        })
        labels = SummaryQueryService(self.snapshot()).top_listened_genres()
        self.assertEqual(labels["classified_listens"], 5)
        self.assertEqual(len(SummaryQueryService(self.snapshot()).top_listened_genres(
            limit=1, grouping="families")["genres"]), 1)

    def test_discovery_preview_requires_apply_and_retains_pending_or_excluded(self):
        self.populate_listens()
        self.fixture.datasets["genre_label_mappings"] = [self.mapping("noise-label")]
        self.fixture.snapshot()
        self.repository.import_seed(seed())
        old = self.snapshot()
        self.assertEqual(self.repository.discover_labels(), {
            "unknown_labels": ["new style"], "pending_added": 0, "applied": False,
        })
        self.assertEqual(self.snapshot().commit_id, old.commit_id)
        self.assertEqual(self.repository.discover_labels(apply=True), {
            "unknown_labels": ["new style"], "pending_added": 1, "applied": True,
        })
        self.assertEqual(self.repository.discover_labels(apply=True)["pending_added"], 0)
        self.assertEqual(len(old.rows("genre_label_mappings")), 7)
        mappings = {row["normalized_label"]: row for row in self.snapshot().rows("genre_label_mappings")}
        self.assertEqual(mappings["new style"]["status"], "pending")
        self.assertIsNone(mappings["new style"]["genre_id"])
        self.assertEqual(mappings["noise-label"]["status"], "excluded")
        self.assertNotIn("orphan label", mappings)

    def test_import_promotes_pending_preserves_created_at_and_excluded_decisions(self):
        self.populate_listens()
        self.fixture.datasets["genre_label_mappings"] = [
            self.mapping("noise-label"), self.mapping("new style", "pending"),
        ]
        self.fixture.snapshot()
        expanded = seed()
        expanded["genres"].append({
            "id": "new-style", "name": "New Style", "parents": ["pop"],
            "labels": ["new style", "noise-label"],
        })
        self.repository.import_seed(expanded)
        mappings = {row["normalized_label"]: row for row in self.snapshot().rows("genre_label_mappings")}
        self.assertEqual(mappings["new style"]["status"], "approved")
        self.assertEqual(mappings["new style"]["genre_id"], "new-style")
        self.assertEqual(mappings["new style"]["created_at"], DATE)
        self.assertEqual(mappings["noise-label"]["status"], "excluded")
        result = SummaryQueryService(self.snapshot()).top_listened_genres(grouping="families")
        self.assertEqual(result["classified_listens"], 4)
        self.assertIn({"genre": "Pop", "weighted_listens": 4.0, "share_percent": 100.0}, result["genres"])

    def test_import_preserves_manual_names_approved_mapping_and_unmentioned_edges(self):
        original = seed()
        self.fixture.datasets["genres"] = [{
            "id": row["id"], "name": "Manual Rock" if row["id"] == "rock" else row["name"],
            "introduced_by": "manual", "created_at": DATE,
        } for row in original["genres"]]
        self.fixture.datasets["genre_relations"] = [{
            "parent_id": "pop", "child_id": "grunge", "introduced_by": "manual",
        }]
        self.fixture.datasets["genre_label_mappings"] = [self.mapping("rock", "approved", "pop")]
        self.fixture.snapshot()
        self.repository.import_seed(original)
        current = self.snapshot()
        genres = {row["id"]: row for row in current.rows("genres")}
        self.assertEqual(genres["rock"]["name"], "Manual Rock")
        self.assertEqual(genres["rock"]["created_at"], DATE)
        mappings = {row["normalized_label"]: row for row in current.rows("genre_label_mappings")}
        self.assertEqual(mappings["rock"]["genre_id"], "pop")
        self.assertEqual(mappings["rock"]["introduced_by"], "manual")
        self.assertIn({"parent_id": "pop", "child_id": "grunge", "introduced_by": "manual"},
                      current.rows("genre_relations"))

    def test_cross_import_cycle_rejected_atomically(self):
        self.repository.import_seed(seed())
        before = self.store.head()
        cyclic_union = seed()
        cyclic_union["genres"] = [
            {"id": "rock", "name": "Rock", "parents": ["grunge"], "labels": []},
            {"id": "grunge", "name": "Grunge", "parents": [], "labels": []},
        ]
        with self.assertRaisesRegex(ValidationError, "cycle"):
            self.repository.import_seed(cyclic_union)
        self.assertEqual(self.store.head(), before)
        self.assertEqual(self.store.publications, 1)

    def test_invalid_seed_and_configuration_fail_without_publication(self):
        invalid = seed()
        invalid["genres"][0]["parents"] = ["missing"]
        with self.assertRaises(ValueError):
            self.repository.import_seed(invalid)
        for attempts in (0, True, 1.5):
            with self.assertRaises(ValidationError):
                GenreTaxonomyRepository(self.store, ACCOUNT, attempts=attempts)
        with self.assertRaises(ValidationError):
            self.repository.discover_labels(apply="yes")
        self.assertEqual(self.store.publications, 0)

    def test_import_retries_from_new_head_without_losing_other_account(self):
        publish = self.store.publish
        competing = GenreTaxonomyRepository(self.store, "other-account")
        first = True

        def race(expected, changes, message):
            nonlocal first
            if first:
                first = False
                with patch.object(self.store, "publish", side_effect=publish):
                    competing.import_seed(seed())
            return publish(expected, changes, message)

        with patch.object(self.store, "publish", side_effect=race):
            self.repository.import_seed(seed())
        self.assertEqual(len(self.snapshot().rows("genres")), 5)
        self.assertEqual(len(Snapshot.load(self.store, "other-account").rows("genres")), 5)
        self.assertEqual(self.store.publications, 2)

    def test_discovery_retries_and_respects_concurrent_approval(self):
        self.populate_listens()
        self.repository.import_seed(seed())
        publish = self.store.publish
        first = True

        def race(expected, changes, message):
            nonlocal first
            if first:
                first = False
                expanded = seed()
                expanded["genres"][0]["labels"].append("new style")
                with patch.object(self.store, "publish", side_effect=publish):
                    self.repository.import_seed(expanded)
            return publish(expected, changes, message)

        with patch.object(self.store, "publish", side_effect=race):
            result = self.repository.discover_labels(apply=True)
        self.assertEqual(result["unknown_labels"], ["noise-label"])
        self.assertEqual(result["pending_added"], 1)
        mappings = {row["normalized_label"]: row for row in self.snapshot().rows("genre_label_mappings")}
        self.assertEqual(mappings["new style"]["status"], "approved")

    def test_conflict_retry_exhaustion_is_explicit(self):
        with patch.object(self.store, "publish", side_effect=PublicationConflict("race")) as publish:
            with self.assertRaisesRegex(PublicationConflict, "exhausted"):
                self.repository.import_seed(seed())
            self.assertEqual(publish.call_count, 3)
        self.assertEqual(self.store.publications, 0)

    def test_discovery_conflict_exhaustion_does_not_publish_partial_rows(self):
        self.populate_listens()
        before = self.store.head()
        with patch.object(self.store, "publish", side_effect=PublicationConflict("race")) as publish:
            with self.assertRaisesRegex(PublicationConflict, "exhausted"):
                self.repository.discover_labels(apply=True)
            self.assertEqual(publish.call_count, 3)
        self.assertEqual(self.store.head(), before)
        self.assertEqual(self.snapshot().rows("genre_label_mappings"), [])

    def test_storage_errors_are_not_retried_or_hidden(self):
        with patch.object(self.store, "publish", side_effect=StorageError("uncertain")) as publish:
            with self.assertRaisesRegex(StorageError, "uncertain"):
                self.repository.import_seed(seed())
            self.assertEqual(publish.call_count, 1)

    def test_families_require_new_taxonomy_but_old_snapshots_remain_readable(self):
        for version in (1, 2, 3, 4, 5):
            with self.subTest(version=version):
                self.fixture.snapshot()
                path = f"accounts/{ACCOUNT}/manifest.json"
                files = self.store.commits[self.store.head()]
                manifest = parse_json(files[path].decode())
                manifest["schema_version"] = version
                manifest["datasets"] = {key: value for key, value in manifest["datasets"].items()
                                        if key in SNAPSHOT_SCHEMAS[version]}
                # Empty receipt headers must match the historical manifest schema.
                schema = SNAPSHOT_SCHEMAS[version]["receipts"]
                entry = manifest["datasets"]["receipts"]
                relative = entry["partitions"][0]["path"]
                content = encode_csv(schema, [])
                files[f"accounts/{ACCOUNT}/{relative}"] = content
                entry["schema_version"] = schema.version
                entry["partitions"] = [descriptor(relative, content, schema)]
                files[path] = canonical_json(manifest).encode()
                old = self.snapshot()
                self.assertEqual(old.rows("genres"), [])
                self.assertEqual(SummaryQueryService(old).library_counts()["total_tracks"], 0)
                if version < 4:
                    with self.assertRaisesRegex(ValidationError, "no CSV taxonomy"):
                        SummaryQueryService(old).top_listened_genres(grouping="families")
                else:
                    self.assertEqual(SummaryQueryService(old).top_listened_genres(grouping="families")["genres"], [])
                self.repository.import_seed(seed())
                self.assertEqual(self.snapshot().manifest["schema_version"], 6)
                self.assertEqual(old.manifest["schema_version"], version)

    def test_taxonomy_validation_rejects_bad_targets_status_duplicates_and_cycles(self):
        self.repository.import_seed(seed())
        base = {key: self.snapshot().rows(key) for key in ("genres", "genre_relations", "genre_label_mappings")}
        for defect in (
            "cycle", "unknown", "bad-status", "pending-target", "approved-target",
            "duplicate", "unnormalized", "timestamp",
        ):
            rows = copy.deepcopy(base)
            if defect in ("cycle", "unknown"):
                rows["genre_relations"].append({
                    "parent_id": "grunge" if defect == "cycle" else "missing",
                    "child_id": "rock", "introduced_by": "manual",
                })
            elif defect == "bad-status":
                rows["genre_label_mappings"][0]["status"] = "invalid"
            elif defect == "pending-target":
                rows["genre_label_mappings"][0]["status"] = "pending"
            elif defect == "approved-target":
                rows["genre_label_mappings"][0]["genre_id"] = "missing"
            elif defect == "duplicate":
                rows["genre_label_mappings"].append(rows["genre_label_mappings"][0])
            elif defect == "unnormalized":
                rows["genre_label_mappings"][0]["normalized_label"] = " Rock "
            else:
                rows["genres"][0]["created_at"] = "bad"
            with self.subTest(defect=defect), self.assertRaises(ValidationError):
                validate_taxonomy(rows)

    def test_family_query_validates_graph_instead_of_returning_partial_classification(self):
        self.populate_listens()
        self.fixture.datasets["genres"] = [{
            "id": "rock", "name": "Rock", "introduced_by": "manual", "created_at": DATE,
        }]
        self.fixture.datasets["genre_relations"] = [{
            "parent_id": "missing", "child_id": "rock", "introduced_by": "manual",
        }]
        self.fixture.snapshot()
        with self.assertRaisesRegex(ValidationError, "Unknown genre"):
            SummaryQueryService(self.snapshot()).top_listened_genres(grouping="families")

    def test_family_ids_are_not_merged_when_root_names_are_equal(self):
        self.populate_listens()
        original = seed()
        original["genres"][1]["name"] = "Rock"
        self.repository.import_seed(original)
        rows = SummaryQueryService(self.snapshot()).top_listened_genres(grouping="families")["genres"]
        self.assertEqual(rows, [
            {"genre": "Rock", "weighted_listens": 3.0, "share_percent": 100.0},
            {"genre": "Rock", "weighted_listens": 3.0, "share_percent": 100.0},
        ])

    def test_unrelated_mapping_sources_and_fields_are_not_used_as_families(self):
        self.populate_listens()
        self.fixture.datasets["genres"] = [{
            "id": "rock", "name": "Rock", "introduced_by": "manual", "created_at": DATE,
        }]
        wrong_source = self.mapping("new style", "approved", "rock")
        wrong_source["source"] = "other-provider"
        wrong_field = self.mapping("noise-label", "approved", "rock")
        wrong_field["field"] = "other-field"
        self.fixture.datasets["genre_label_mappings"] = [wrong_source, wrong_field]
        self.fixture.snapshot()
        result = SummaryQueryService(self.snapshot()).top_listened_genres(grouping="families")
        self.assertEqual(result["classified_listens"], 0)
        self.assertEqual(result["genres"], [])

    def test_family_history_remains_pinned_after_taxonomy_extension(self):
        self.populate_listens()
        self.repository.import_seed(seed())
        before = self.snapshot()
        initial = SummaryQueryService(before).top_listened_genres(grouping="families")
        expanded = seed()
        expanded["genres"][2]["labels"].append("new style")
        self.repository.import_seed(expanded)
        self.assertEqual(SummaryQueryService(before).top_listened_genres(grouping="families"), initial)
        self.assertEqual(
            SummaryQueryService(self.snapshot()).top_listened_genres(grouping="families")["classified_listens"], 4,
        )

    def test_seed_archive_corruption_is_reported_not_overwritten(self):
        self.repository.import_seed(seed())
        path = self.store.list_paths(self.store.head(), f"accounts/{ACCOUNT}/taxonomy/imports")[0]
        self.store.commits[self.store.head()][path] = b"corrupt"
        with self.assertRaisesRegex(ValidationError, "checksum"):
            self.repository.import_seed(seed())

    @unittest.skipUnless(shutil.which("git"), "Git required for synthetic integration")
    def test_local_git_consolidation_preserves_taxonomy_provider_data_and_pending(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LocalGitStore.initialize(Path(directory) / "data")
            repository = GenreTaxonomyRepository(store, ACCOUNT)
            listening_capture = batch(item(), account=ACCOUNT)
            store.publish_batch(listening_capture)
            pending = Snapshot.load(store, ACCOUNT)
            repository.import_seed(seed())
            imported = Snapshot.load(store, ACCOUNT)
            self.assertEqual(imported.pending_batches(), 1)
            self.assertEqual(imported.rows("listening_events"), [])
            metadata = smarter(capture(
                entity="artist", entity_id=ARTIST, genres=["folk rock", "grunge"],
            ), live=False, account=ACCOUNT)
            store.publish_batch(metadata)
            consolidate(store, ACCOUNT)
            result = Snapshot.load(store, ACCOUNT)
            self.assertEqual(result.manifest["schema_version"], 6)
            self.assertEqual(len(result.rows("listening_events")), 1)
            self.assertEqual(result.rows("genres"), imported.rows("genres"))
            self.assertEqual(result.rows("genre_label_mappings"), imported.rows("genre_label_mappings"))
            self.assertEqual(result.pending_batches(), 0)
            self.assertEqual(pending.pending_batches(), 1)
            self.assertEqual(len(SummaryQueryService(result).recent_listening_events()), 1)
            self.assertEqual(SummaryQueryService(result).top_listened_genres(grouping="families")["genres"], [
                {"genre": "Folk", "weighted_listens": 1.0, "share_percent": 100.0},
                {"genre": "Rock", "weighted_listens": 1.0, "share_percent": 100.0},
            ])
            self.assertEqual(len(result.rows("smarterplaylists_artist_metadata")), 1)
