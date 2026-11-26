import copy
import hashlib
import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from services.pulse import build_pulse
from services.smarterplaylists import normalize_capture
from spoty.domain.listening import ListeningBatch, ValidationError, canonical_json
from spoty.domain.metadata import MetadataBatch
from spoty.services.consolidation import consolidate
from spoty.services.pulse_queries import PulseQueryService
from spoty.storage.csv_codec import (
    BATCH_SCHEMA, CORE_DATASETS, LEGACY_SCHEMAS, decode_batch, decode_csv, encode_batch, encode_csv,
)
from spoty.storage.local_git import LocalGitStore, PublicationConflict, StorageError
from spoty.storage.snapshots import Snapshot, descriptor


TRACK = "T" * 22
ARTIST = "R" * 22
ALBUM = "A" * 22
MBID = "01234567-89ab-cdef-0123-456789abcdef"
ACCOUNT = "account-demo"
DATE = "2026-11-06T10:00:00Z"


def spotify_item(track_id=TRACK, played_at=DATE):
    artist = {"id": ARTIST, "name": "Artist"}
    return {
        "played_at": played_at, "context": None,
        "track": {
            "id": track_id, "name": "Catalog track", "duration_ms": 180000,
            "artists": [artist], "album": {
                "id": ALBUM, "name": "Album", "artists": [artist], "total_tracks": 1,
            },
        },
    }


def capture(when=DATE, entity="track", entity_id=TRACK, **fields):
    values = {"id": entity_id, "uri": f"spotify:{entity}:{entity_id}", "name": "Resource name", **fields}
    return {
        "captured_at": when, "entities": {
            entity: {
                "values": copy.deepcopy(values), "conflicts": {}, "missing_requested": [],
                "raw": {"status": "complete", "playlist_uri": None, "tracks": [copy.deepcopy(values)]},
            },
        },
    }


def smarter(*captures, live=True, producer="pulse", account=ACCOUNT):
    return MetadataBatch.from_smarterplaylists(
        account, producer, "2026-11-06T12:00:00Z", list(captures) or [capture(energy=0.8)],
        live_track=live,
    )


def enrichment(*records, when=DATE):
    return MetadataBatch.from_enrichment_records(ACCOUNT, "enrichment", when, list(records))


class MetadataContractTest(unittest.TestCase):
    def test_identity_and_original_capture_round_trip(self):
        original = capture(energy=0, instrumentalness=0, explicit=False, vendor_unknown={"x": None})
        batch = smarter(original)
        restored = decode_batch(encode_batch(batch))
        self.assertIsInstance(restored, MetadataBatch)
        self.assertEqual(restored.batch_id, batch.batch_id)
        self.assertEqual(restored.payloads[0]["capture"], original)
        self.assertNotEqual(batch.batch_id, smarter(original, account="other-account").batch_id)

    def test_invalid_smarter_captures_rejected_before_publication(self):
        for field, value in (("energy", 2), ("explicit", 0), ("id", "bad"), ("access_token", "secret")):
            with self.subTest(field=field), self.assertRaises(ValidationError):
                smarter(capture(**{field: value}))
        with self.assertRaises(ValidationError):
            smarter(capture(when="2026-11-06T10:00:00"))

    def test_live_lookup_requires_exactly_one_track(self):
        with self.assertRaises(ValidationError):
            smarter(capture(entity="artist", entity_id=ARTIST))
        multi = capture()
        multi["entities"].update(capture(entity="album", entity_id=ALBUM)["entities"])
        with self.assertRaises(ValidationError):
            smarter(multi)

    def test_provider_status_content_mbid_and_levels_checked(self):
        base = {
            "provider": "musicbrainz", "spotify_track_id": TRACK, "match_status": "matched",
            "recording_mbid": MBID, "normalized_data": {"recording_mbid": MBID}, "raw_response": {},
        }
        for update in (
            {"match_status": "ambiguous"}, {"recording_mbid": None},
            {"recording_mbid": "bad"}, {"normalized_data": None}, {"match_status": "unknown"},
        ):
            with self.subTest(update=update), self.assertRaises(ValidationError):
                enrichment({**base, **update})
        with self.assertRaises(ValidationError):
            enrichment({
                "provider": "acousticbrainz", "spotify_track_id": TRACK, "match_status": "found",
                "recording_mbid": MBID, "analysis_level": "high-level",
                "normalized_data": {"lowlevel": {}},
            })
        with self.assertRaises(ValidationError):
            enrichment({"provider": "lastfm", "spotify_track_id": TRACK,
                        "match_status": "not_found", "normalized_data": {}})

    def test_secret_nonfinite_and_unknown_fields_rejected(self):
        base = {"provider": "lastfm", "spotify_track_id": TRACK, "match_status": "matched",
                "normalized_data": {}, "raw_response": {}}
        for update in (
            {"raw_response": {"nested": {"cookie": "secret"}}},
            {"normalized_data": {"value": float("inf")}},
            {"command": "not-supported"}, {"spotify_track_id": "invalid"},
        ):
            with self.subTest(update=update), self.assertRaises(ValidationError):
                enrichment({**base, **update})

    def test_nested_mutation_is_revalidated(self):
        batch = smarter()
        batch.payloads[0]["capture"]["entities"]["track"]["values"]["energy"] = 2
        with self.assertRaises(ValidationError):
            encode_batch(batch)

    def test_modified_identity_or_kind_is_rejected(self):
        batch = smarter()
        rows = decode_csv(BATCH_SCHEMA, encode_batch(batch))
        for update in ({"batch_id": "f" * 64}, {"batch_kind": "unknown"}, {"account_key": "other"}):
            with self.subTest(update=update), self.assertRaises(ValidationError):
                decode_batch(encode_csv(BATCH_SCHEMA, [{**rows[0], **update}]))


@unittest.skipUnless(shutil.which("git"), "Git required for metadata prototype")
class MetadataGitTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = LocalGitStore.initialize(Path(self.temporary.name) / "data.git")

    def publish(self, *captures, **options):
        return self.store.publish_batch(smarter(*captures, **options))

    def snapshot(self):
        return Snapshot.load(self.store, ACCOUNT)

    def metadata(self):
        return self.snapshot().rows("smarterplaylists_track_metadata")[0]

    def test_live_capture_before_catalog_does_not_create_core_entities(self):
        batch = smarter(capture(energy=0.8))
        saved = self.store.publish_batch(batch)
        before = Snapshot.load(self.store, ACCOUNT, saved.commit_id)
        result = consolidate(self.store, ACCOUNT)
        self.assertEqual(result.saved_metadata, 1)
        self.assertEqual(result.saved_events, 0)
        self.assertEqual(before.pending_batches("metadata_capture"), 1)
        self.assertEqual(before.rows("track_audio"), [])
        for dataset in ("artists", "albums", "tracks", "listening_events"):
            self.assertEqual(self.snapshot().rows(dataset), [])
        row = self.metadata()
        self.assertEqual(row["normalized_data"]["energy"], 0.8)
        self.assertEqual(self.snapshot().rows("track_audio")[0]["audio"], {"energy": 0.8})
        self.assertEqual(self.store.publish_batch(batch).status, "already_saved")

    def test_pulse_preserves_zero_false_originals_and_field_provenance(self):
        source = capture(energy=0, explicit=False, instrumentalness=0, artist_popularity=80)
        self.publish(source)
        consolidate(self.store, ACCOUNT)
        expected = normalize_capture(source)
        row = self.metadata()
        self.assertEqual(row["normalized_data"], expected.records[0].normalized_data)
        self.assertEqual(row["raw_response"], expected.records[0].raw_response)
        self.assertIs(row["normalized_data"]["explicit"], False)
        self.assertEqual(row["field_metadata"]["energy"]["capture_id"], expected.capture_id)
        self.assertEqual(row["normalized_data"]["related_artist"]["popularity"], 80)
        receipt = self.snapshot().rows("receipts")[0]
        self.assertEqual((receipt["saved_metadata"], receipt["saved_events"]), (1, 0))

    def test_partial_older_and_equal_time_merge_match_existing_rules(self):
        self.publish(capture(energy=0.9, tempo=100))
        consolidate(self.store, ACCOUNT)
        original = self.metadata()
        later = capture(when="2026-11-07T10:00:00Z", tempo=110)
        later["entities"]["track"]["missing_requested"] = ["energy"]
        self.publish(later, producer="later")
        consolidate(self.store, ACCOUNT)
        current = self.metadata()
        self.assertEqual(current["normalized_data"]["energy"], 0.9)
        self.assertEqual(current["field_metadata"]["energy"], original["field_metadata"]["energy"])
        self.publish(capture(when="2026-11-05T10:00:00Z", energy=0.1, tempo=90), producer="older")
        consolidate(self.store, ACCOUNT)
        self.assertEqual(self.metadata()["normalized_data"], current["normalized_data"])
        self.assertEqual(self.metadata()["raw_response"], current["raw_response"])
        self.assertEqual(self.metadata()["captured_at"], current["captured_at"])
        self.assertEqual(self.metadata()["last_success_run_id"], current["last_success_run_id"])
        self.publish(capture(when="2026-11-07T10:00:00Z", tempo=120), producer="contradiction")
        result = consolidate(self.store, ACCOUNT)
        self.assertEqual(result.rejected_batches, 1)
        self.assertEqual(self.metadata()["normalized_data"], current["normalized_data"])

    def test_provider_records_keep_insert_only_and_levels_independent(self):
        records = [
            {"provider": "lastfm", "spotify_track_id": TRACK, "match_status": "not_found",
             "raw_response": {"error": 6}},
            {"provider": "musicbrainz", "spotify_track_id": TRACK, "match_status": "ambiguous",
             "raw_response": {"recordings": [{"id": MBID}]}},
            *[{"provider": "acousticbrainz", "spotify_track_id": TRACK, "match_status": "found",
               "recording_mbid": MBID, "analysis_level": level,
               "normalized_data": {key: {"value": 0}}, "raw_response": {key: {"value": 0}}}
              for level, key in (("low-level", "lowlevel"), ("high-level", "highlevel"))],
        ]
        batch = enrichment(*records)
        self.store.publish_batch(batch)
        self.assertEqual(consolidate(self.store, ACCOUNT).saved_metadata, 4)
        first = self.snapshot()
        lastfm = first.rows("lastfm_track_metadata")[0]
        self.assertIsNone(lastfm["normalized_data"])
        self.assertEqual(lastfm["raw_response"], {"error": 6})
        self.assertEqual(first.rows("musicbrainz_recording_metadata")[0]["match_status"], "ambiguous")
        self.assertEqual(len(first.rows("acousticbrainz_analysis_metadata")), 2)
        self.store.publish_batch(enrichment({
            "provider": "lastfm", "spotify_track_id": TRACK, "match_status": "matched",
            "normalized_data": {"title": "Now found"}, "raw_response": {},
        }, when="2026-11-07T10:00:00Z"))
        result = consolidate(self.store, ACCOUNT)
        self.assertEqual((result.saved_metadata, result.duplicate_metadata), (0, 1))
        self.assertEqual(self.snapshot().rows("lastfm_track_metadata"), [lastfm])

    def test_repeated_smarter_capture_in_new_envelope_is_not_applied_twice(self):
        self.publish(capture(), producer="one")
        consolidate(self.store, ACCOUNT)
        first = self.metadata()
        self.publish(capture(), producer="two")
        result = consolidate(self.store, ACCOUNT)
        self.assertEqual((result.saved_metadata, result.duplicate_metadata), (0, 1))
        self.assertEqual(self.metadata(), first)
        self.assertEqual(len(self.snapshot().rows("smarterplaylists_import_runs")), 1)

    def test_offline_capture_requires_catalog_and_related_fields_do_not_promote(self):
        self.publish(capture(energy=0.5), live=False)
        self.assertEqual(consolidate(self.store, ACCOUNT).rejected_batches, 1)
        self.assertEqual(self.snapshot().rows("smarterplaylists_track_metadata"), [])
        self.store.publish_batch(ListeningBatch.from_items(
            ACCOUNT, "listening", DATE, [spotify_item()],
        ))
        artist = capture(entity="artist", entity_id=ARTIST, genres=["rock"])
        album = capture(entity="album", entity_id=ALBUM, label="Demo label")
        self.publish(
            capture(energy=0.5, artist_popularity=80), artist, album,
            live=False, producer="offline",
        )
        result = consolidate(self.store, ACCOUNT)
        self.assertEqual(result.saved_metadata, 3)
        self.assertEqual(self.snapshot().rows("smarterplaylists_artist_metadata")[0]["normalized_data"]["genres"], ["rock"])
        self.assertNotIn("popularity", self.snapshot().rows("smarterplaylists_artist_metadata")[0]["normalized_data"])
        self.assertEqual(self.snapshot().rows("smarterplaylists_album_metadata")[0]["normalized_data"]["label"], "Demo label")

    def test_batch_conflict_rolls_back_other_provider_writes(self):
        self.publish(capture(energy=0.5))
        consolidate(self.store, ACCOUNT)
        mixed = MetadataBatch.from_records(ACCOUNT, "mixed", DATE, [
            {"provider": "lastfm", "spotify_track_id": TRACK, "match_status": "matched",
             "normalized_data": {"title": "Test"}, "raw_response": {}, "fetched_at": DATE},
            {"provider": "smarterplaylists", "capture": capture(energy=0.7),
             "allow_unregistered_track": True},
        ])
        self.store.publish_batch(mixed)
        self.assertEqual(consolidate(self.store, ACCOUNT).rejected_batches, 1)
        self.assertEqual(self.snapshot().rows("lastfm_track_metadata"), [])
        self.assertEqual(self.metadata()["normalized_data"]["energy"], 0.5)

    def test_rejected_metadata_does_not_block_valid_batch_and_original_is_preserved(self):
        self.publish(capture(energy=0.5))
        consolidate(self.store, ACCOUNT)
        rejected = smarter(capture(energy=0.7), producer="conflict")
        self.store.publish_batch(rejected)
        valid = smarter(capture(when="2026-11-07T10:00:00Z", tempo=90), producer="valid")
        self.store.publish_batch(valid)
        result = consolidate(self.store, ACCOUNT)
        self.assertEqual((result.accepted_batches, result.rejected_batches), (1, 1))
        self.assertEqual(result.saved_metadata, 1)
        self.assertEqual(self.metadata()["normalized_data"]["tempo"], 90)
        snapshot = self.snapshot()
        receipt = next(row for row in snapshot.rows("receipts") if row["batch_id"] == rejected.batch_id)
        self.assertEqual(receipt["error_code"], "invalid_metadata_batch")
        self.assertEqual((receipt["saved_metadata"], receipt["duplicate_metadata"]), (0, 0))
        for batch, location in ((rejected, "quarantine"), (valid, "archive")):
            path = f"accounts/{ACCOUNT}/{location}/metadata_capture/{batch.captured_at[:7]}/{batch.batch_id}.csv"
            self.assertEqual(self.store.read(snapshot.commit_id, path), encode_batch(batch))
        with self.assertRaises(StorageError):
            self.store.publish_batch(rejected)
        self.assertEqual(snapshot.pending_batches(), 0)

    def test_missing_dataset_in_current_manifest_is_an_error_not_an_empty_result(self):
        self.publish()
        consolidate(self.store, ACCOUNT)
        snapshot = self.snapshot()
        manifest = copy.deepcopy(snapshot.manifest)
        del manifest["datasets"]["track_audio"]
        commit = self.store.publish(
            snapshot.commit_id, {f"accounts/{ACCOUNT}/manifest.json": canonical_json(manifest).encode()},
            "Synthetic incomplete manifest",
        )
        with self.assertRaises(ValidationError):
            Snapshot.load(self.store, ACCOUNT, commit)

    def test_concurrent_publication_recalculates_both_metadata_captures(self):
        self.publish(capture(energy=0.5))
        original = self.store.publish
        injected = False

        def concurrent(expected, changes, message):
            nonlocal injected
            if message == "Consolidate capture batches" and not injected:
                injected = True
                self.publish(capture(when="2026-11-07T10:00:00Z", tempo=123), producer="concurrent")
            return original(expected, changes, message)

        with patch.object(self.store, "publish", side_effect=concurrent):
            result = consolidate(self.store, ACCOUNT)
        self.assertEqual(result.accepted_batches, 2)
        self.assertEqual(self.metadata()["normalized_data"]["energy"], 0.5)
        self.assertEqual(self.metadata()["normalized_data"]["tempo"], 123)

    def test_snapshot_v1_is_readable_and_upgraded_without_losing_receipts(self):
        manifest = {
            "schema_version": 1, "account_key": ACCOUNT, "snapshot_id": "a" * 32,
            "parent_snapshot_id": None, "published_at": DATE, "datasets": {},
        }
        changes = {}
        receipt = {
            "batch_id": "b" * 64, "batch_kind": "listening_capture", "outcome": "consolidated",
            "processed_at": DATE, "snapshot_id": "a" * 32, "saved_events": 1,
            "duplicate_events": 0, "error_code": None,
        }
        for dataset in CORE_DATASETS:
            data = encode_csv(LEGACY_SCHEMAS[dataset], [receipt] if dataset == "receipts" else [])
            relative = f"datasets/{dataset}/bucket-00/part-0000.csv"
            changes[f"accounts/{ACCOUNT}/{relative}"] = data
            manifest["datasets"][dataset] = {
                "schema_version": 1, "bucket_count": 16,
                "partitions": [descriptor(relative, data, LEGACY_SCHEMAS[dataset])],
            }
        changes[f"accounts/{ACCOUNT}/manifest.json"] = canonical_json(manifest).encode()
        commit = self.store.publish(self.store.head(), changes, "Synthetic v1 snapshot")
        old = Snapshot.load(self.store, ACCOUNT, commit)
        self.assertEqual(old.rows("track_audio"), [])
        self.assertEqual(old.rows("receipts")[0]["saved_metadata"], 0)
        self.publish(capture(energy=0.5))
        consolidate(self.store, ACCOUNT)
        self.assertEqual(self.snapshot().manifest["schema_version"], 6)
        self.assertEqual(len(self.snapshot().rows("receipts")), 2)
        self.assertEqual(old.rows("receipts")[0]["batch_id"], receipt["batch_id"])

    def test_pulse_reads_light_projection_and_reuses_existing_calculation(self):
        self.store.publish_batch(ListeningBatch.from_items(
            ACCOUNT, "history", DATE, [
                spotify_item(played_at="2026-11-06T09:00:00Z"), spotify_item(),
            ],
        ))
        self.publish(capture(energy=0, tempo=120))
        consolidate(self.store, ACCOUNT)
        snapshot = self.snapshot()
        original_read = self.store.read
        original_read_many = self.store.read_many

        def no_originals(commit, path):
            if "/datasets/smarterplaylists_" in path or "/archive/" in path:
                raise AssertionError("Pulse loaded complete original metadata")
            return original_read(commit, path)

        def no_grouped_originals(commit, paths):
            if any("/datasets/smarterplaylists_" in path or "/archive/" in path for path in paths):
                raise AssertionError("Pulse loaded complete original metadata")
            return original_read_many(commit, paths)

        now = datetime(2026, 11, 6, 10, 5, tzinfo=timezone.utc).timestamp()
        started = datetime(2026, 11, 6, 10, tzinfo=timezone.utc).timestamp()
        with patch.object(self.store, "read", side_effect=no_originals), patch.object(
            self.store, "read_many", side_effect=no_grouped_originals,
        ):
            rows = PulseQueryService(snapshot).recent_audio_history(TRACK, now, 2, started)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[-1]["audio"], {"energy": 0, "tempo": 120})
        self.assertEqual(rows[-1]["name"], "Catalog track")
        result = build_pulse(rows, now, 2)
        energy = next(band for band in result["bands"] if band["parameter"].key == "energy")
        self.assertEqual((energy["current"], energy["sample_count"]), (0.0, 1))
        valence = next(band for band in result["bands"] if band["parameter"].key == "valence")
        self.assertIsNone(valence["current"])

    def test_pulse_time_bounds_missing_track_and_invalid_query(self):
        self.store.publish_batch(ListeningBatch.from_items(
            ACCOUNT, "history", DATE, [spotify_item()],
        ))
        consolidate(self.store, ACCOUNT)
        service = PulseQueryService(self.snapshot())
        now = datetime(2026, 11, 6, 12, tzinfo=timezone.utc).timestamp()
        self.assertEqual(len(service.recent_audio_history("U" * 22, now, 2)), 2)
        self.assertEqual(len(service.recent_audio_history("U" * 22, now, 1)), 1)
        self.assertEqual(service.recent_audio_history("U" * 22, now)[-1]["audio"], {})
        for arguments in ((TRACK, float("nan"), 2), (TRACK, now, -1), (TRACK, now, True)):
            with self.assertRaises(ValidationError):
                service.recent_audio_history(*arguments)

    def test_partition_failure_and_uncertain_commit_preserve_metadata(self):
        batch = smarter()
        self.store.publish_batch(batch)
        before = self.store.head()
        with self.assertRaises(ValidationError):
            consolidate(self.store, ACCOUNT, max_bytes=1)
        self.assertEqual(self.store.head(), before)
        original_publish = self.store.publish

        def uncertain(*args, **kwargs):
            original_publish(*args, **kwargs)
            raise StorageError("Failure after publication")

        with patch.object(self.store, "publish", side_effect=uncertain):
            with self.assertRaises(StorageError):
                consolidate(self.store, ACCOUNT)
        self.assertEqual(self.store.publish_batch(batch).status, "already_saved")
        self.assertEqual(consolidate(self.store, ACCOUNT).saved_metadata, 0)
        self.assertEqual(len(self.snapshot().rows("smarterplaylists_track_metadata")), 1)

    def test_checksum_validated_for_audio_projection(self):
        self.publish(capture(energy=0.5))
        consolidate(self.store, ACCOUNT)
        snapshot = self.snapshot()
        part = snapshot.manifest["datasets"]["track_audio"]["partitions"][0]
        path = f"accounts/{ACCOUNT}/{part['path']}"
        content = self.store.read(snapshot.commit_id, path)
        self.assertEqual(hashlib.sha256(content).hexdigest(), part["sha256"])
        commit = self.store.publish(snapshot.commit_id, {path: content + b"\n"}, "Synthetic corruption")
        with self.assertRaises(ValidationError):
            PulseQueryService(Snapshot.load(self.store, ACCOUNT, commit)).recent_audio_history(TRACK, 1)

    def test_metadata_publication_conflict_has_bounded_retries(self):
        with patch.object(self.store, "publish", side_effect=PublicationConflict("Synthetic")) as publish:
            with self.assertRaises(PublicationConflict):
                self.store.publish_batch(smarter(), attempts=2)
        self.assertEqual(publish.call_count, 2)
