import copy
import unittest
from datetime import datetime, timezone
from unittest.mock import patch
from uuid import UUID

from spoty.domain.listening import ValidationError, canonical_json
from spoty.domain.metadata import MetadataBatch
from spoty.services.consolidation import consolidate
from spoty.services.coverage import coverage_projection, metadata_overview
from spoty.services.metadata import apply_metadata
from spoty.services.summary_queries import SummaryQueryService
from spoty.services.favorites import FavoriteRevisionBlocked
from spoty.storage.contracts import PublicationConflict, batch_path, publish_batch
from spoty.storage.csv_codec import MAX_CSV_BYTES, SCHEMAS, encode_batch, encode_csv
from spoty.domain.favorites import FavoritesBatch
from spoty.storage.snapshots import Snapshot, descriptor, snapshot_changes
from spoty.storage.staging import DiskRows
from tests.test_csv_genre_taxonomy import WritableMemoryStore
from tests.test_csv_git_prototype import batch, item
from tests.test_csv_metadata_prototype import ALBUM, ARTIST, MBID, TRACK, capture, smarter


ACCOUNT = "test-account"
DATE = "2026-11-09T12:00:00Z"


class MetadataIncrementalTest(unittest.TestCase):
    def setUp(self):
        self.store = WritableMemoryStore()
        publish_batch(self.store, batch(item(), account=ACCOUNT))
        consolidate(self.store, ACCOUNT)
        publish_batch(self.store, smarter(
            capture(energy=0.5), capture(entity="artist", entity_id=ARTIST, genres=["rock"]),
            capture(entity="album", entity_id=ALBUM, label="Original"),
            live=False, account=ACCOUNT,
        ))
        consolidate(self.store, ACCOUNT)
        self.before = self.snapshot()

    def snapshot(self):
        return Snapshot.load(self.store, ACCOUNT)

    def provider_batch(self, provider="lastfm", *, track_id=TRACK, matched=True, level="low-level"):
        record = {
            "provider": provider, "spotify_track_id": track_id,
            "match_status": "matched" if matched else "not_found",
            "normalized_data": {"name": "Synthetic", "count": 0} if matched else None,
            "raw_response": {"synthetic": True},
        }
        if provider == "musicbrainz":
            record["recording_mbid"] = MBID if matched else None
            record["normalized_data"] = {"recording_mbid": MBID, "title": "Synthetic"} if matched else None
        if provider == "acousticbrainz":
            record.update(
                recording_mbid=MBID, analysis_level=level, match_status="found" if matched else "not_found",
                normalized_data={("lowlevel" if level == "low-level" else "highlevel"): {"value": False}}
                if matched else None,
            )
        return MetadataBatch.from_enrichment_records(ACCOUNT, "synthetic", DATE, [record])

    def assert_reused(self, before, after, changed):
        for dataset, entry in before.manifest["datasets"].items():
            if dataset not in changed:
                self.assertEqual(entry, after.manifest["datasets"][dataset], dataset)
                for part in entry["partitions"]:
                    path = f"accounts/{ACCOUNT}/{part['path']}"
                    self.assertEqual(self.store.read(before.commit_id, path), self.store.read(after.commit_id, path))

    def assert_coverage_matches_full(self, snapshot):
        rows = {dataset: snapshot.rows(dataset) for dataset in SCHEMAS}
        projection = coverage_projection(rows, include_orphans=True)
        for kind in ("tracks", "artists", "albums"):
            self.assertEqual(SummaryQueryService(snapshot).metadata_overview(kind), metadata_overview(
                kind, {row["id"] for row in rows[kind]}, projection, include_orphans=True,
            ))

    def test_each_provider_reads_only_its_dataset_and_reuses_all_others(self):
        for provider, dataset in (
            ("lastfm", "lastfm_track_metadata"), ("musicbrainz", "musicbrainz_recording_metadata"),
            ("acousticbrainz", "acousticbrainz_analysis_metadata"),
        ):
            with self.subTest(provider=provider):
                before = self.snapshot()
                publish_batch(self.store, self.provider_batch(provider))
                read = self.store.read

                def guarded(commit, path):
                    if ("/datasets/" in path and path.split("/")[3] not in {
                            dataset, "tracks", "artists", "albums", "metadata_coverage", "receipts",
                            "favorites_replica_state"}):
                        raise AssertionError(f"Unrelated dataset read: {path}")
                    return read(commit, path)

                with patch.object(self.store, "read", side_effect=guarded):
                    result = consolidate(self.store, ACCOUNT)
                after = self.snapshot()
                self.assertEqual(result.saved_metadata, 1)
                self.assertEqual(after.pending_batches(), 0)
                self.assert_reused(before, after, {dataset, "metadata_coverage", "receipts"})
                self.assert_coverage_matches_full(after)
        self.assertEqual(SummaryQueryService(self.before).metadata_overview("tracks")["enriched"], 1)

    def test_smarter_artist_album_and_track_only_change_their_projections(self):
        for entity, entity_id, fields in (
            ("artist", ARTIST, {"genres": ["folk", "rock"]}),
            ("album", ALBUM, {"label": "Updated"}),
            ("track", TRACK, {"energy": 0, "tempo": 120}),
        ):
            with self.subTest(entity=entity):
                before = self.snapshot()
                update = smarter(capture(when=DATE, entity=entity, entity_id=entity_id, **fields),
                                 account=ACCOUNT, live=False)
                publish_batch(self.store, update)
                dataset = f"smarterplaylists_{entity}_metadata"
                read = self.store.read

                def guarded(commit, path):
                    if "_metadata/" in path and f"/{dataset}/" not in path:
                        raise AssertionError(f"Unrelated provider read: {path}")
                    return read(commit, path)

                with patch.object(self.store, "read", side_effect=guarded):
                    result = consolidate(self.store, ACCOUNT)
                after = self.snapshot()
                changed = {dataset, "smarterplaylists_import_runs", "metadata_coverage", "receipts"}
                if entity == "track":
                    changed.add("track_audio")
                    self.assertEqual(after.rows("track_audio")[0]["audio"], {"energy": 0, "tempo": 120})
                self.assertEqual(result.saved_metadata, 1)
                self.assert_reused(before, after, changed)
                self.assert_coverage_matches_full(after)

    def test_partial_and_full_publication_have_identical_rows_for_mixed_metadata_batch(self):
        original = self.provider_batch("acousticbrainz", level="high-level")
        captures = smarter(
            capture(when=DATE, tempo=120),
            capture(when=DATE, entity="artist", entity_id=ARTIST, genres=["folk"]),
            account=ACCOUNT, live=False,
        )
        update = MetadataBatch.from_records(ACCOUNT, "mixed", DATE, [*original.payloads, *captures.payloads])
        publish_batch(self.store, update)
        full_store = copy.deepcopy(self.store)
        with patch("spoty.services.consolidation.uuid4", return_value=UUID(hex="c" * 32)), \
                patch("spoty.services.consolidation.datetime") as clock:
            clock.now.return_value = datetime(2026, 11, 9, 12, tzinfo=timezone.utc)
            partial = consolidate(self.store, ACCOUNT)
            full = consolidate(full_store, ACCOUNT, max_bytes=MAX_CSV_BYTES - 1)
        self.assertEqual(partial.saved_metadata, full.saved_metadata)
        optimized, rebuilt = self.snapshot(), Snapshot.load(full_store, ACCOUNT)
        for dataset in SCHEMAS:
            self.assertEqual(optimized.rows(dataset), rebuilt.rows(dataset), dataset)

    def test_rejected_batch_cannot_mutate_original_rows_or_other_provider_writes(self):
        rows = {dataset: self.before.rows(dataset) for dataset in SCHEMAS}
        maps = {dataset: {tuple(row[key] for key in SCHEMAS[dataset].keys): row for row in entries}
                for dataset, entries in rows.items()}
        original = copy.deepcopy(maps)
        earlier = smarter(capture(when="2026-11-05T10:00:00Z", tempo=100), account=ACCOUNT)
        conflict = smarter(capture(energy=0.8), account=ACCOUNT)
        update = MetadataBatch.from_records(ACCOUNT, "mixed", DATE, [
            *self.provider_batch().payloads, *earlier.payloads, *conflict.payloads,
        ])
        with self.assertRaisesRegex(ValidationError, "Conflicting metadata"):
            apply_metadata(update, maps, DATE)
        self.assertEqual(maps, original)
        accepted = smarter(capture(when=DATE, tempo=120), account=ACCOUNT)
        staged, saved, _ = apply_metadata(accepted, maps, DATE)
        self.assertEqual(saved, 1)
        self.assertEqual(staged["smarterplaylists_track_metadata"][(TRACK,)]["normalized_data"]["tempo"], 120)
        self.assertEqual(maps, original)
        publish_batch(self.store, update)
        result = consolidate(self.store, ACCOUNT)
        self.assertEqual(result.rejected_batches, 1)
        self.assertEqual(self.snapshot().rows("lastfm_track_metadata"), [])
        self.assertEqual(self.snapshot().rows("smarterplaylists_track_metadata"),
                         self.before.rows("smarterplaylists_track_metadata"))
        self.assertEqual(self.snapshot().rows("smarterplaylists_import_runs"),
                         self.before.rows("smarterplaylists_import_runs"))

    def test_statuses_levels_and_orphans_match_full_projection(self):
        for update in (
            self.provider_batch("lastfm", matched=False),
            self.provider_batch("musicbrainz", track_id="Z" * 22),
            self.provider_batch("acousticbrainz"),
            self.provider_batch("acousticbrainz", level="high-level", matched=False),
        ):
            publish_batch(self.store, update)
        consolidate(self.store, ACCOUNT)
        after = self.snapshot()
        self.assert_coverage_matches_full(after)
        overview = SummaryQueryService(after).metadata_overview("tracks")
        self.assertEqual([source["available"] for source in overview["sources"]], [1, 1, 0, 0, 1, 0])
        publish_batch(self.store, self.provider_batch("lastfm"))
        result = consolidate(self.store, ACCOUNT)
        self.assertEqual((result.saved_metadata, result.duplicate_metadata), (0, 1))
        self.assert_coverage_matches_full(self.snapshot())

    def test_corrupt_loaded_dataset_or_projection_blocks_publication(self):
        original = copy.deepcopy(self.store)
        for dataset in ("lastfm_track_metadata", "metadata_coverage"):
            with self.subTest(dataset=dataset):
                self.store = copy.deepcopy(original)
                part = self.before.manifest["datasets"][dataset]["partitions"][0]
                self.store.publish(self.before.commit_id, {f"accounts/{ACCOUNT}/{part['path']}": b"corrupt"}, "Corrupt")
                publish_batch(self.store, self.provider_batch())
                head = self.store.head()
                with self.assertRaisesRegex(ValidationError, "checksum"):
                    consolidate(self.store, ACCOUNT)
                self.assertEqual(self.store.head(), head)
                self.assertEqual(self.snapshot().pending_batches(), 1)

    def test_corrupt_unrelated_raw_is_reused_and_detected_when_read(self):
        part = self.before.manifest["datasets"]["smarterplaylists_track_metadata"]["partitions"][0]
        self.store.publish(self.before.commit_id, {f"accounts/{ACCOUNT}/{part['path']}": b"corrupt"}, "Corrupt")
        publish_batch(self.store, self.provider_batch())
        consolidate(self.store, ACCOUNT)
        self.assertEqual(SummaryQueryService(self.snapshot()).metadata_overview("tracks")["enriched"], 1)
        with self.assertRaisesRegex(ValidationError, "checksum"):
            self.snapshot().rows("smarterplaylists_track_metadata")

    def test_invalid_envelope_is_quarantined_without_reading_any_raw(self):
        update = self.provider_batch()
        published = publish_batch(self.store, update)
        self.store.publish(published.commit_id, {batch_path(update): b"bad"}, "Invalid synthetic envelope")
        read = self.store.read

        def guarded(commit, path):
            if "_metadata/" in path:
                raise AssertionError("Raw read for an invalid batch")
            return read(commit, path)

        with patch.object(self.store, "read", side_effect=guarded):
            result = consolidate(self.store, ACCOUNT)
        self.assertEqual(result.rejected_batches, 1)
        receipt = next(row for row in self.snapshot().rows("receipts") if row["batch_id"] == update.batch_id)
        self.assertEqual(receipt["error_code"], "invalid_metadata_batch")
        self.assertEqual(self.snapshot().pending_batches(), 0)

    def test_conflict_retry_incorporates_competing_provider_batch(self):
        publish_batch(self.store, self.provider_batch())
        publish = self.store.publish
        first = True

        def race(expected, changes, message):
            nonlocal first
            if first:
                first = False
                with patch.object(self.store, "publish", side_effect=publish):
                    publish_batch(self.store, self.provider_batch("musicbrainz"))
                raise PublicationConflict("Concurrent synthetic capture")
            return publish(expected, changes, message)

        with patch.object(self.store, "publish", side_effect=race):
            result = consolidate(self.store, ACCOUNT)
        self.assertEqual(result.saved_metadata, 2)
        self.assertEqual(self.snapshot().pending_batches(), 0)
        self.assert_coverage_matches_full(self.snapshot())

    def test_legacy_or_custom_limit_reads_all_datasets_and_preserves_old_snapshot(self):
        original = copy.deepcopy(self.store)
        for legacy in (False, True):
            with self.subTest(legacy=legacy):
                self.store = copy.deepcopy(original)
                if legacy:
                    manifest = {**self.before.manifest, "schema_version": 5}
                    self.store.publish(self.before.commit_id, {
                        f"accounts/{ACCOUNT}/manifest.json": canonical_json(manifest).encode(),
                    }, "Synthetic v5 without orphans")
                old = self.snapshot()
                publish_batch(self.store, self.provider_batch())
                with patch.object(self.store, "read", wraps=self.store.read) as read:
                    consolidate(self.store, ACCOUNT, max_bytes=MAX_CSV_BYTES if legacy else 2000)
                self.assertTrue(any("/smarterplaylists_track_metadata/" in call.args[1] for call in read.call_args_list))
                self.assertEqual(self.snapshot().manifest["schema_version"], 6)
                self.assertEqual(old.rows("lastfm_track_metadata"), [])
                if not legacy:
                    self.assertTrue(all(part["byte_size"] <= 2000
                                        for entry in self.snapshot().manifest["datasets"].values()
                                        for part in entry["partitions"]))

    def test_partial_metadata_writer_requires_coverage_audio_and_import_ledger(self):
        for datasets in (
            {"receipts", "lastfm_track_metadata"},
            {"receipts", "metadata_coverage", "smarterplaylists_track_metadata", "smarterplaylists_import_runs"},
            {"receipts", "metadata_coverage", "smarterplaylists_track_metadata", "track_audio"},
            {"receipts", "metadata_coverage", "track_audio"},
            {"receipts", "metadata_coverage", "tracks"},
        ):
            with self.subTest(datasets=datasets), self.assertRaisesRegex(ValidationError, "Partial publication"):
                snapshot_changes(self.before, {dataset: self.before.rows(dataset) for dataset in datasets},
                                 snapshot_id="c" * 32, published_at=DATE, reuse_unchanged=True)

    def test_metadata_staging_never_materializes_full_provider_rows(self):
        publish_batch(self.store, smarter(capture(when=DATE, tempo=120), account=ACCOUNT))
        rows = Snapshot.rows

        def guarded(snapshot, dataset):
            if dataset.endswith("_metadata"):
                raise AssertionError("Full raw dataset materialized")
            return rows(snapshot, dataset)

        with patch.object(Snapshot, "rows", guarded):
            result = consolidate(self.store, ACCOUNT)
        self.assertEqual(result.saved_metadata, 1)
        self.assert_coverage_matches_full(self.snapshot())

    def test_disk_write_failure_preserves_head_and_pending_capture(self):
        publish_batch(self.store, self.provider_batch())
        head = self.store.head()
        with patch.object(DiskRows, "publish_parts", side_effect=OSError("Synthetic disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                consolidate(self.store, ACCOUNT)
        self.assertEqual(self.store.head(), head)
        self.assertEqual(self.snapshot().pending_batches(), 1)
        self.assertEqual(consolidate(self.store, ACCOUNT).saved_metadata, 1)

    def test_misplaced_favorite_envelope_still_blocks_instead_of_quarantining(self):
        favorite = FavoritesBatch(ACCOUNT, "synthetic", DATE, ({
            "revision": 1, "request_id": "d" * 32, "operation": "change", "started_at": DATE,
            "track_id": TRACK, "liked": True, "desired_liked": True, "resolution": "confirmed",
        },))
        update = self.provider_batch()
        path = batch_path(update)
        self.store.publish(self.before.commit_id, {path: encode_batch(favorite)}, "Misplaced synthetic favorite")
        head = self.store.head()
        with self.assertRaises(FavoriteRevisionBlocked):
            consolidate(self.store, ACCOUNT)
        self.assertEqual(self.store.head(), head)

    def test_spl_scan_rebuilds_complete_audio_like_full_path(self):
        manifest = copy.deepcopy(self.before.manifest)
        path = manifest["datasets"]["track_audio"]["partitions"][0]["path"]
        stale = encode_csv(SCHEMAS["track_audio"], [{
            "spotify_track_id": "Z" * 22, "name": "Stale", "audio": {"energy": 0.9},
        }])
        manifest["datasets"]["track_audio"]["partitions"] = [descriptor(path, stale, SCHEMAS["track_audio"])]
        self.store.publish(self.before.commit_id, {
            f"accounts/{ACCOUNT}/{path}": stale,
            f"accounts/{ACCOUNT}/manifest.json": canonical_json(manifest).encode(),
        }, "Synthetic stale audio")
        # A non-track SPL capture must not accidentally rebuild track audio.
        publish_batch(self.store, smarter(
            capture(when=DATE, entity="artist", entity_id=ARTIST, genres=["folk"]),
            live=False, account=ACCOUNT,
        ))
        consolidate(self.store, ACCOUNT)
        self.assertEqual(self.snapshot().rows("track_audio")[0]["spotify_track_id"], "Z" * 22)
        publish_batch(self.store, smarter(capture(when=DATE, tempo=120), account=ACCOUNT))
        consolidate(self.store, ACCOUNT)
        audio = self.snapshot().rows("track_audio")
        self.assertEqual(len(audio), 1)
        self.assertEqual(audio[0]["spotify_track_id"], TRACK)
        self.assertEqual(audio[0]["audio"], {"energy": 0.5, "tempo": 120})
