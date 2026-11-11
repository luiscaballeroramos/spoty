import copy
import unittest
from unittest.mock import patch

from spoty.domain.listening import ValidationError, canonical_json, parse_json
from spoty.services.consolidation import consolidate
from spoty.services.coverage import coverage_projection, extend_catalog_projection, metadata_overview
from spoty.services.summary_queries import SummaryQueryService
from spoty.storage.contracts import PublicationConflict, publish_batch
from spoty.storage.csv_codec import SCHEMAS
from spoty.storage.snapshots import Snapshot, dataset_partitions, descriptor, snapshot_changes
from tests import test_csv_summary_queries as fixtures
from tests.test_csv_genre_taxonomy import WritableMemoryStore
from tests.test_csv_git_prototype import batch, item
from tests.test_csv_metadata_prototype import ARTIST, TRACK, capture, smarter


ACCOUNT = "test-account"


class CoverageProjectionTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.CsvSummaryQueriesTest()
        self.fixture.setUp()
        self.store = WritableMemoryStore()
        self.fixture.store = self.store

    def snapshot(self):
        return Snapshot.load(self.store, ACCOUNT)

    def initial_capture(self):
        publish_batch(self.store, batch(item(), account=ACCOUNT))
        consolidate(self.store, ACCOUNT)
        publish_batch(self.store, smarter(
            capture(energy=0, explicit=False), capture(entity="artist", entity_id=ARTIST, genres=["rock"]),
            live=False, account=ACCOUNT,
        ))
        consolidate(self.store, ACCOUNT)
        return self.snapshot()

    def test_current_projection_matches_v4_fallback_exactly_for_every_entity(self):
        self.fixture.seed()
        self.fixture.datasets["smarterplaylists_track_metadata"] = [
            self.fixture.metadata("track", "track-a", {
                "zero": 0, "false": False, "empty": [], "blank": " ", "null": None,
                "nested": {"empty": None},
            }),
            self.fixture.metadata("track", "orphan", {"name": "Ignored"}),
        ]
        self.fixture.datasets["lastfm_track_metadata"] = [{
            "spotify_track_id": "track-a", "match_status": "matched",
            "normalized_data": {"name": "Name"}, "raw_response": {}, "fetched_at": "date",
        }]
        self.fixture.snapshot()
        current = self.snapshot()
        expected = {kind: SummaryQueryService(current).metadata_overview(kind)
                    for kind in ("tracks", "artists", "albums")}
        files = self.store.commits[current.commit_id]
        path = f"accounts/{ACCOUNT}/manifest.json"
        manifest = parse_json(files[path].decode())
        manifest["schema_version"] = 4
        del manifest["datasets"]["metadata_coverage"]
        self.store.publish(current.commit_id, {path: canonical_json(manifest).encode()}, "Synthetic v4")
        for kind in expected:
            self.assertEqual(SummaryQueryService(self.snapshot()).metadata_overview(kind), expected[kind])
        self.assertEqual(current.manifest["schema_version"], 6)

    def test_current_coverage_never_reads_raw_provider_partitions(self):
        current = self.initial_capture()
        original_read = self.store.read

        def guarded(commit, path):
            if "_metadata/" in path:
                raise AssertionError("Raw provider dataset read for coverage")
            return original_read(commit, path)

        with patch.object(self.store, "read", side_effect=guarded):
            result = SummaryQueryService(current).metadata_overview("tracks")
        self.assertEqual(result["enriched"], 1)
        self.assertEqual(result["sources"][1]["fields"], [
            {"field": field, "available": 1} for field in ("energy", "explicit", "id", "name", "uri")
        ])

    def test_listening_only_reuses_all_other_descriptors_and_never_reads_raw(self):
        before = self.initial_capture()
        original_files = copy.deepcopy(self.store.commits[before.commit_id])
        publish_batch(self.store, batch(item(played_at="2026-11-06T11:00:00Z"), account=ACCOUNT))
        original_read = self.store.read

        def guarded(commit, path):
            if "_metadata/" in path or "/metadata_coverage/" in path or "/track_audio/" in path:
                raise AssertionError("Untouched heavy/projection dataset read during listening-only consolidation")
            return original_read(commit, path)

        with patch.object(self.store, "read", side_effect=guarded):
            result = consolidate(self.store, ACCOUNT)
        after = self.snapshot()
        self.assertEqual(result.saved_events, 1)
        for dataset, entry in before.manifest["datasets"].items():
            if dataset not in ("listening_events", "receipts"):
                self.assertEqual(after.manifest["datasets"][dataset], entry)
                for part in entry["partitions"]:
                    path = f"accounts/{ACCOUNT}/{part['path']}"
                    self.assertEqual(self.store.commits[after.commit_id][path], original_files[path])
        self.assertEqual(len(before.rows("listening_events")), 1)
        self.assertEqual(len(after.rows("listening_events")), 2)
        self.assertEqual(SummaryQueryService(before).metadata_overview("tracks"),
                         SummaryQueryService(after).metadata_overview("tracks"))

    def test_new_catalog_entity_includes_existing_orphan_metadata_without_raw_reads(self):
        before = self.initial_capture()
        orphan_id = "Z" * 22
        publish_batch(self.store, smarter(capture(entity_id=orphan_id, energy=0.25), account=ACCOUNT))
        consolidate(self.store, ACCOUNT)
        self.assertEqual(SummaryQueryService(self.snapshot()).metadata_overview("tracks")["enriched"], 1)
        publish_batch(self.store, batch(item(track_id=orphan_id), account=ACCOUNT))
        original_read = self.store.read

        def guarded(commit, path):
            if "_metadata/" in path:
                raise AssertionError("Raw metadata read while adding catalog")
            return original_read(commit, path)

        with patch.object(self.store, "read", side_effect=guarded):
            consolidate(self.store, ACCOUNT)
        after = self.snapshot()
        self.assertEqual(SummaryQueryService(after).metadata_overview("tracks")["enriched"], 2)
        self.assertEqual(SummaryQueryService(before).metadata_overview("tracks")["enriched"], 1)
        audio = next(row for row in after.rows("track_audio") if row["spotify_track_id"] == orphan_id)
        self.assertEqual(audio["name"], item()["track"]["name"])
        self.assertEqual(audio["audio"]["energy"], 0.25)

    def test_metadata_update_rebuilds_projection_and_preserves_old_snapshot(self):
        before = self.initial_capture()
        publish_batch(self.store, smarter(capture(when="2026-11-07T10:00:00Z", tempo=120), account=ACCOUNT))
        consolidate(self.store, ACCOUNT)
        after = self.snapshot()
        fields = SummaryQueryService(after).metadata_overview("tracks")["sources"][1]["fields"]
        self.assertIn({"field": "tempo", "available": 1}, fields)
        self.assertNotIn({"field": "tempo", "available": 1},
                         SummaryQueryService(before).metadata_overview("tracks")["sources"][1]["fields"])

    def test_v5_upgrade_rebuilds_orphan_projection_before_incremental_catalog(self):
        self.initial_capture()
        orphan_id = "Z" * 22
        publish_batch(self.store, smarter(capture(entity_id=orphan_id, energy=0.25), account=ACCOUNT))
        consolidate(self.store, ACCOUNT)
        current = self.snapshot()
        rows = {dataset: current.rows(dataset) for dataset in SCHEMAS}
        rows["metadata_coverage"] = coverage_projection(rows)
        changes = snapshot_changes(current, rows, snapshot_id="b" * 32, published_at="2026-11-09T10:00:00Z")
        # A true v5 projection omitted uncataloged provider records.
        entry = []
        for name, content in dataset_partitions("metadata_coverage", rows["metadata_coverage"]).items():
            path = f"datasets/metadata_coverage/{name}"
            changes[f"accounts/{ACCOUNT}/{path}"] = content
            entry.append(descriptor(path, content, SCHEMAS["metadata_coverage"]))
        manifest_path = f"accounts/{ACCOUNT}/manifest.json"
        manifest = parse_json(changes[manifest_path].decode())
        manifest["schema_version"] = 5
        manifest["datasets"]["metadata_coverage"]["partitions"] = entry
        changes[manifest_path] = canonical_json(manifest).encode()
        self.store.publish(current.commit_id, changes, "Synthetic true v5")
        old = self.snapshot()
        self.assertEqual(SummaryQueryService(old).metadata_overview("tracks")["enriched"], 1)
        publish_batch(self.store, batch(item(track_id=orphan_id), account=ACCOUNT))
        consolidate(self.store, ACCOUNT)
        self.assertEqual(self.snapshot().manifest["schema_version"], 6)
        self.assertEqual(SummaryQueryService(self.snapshot()).metadata_overview("tracks")["enriched"], 2)

    def test_new_artist_album_and_track_update_spotify_fields_without_reading_providers(self):
        before = self.initial_capture()
        rows = {dataset: before.rows(dataset) for dataset in SCHEMAS}
        for entity, entity_id in (("artist", "Y" * 22), ("album", "X" * 22)):
            rows[f"smarterplaylists_{entity}_metadata"].append(
                self.fixture.metadata(entity, entity_id, {"name": "Orphan"}),
            )
        self.store.publish(before.commit_id, snapshot_changes(
            before, rows, snapshot_id="c" * 32, published_at="2026-11-09T10:00:00Z",
        ), "Synthetic orphan artist and album")
        before = self.snapshot()
        new_item = item(track_id="Z" * 22)
        new_artist = {"id": "Y" * 22, "name": "New artist"}
        new_item["track"]["artists"] = [new_artist]
        new_item["track"]["album"].update(id="X" * 22, artists=[new_artist], name="New album")
        publish_batch(self.store, batch(new_item, account=ACCOUNT))
        original_read = self.store.read

        def guarded(commit, path):
            if "_metadata/" in path:
                raise AssertionError("Raw provider read")
            return original_read(commit, path)

        with patch.object(self.store, "read", side_effect=guarded):
            consolidate(self.store, ACCOUNT)
        after = self.snapshot()
        for kind in ("tracks", "artists", "albums"):
            result = SummaryQueryService(after).metadata_overview(kind)
            self.assertEqual(result["total"], 2)
            self.assertEqual(result["sources"][0]["available"], 2)
        for kind in ("artists", "albums"):
            self.assertEqual(SummaryQueryService(before).metadata_overview(kind)["sources"][1]["available"],
                             1 if kind == "artists" else 0)
            self.assertEqual(SummaryQueryService(after).metadata_overview(kind)["sources"][1]["available"],
                             2 if kind == "artists" else 1)
        self.assertEqual(SummaryQueryService(after).metadata_overview("tracks")["enriched"], 1)
        for dataset, entry in before.manifest["datasets"].items():
            if "_metadata" in dataset or dataset in ("genres", "liked_tracks"):
                self.assertEqual(entry, after.manifest["datasets"][dataset])

    def test_orphan_provider_coverage_counts_only_after_catalog_reference_exists(self):
        projection = [{
            "entity_type": "tracks", "entity_id": "orphan",
            "source": "Last.fm", "fields": ["name"],
        }]
        self.assertEqual(metadata_overview("tracks", set(), projection, include_orphans=True)["enriched"], 0)
        self.assertEqual(metadata_overview("tracks", {"orphan"}, projection, include_orphans=True)["enriched"], 1)
        with self.assertRaises(ValidationError):
            metadata_overview("tracks", set(), [{**projection[0], "source": "Spotify"}], include_orphans=True)

    def test_catalog_extension_rejects_spotify_projection_missing_from_original_catalog(self):
        catalog = {"tracks": [{"id": "new", "name": "Name"}], "artists": [], "albums": []}
        with self.assertRaisesRegex(ValidationError, "unknown catalog"):
            extend_catalog_projection(catalog, catalog, [{
                "entity_type": "tracks", "entity_id": "new", "source": "Spotify", "fields": ["name"],
            }])

    def test_new_track_with_empty_name_remains_invalid_and_preserves_orphan_audio(self):
        self.initial_capture()
        orphan_id = "Z" * 22
        publish_batch(self.store, smarter(capture(entity_id=orphan_id, energy=0.25), account=ACCOUNT))
        consolidate(self.store, ACCOUNT)
        before = self.snapshot()
        expected = next(row for row in before.rows("track_audio") if row["spotify_track_id"] == orphan_id)
        new_item = item(track_id=orphan_id)
        new_item["track"]["name"] = ""
        with self.assertRaisesRegex(ValidationError, "nonempty string"):
            batch(new_item, account=ACCOUNT)
        self.assertEqual(self.store.head(), before.commit_id)
        actual = next(row for row in self.snapshot().rows("track_audio") if row["spotify_track_id"] == orphan_id)
        self.assertEqual(actual, expected)

    def test_partial_catalog_publication_requires_all_projections_and_v6(self):
        before = self.initial_capture()
        incomplete = {dataset: before.rows(dataset) for dataset in
                      ("tracks", "artists", "albums", "metadata_coverage", "listening_events", "receipts")}
        with self.assertRaisesRegex(ValidationError, "complete catalog"):
            snapshot_changes(before, incomplete, snapshot_id="c" * 32,
                             published_at="2026-11-09T10:00:00Z", reuse_unchanged=True)
        legacy = Snapshot(before.store, before.account, before.commit_id, {**before.manifest, "schema_version": 5})
        with self.assertRaisesRegex(ValidationError, "v6"):
            snapshot_changes(legacy, {}, snapshot_id="c" * 32,
                             published_at="2026-11-09T10:00:00Z", reuse_unchanged=True)

    def test_corrupt_projection_blocks_catalog_addition_atomically(self):
        current = self.initial_capture()
        part = current.manifest["datasets"]["metadata_coverage"]["partitions"][0]
        self.store.publish(current.commit_id, {f"accounts/{ACCOUNT}/{part['path']}": b"corrupt"}, "Corrupt projection")
        publish_batch(self.store, batch(item(track_id="Z" * 22), account=ACCOUNT))
        head = self.store.head()
        with self.assertRaisesRegex(ValidationError, "checksum"):
            consolidate(self.store, ACCOUNT)
        self.assertEqual(self.store.head(), head)

    def test_all_orphan_provider_sources_match_full_rebuild_after_catalog_addition(self):
        self.initial_capture()
        current = self.snapshot()
        rows = {dataset: current.rows(dataset) for dataset in SCHEMAS}
        orphan_id = "Z" * 22
        rows["lastfm_track_metadata"] = [{
            "spotify_track_id": orphan_id, "match_status": "matched", "normalized_data": {"count": 0},
            "raw_response": {"synthetic": True}, "fetched_at": "2026-11-09T10:00:00Z",
        }]
        rows["musicbrainz_recording_metadata"] = [{
            "spotify_track_id": orphan_id, "match_status": "matched", "normalized_data": {"title": "Title"},
            "recording_mbid": "synthetic", "raw_response": {}, "fetched_at": "2026-11-09T10:00:00Z",
        }]
        rows["acousticbrainz_analysis_metadata"] = [{
            "spotify_track_id": orphan_id, "match_status": "found", "analysis_level": level,
            "recording_mbid": "synthetic", "normalized_data": {"value": False},
            "raw_response": {}, "fetched_at": "2026-11-09T10:00:00Z",
        } for level in ("low-level", "high-level")]
        self.store.publish(current.commit_id, snapshot_changes(
            current, rows, snapshot_id="b" * 32, published_at="2026-11-09T10:00:00Z",
        ), "Synthetic orphan provider results")
        before = self.snapshot()
        self.assertEqual(SummaryQueryService(before).metadata_overview("tracks")["enriched"], 1)
        publish_batch(self.store, batch(item(track_id=orphan_id), account=ACCOUNT))
        consolidate(self.store, ACCOUNT)
        after = self.snapshot()
        observed = SummaryQueryService(after).metadata_overview("tracks")
        full_rows = {dataset: after.rows(dataset) for dataset in SCHEMAS}
        oracle = metadata_overview(
            "tracks", {row["id"] for row in full_rows["tracks"]},
            coverage_projection(full_rows, include_orphans=True), include_orphans=True,
        )
        self.assertEqual(observed, oracle)
        self.assertEqual(observed["enriched"], 2)
        self.assertTrue(all(source["available"] == 1 for source in observed["sources"][2:]))
        for dataset in ("lastfm_track_metadata", "musicbrainz_recording_metadata", "acousticbrainz_analysis_metadata"):
            self.assertEqual(before.manifest["datasets"][dataset], after.manifest["datasets"][dataset])

    def test_corrupted_coverage_fails_without_reading_raw_or_returning_empty(self):
        current = self.initial_capture()
        part = current.manifest["datasets"]["metadata_coverage"]["partitions"][0]
        path = f"accounts/{ACCOUNT}/{part['path']}"
        self.store.commits[current.commit_id][path] += b"corrupt"
        with self.assertRaisesRegex(ValidationError, "checksum"):
            SummaryQueryService(current).metadata_overview("tracks")

    def test_semantically_invalid_projection_is_rejected(self):
        valid = {"entity_type": "tracks", "entity_id": TRACK, "source": "Spotify", "fields": ["name"]}
        for change in (
            {"source": "Invalid"}, {"entity_type": "Invalid"}, {"fields": None},
            {"fields": ["x", "x"]}, {"fields": [1]}, {"fields": []}, {"entity_id": "orphan"},
        ):
            with self.subTest(change=change), self.assertRaises(ValidationError):
                metadata_overview("tracks", {TRACK}, [{**valid, **change}])
        with self.assertRaises(ValidationError):
            metadata_overview("tracks", {TRACK}, [valid, valid])

    def test_invalid_reused_descriptor_blocks_publication_without_reading_raw(self):
        self.initial_capture()
        current = self.snapshot()
        manifest = copy.deepcopy(current.manifest)
        manifest["datasets"]["smarterplaylists_track_metadata"]["partitions"][0]["sha256"] = "bad"
        self.store.publish(current.commit_id, {
            f"accounts/{ACCOUNT}/manifest.json": canonical_json(manifest).encode(),
        }, "Synthetic invalid descriptor")
        publish_batch(self.store, batch(item(played_at="2026-11-06T11:00:00Z"), account=ACCOUNT))
        head = self.store.head()
        with self.assertRaisesRegex(ValidationError, "integrity descriptor"):
            consolidate(self.store, ACCOUNT)
        self.assertEqual(self.store.head(), head)

    def test_reused_raw_corruption_is_detected_on_read_not_by_unrelated_consolidation(self):
        current = self.initial_capture()
        part = current.manifest["datasets"]["smarterplaylists_track_metadata"]["partitions"][0]
        path = f"accounts/{ACCOUNT}/{part['path']}"
        self.store.publish(current.commit_id, {path: b"bad"}, "Synthetic raw corruption")
        publish_batch(self.store, batch(item(played_at="2026-11-06T11:00:00Z"), account=ACCOUNT))
        consolidate(self.store, ACCOUNT)
        after = self.snapshot()
        self.assertEqual(SummaryQueryService(after).metadata_overview("tracks")["enriched"], 1)
        with self.assertRaisesRegex(ValidationError, "checksum"):
            after.rows("smarterplaylists_track_metadata")

    def test_legacy_v4_publication_rebuilds_projection_once(self):
        current = self.initial_capture()
        manifest = copy.deepcopy(current.manifest)
        manifest["schema_version"] = 4
        del manifest["datasets"]["metadata_coverage"]
        self.store.publish(current.commit_id, {
            f"accounts/{ACCOUNT}/manifest.json": canonical_json(manifest).encode(),
        }, "Synthetic v4")
        old = self.snapshot()
        expected = SummaryQueryService(old).metadata_overview("tracks")
        publish_batch(self.store, batch(item(played_at="2026-11-06T11:00:00Z"), account=ACCOUNT))
        consolidate(self.store, ACCOUNT)
        self.assertEqual(self.snapshot().manifest["schema_version"], 6)
        self.assertEqual(SummaryQueryService(self.snapshot()).metadata_overview("tracks"), expected)
        self.assertEqual(old.manifest["schema_version"], 4)

    def test_partial_serializer_rejects_catalog_changes_or_legacy_snapshot(self):
        current = self.initial_capture()
        with self.assertRaisesRegex(ValidationError, "Partial publication"):
            snapshot_changes(current, {"tracks": []}, snapshot_id="a" * 32,
                             published_at="2026-11-09T10:00:00Z", reuse_unchanged=True)

    def test_explicit_smaller_partition_limit_still_repartitions_full_snapshot(self):
        self.initial_capture()
        publish_batch(self.store, batch(item(played_at="2026-11-06T11:00:00Z"), account=ACCOUNT))
        consolidate(self.store, ACCOUNT, max_bytes=2000)
        for dataset in self.snapshot().manifest["datasets"].values():
            self.assertTrue(all(part["byte_size"] <= 2000 for part in dataset["partitions"]))

    def test_optimized_conflict_retry_keeps_competing_capture_in_inbox(self):
        self.initial_capture()
        publish_batch(self.store, batch(item(played_at="2026-11-06T11:00:00Z"), account=ACCOUNT))
        original = self.store.publish
        first = True

        def race(expected, changes, message):
            nonlocal first
            if first:
                first = False
                with patch.object(self.store, "publish", side_effect=original):
                    publish_batch(self.store, batch(item(played_at="2026-11-06T12:00:00Z"), account=ACCOUNT))
                raise PublicationConflict("Concurrent synthetic capture")
            return original(expected, changes, message)

        with patch.object(self.store, "publish", side_effect=race):
            consolidate(self.store, ACCOUNT)
        self.assertEqual(len(self.snapshot().rows("listening_events")), 3)
        self.assertEqual(self.snapshot().pending_batches(), 0)
