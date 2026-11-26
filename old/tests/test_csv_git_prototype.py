import copy
import hashlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from spoty.domain.listening import ListeningBatch, ValidationError, canonical_json, parse_json
from spoty.services.consolidation import consolidate
from spoty.storage.csv_codec import (
    BATCH_SCHEMA, JSON_NULL, MAX_CSV_BYTES, SCHEMAS, Field, Schema,
    decode_batch, decode_csv, encode_batch, encode_csv,
)
from spoty.storage.local_git import LocalGitStore, PublicationConflict, StorageError, validate_path
from spoty.storage.snapshots import Snapshot, dataset_partitions, partitions


TRACK = "T" * 22
ALBUM = "A" * 22
ARTIST = "R" * 22


def item(track_id=TRACK, played_at="2026-11-06T10:00:00Z"):
    artist = {"id": ARTIST, "name": "Artista"}
    return {
        "played_at": played_at, "context": {"uri": "spotify:playlist:example"},
        "track": {
            "id": track_id, "name": 'Cancion, "uno"\nsegunda linea',
            "duration_ms": 180_000, "artists": [artist], "explicit": False,
            "track_number": 1, "disc_number": 1, "popularity": 0,
            "album": {
                "id": ALBUM, "name": "Album", "artists": [artist],
                "total_tracks": 10, "images": [{"url": "https://example.invalid/cover"}],
                "release_date": "2020", "release_date_precision": "year",
            },
        },
    }


def batch(*items, account="account-demo", producer="test"):
    return ListeningBatch.from_items(
        account, producer, "2026-11-06T10:05:00Z", list(items) or [item()],
    )


class CodecTest(unittest.TestCase):
    def test_lossless_null_empty_literal_escape_unicode_and_json(self):
        schema = Schema((Field("value", nullable=True), Field("data", "json", True)), ())
        rows = [
            {"value": None, "data": None},
            {"value": "", "data": JSON_NULL},
            {"value": "\\N", "data": {"nested": None, "false": False, "zero": 0}},
            {"value": "\\\\N", "data": []},
            {"value": 'Espa\u00f1a, "\u97f3\u697d"\nnext', "data": {"list": ["b", "a"]}},
        ]
        self.assertEqual(decode_csv(schema, encode_csv(schema, rows)), rows)

    def test_empty_dataset_has_header_but_zero_bytes_is_invalid(self):
        self.assertEqual(decode_csv(SCHEMAS["artists"], encode_csv(SCHEMAS["artists"], [])), [])
        with self.assertRaises(ValidationError):
            decode_csv(SCHEMAS["artists"], b"")

    def test_invalid_json_is_rejected(self):
        for value in ('{"x":1,"x":2}', '{"x":NaN}', '{"x":Infinity}', '{"x":1e999}'):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                parse_json(value)

    def test_invalid_csv_header_version_shape_utf8_and_escape(self):
        schema = Schema((Field("value"),), ())
        for data in (
            b"value\nx\n", b"schema_version,value\n2,x\n",
            b"schema_version,value\n1,x,y\n", b"\xff",
            b"schema_version,value\n1,\\bad\n", b"schema_version,value\n1,\\N\n",
        ):
            with self.subTest(data=data), self.assertRaises(ValidationError):
                decode_csv(schema, data)

    def test_integer_codec_does_not_accept_bool_or_float(self):
        for value in (True, 1.0, "1", None):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                encode_csv(Schema((Field("value", "int"),), ()), [{"value": value}])

    def test_byte_limit_includes_csv_header_and_quoting(self):
        schema = SCHEMAS["artists"]
        rows = [{"id": "id", "name": '"\u00e9\n,'}]
        data = encode_csv(schema, rows)
        self.assertEqual(list(partitions(schema, rows, len(data)).values()), [data])
        with self.assertRaises(ValidationError):
            partitions(schema, rows, len(data) - 1)
        with self.assertRaises(ValidationError):
            decode_csv(schema, data, max_bytes=len(data) - 1)

    def test_twenty_mib_limit_is_enforced_exactly(self):
        schema = Schema((Field("value"),), ())
        overhead = len(encode_csv(schema, [{"value": ""}]))
        data = encode_csv(schema, [{"value": "x" * (MAX_CSV_BYTES - overhead)}])
        self.assertEqual(len(data), MAX_CSV_BYTES)
        self.assertEqual(len(decode_csv(schema, data)[0]["value"]), MAX_CSV_BYTES - overhead)
        with self.assertRaises(ValidationError):
            decode_csv(schema, data + b"\n")

    def test_oversized_listening_batch_is_not_serialized_for_publication(self):
        data = item()
        data["track"]["name"] = "x" * MAX_CSV_BYTES
        with self.assertRaises(ValidationError):
            encode_batch(batch(data))

    def test_partitioning_is_stable_and_enforces_unique_keys(self):
        rows = [{"id": f"id-{index:04d}", "name": "x" * 50} for index in range(70)]
        first = partitions(SCHEMAS["artists"], rows, 160)
        self.assertEqual(first, partitions(SCHEMAS["artists"], list(reversed(rows)), 160))
        self.assertTrue(all(len(data) <= 160 for data in first.values()))
        self.assertEqual(sum(len(decode_csv(SCHEMAS["artists"], data)) for data in first.values()), 70)
        with self.assertRaises(ValidationError):
            partitions(SCHEMAS["artists"], [rows[0], rows[0]])

    def test_bucket_assignment_is_identical_in_a_fresh_process(self):
        rows = [{"id": "id", "name": "name"}]
        expected = next(iter(partitions(SCHEMAS["artists"], rows)))
        script = (
            "from spoty.storage.snapshots import partitions; "
            "from spoty.storage.csv_codec import SCHEMAS; "
            "print(next(iter(partitions(SCHEMAS['artists'], [{'id': 'id', 'name': 'name'}]))))"
        )
        result = subprocess.run(
            [sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1],
            capture_output=True, text=True, check=True,
        )
        self.assertEqual(result.stdout.strip(), expected)

    def test_batch_identity_round_trip_and_item_order(self):
        first = batch(item(), item("U" * 22))
        second = batch(item("U" * 22), item())
        self.assertEqual(first.batch_id, second.batch_id)
        restored = decode_batch(encode_batch(first))
        self.assertEqual(restored, first)
        self.assertEqual(restored.batch_id, first.batch_id)

    def test_history_partitions_use_utc_month_not_local_month(self):
        payloads = batch(
            item(played_at="2026-12-01T00:30:00+01:00"),
            item(played_at="2026-12-01T00:30:00Z"),
        ).payloads
        rows = [{field.name: payload[field.name] for field in SCHEMAS["listening_events"].fields}
                for payload in payloads]
        result = dataset_partitions("listening_events", rows)
        self.assertEqual({path.split("/")[0] for path in result}, {"2026-11", "2026-12"})
        self.assertEqual(sum(len(decode_csv(SCHEMAS["listening_events"], data)) for data in result.values()), 2)

    def test_batch_rejects_changed_identity_or_mixed_envelope(self):
        rows = decode_csv(BATCH_SCHEMA, encode_batch(batch(item(), item("U" * 22))))
        for key, value in (("batch_id", "0" * 64), ("account_key", "other-account")):
            changed = copy.deepcopy(rows)
            changed[0][key] = value
            with self.assertRaises(ValidationError):
                decode_batch(encode_csv(BATCH_SCHEMA, changed))

    def test_input_validation_and_secret_rejection(self):
        for modify in (
            lambda data: data.update(played_at="2026-11-06T10:00:00"),
            lambda data: data["track"].update(id="invalid"),
            lambda data: data["track"].update(duration_ms=True),
            lambda data: data["track"].update(explicit=1),
            lambda data: data["track"].update(popularity=101),
            lambda data: data["track"].update(refresh_token="do-not-store"),
            lambda data: data["track"]["album"].update(release_date="2020-99"),
        ):
            data = item()
            modify(data)
            with self.assertRaises(ValidationError):
                batch(data)

    def test_account_paths_and_empty_batch_are_rejected(self):
        for account in ("../other", "", "a/b", "A", "a" * 65):
            with self.subTest(account=account), self.assertRaises(ValidationError):
                batch(account=account)
        for path in ("../x", "/x", "a\\b", "a//b", "a/../b", "a/.git/x"):
            with self.subTest(path=path), self.assertRaises(ValidationError):
                validate_path(path)
        with self.assertRaises(ValidationError):
            ListeningBatch.from_items("account-demo", "test", "2026-11-06T00:00:00Z", [])

    def test_nested_payload_is_detached_from_input_and_revalidated(self):
        source = item()
        capture = batch(source)
        source["track"]["name"] = "Changed"
        self.assertNotEqual(capture.payloads[0]["spotify_item"]["track"]["name"], "Changed")
        capture.payloads[0]["played_at"] += 1
        with self.assertRaises(ValidationError):
            encode_batch(capture)

    def test_utc_offset_normalized_without_changing_instant(self):
        first = batch(item())
        other = batch(item(played_at="2026-11-06T11:00:00+01:00"))
        self.assertEqual(first.payloads[0]["played_at"], other.payloads[0]["played_at"])


@unittest.skipUnless(shutil.which("git"), "Git is required for the local persistence prototype")
class GitPrototypeTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "data.git"
        self.store = LocalGitStore.initialize(self.path)

    def test_refuses_nonempty_directory_and_application_worktree(self):
        with self.assertRaises(StorageError):
            LocalGitStore.initialize(self.path)
        with self.assertRaises(StorageError):
            LocalGitStore(Path(__file__).resolve().parents[1])

    def test_batch_publication_and_retries_are_idempotent(self):
        capture = batch()
        first = self.store.publish_batch(capture)
        second = self.store.publish_batch(capture)
        self.assertEqual(first.status, "saved")
        self.assertEqual(second.status, "already_saved")
        self.assertEqual(first.commit_id, second.commit_id)
        self.assertEqual(Snapshot.load(self.store, capture.account_key).pending_batches(), 1)

    def test_catalog_history_archive_receipt_published_in_one_commit(self):
        capture = batch(item(), item())
        published = self.store.publish_batch(capture)
        before = Snapshot.load(self.store, capture.account_key, published.commit_id)
        result = consolidate(self.store, capture.account_key)
        after = Snapshot.load(self.store, capture.account_key)
        self.assertEqual((result.saved_events, result.duplicate_events), (1, 1))
        self.assertEqual(before.rows("listening_events"), [])
        self.assertEqual(before.pending_batches(), 1)
        self.assertEqual(after.pending_batches(), 0)
        for dataset in ("artists", "albums", "tracks", "listening_events", "receipts"):
            self.assertEqual(len(after.rows(dataset)), 1)
        paths = self.store.list_paths(result.commit_id)
        self.assertTrue(any("/archive/" in path for path in paths))
        self.assertEqual(after.rows("receipts")[0]["outcome"], "consolidated")
        self.assertEqual(self.store.publish_batch(capture).status, "already_saved")
        no_changes = consolidate(self.store, capture.account_key)
        self.assertEqual(no_changes.commit_id, result.commit_id)

    def test_same_event_in_different_batches_deduplicates_and_catalog_is_insert_only(self):
        self.store.publish_batch(batch())
        consolidate(self.store, "account-demo")
        changed = item()
        changed["track"]["name"] = "New name"
        self.store.publish_batch(batch(changed, producer="another-capture"))
        result = consolidate(self.store, "account-demo")
        current = Snapshot.load(self.store, "account-demo")
        self.assertEqual((result.saved_events, result.duplicate_events), (0, 1))
        self.assertNotEqual(current.rows("tracks")[0]["name"], "New name")
        self.assertEqual(len(current.rows("receipts")), 2)

    def test_conflicting_context_is_quarantined_without_partial_catalog(self):
        self.store.publish_batch(batch())
        consolidate(self.store, "account-demo")
        conflicting = item()
        conflicting["context"]["uri"] = "spotify:album:other"
        self.store.publish_batch(batch(conflicting, item("U" * 22), producer="conflict"))
        result = consolidate(self.store, "account-demo")
        current = Snapshot.load(self.store, "account-demo")
        self.assertEqual(result.rejected_batches, 1)
        self.assertEqual(len(current.rows("tracks")), 1)
        self.assertEqual(len(current.rows("listening_events")), 1)
        self.assertTrue(any("/quarantine/" in path for path in self.store.list_paths(result.commit_id)))
        with self.assertRaises(StorageError):
            self.store.publish_batch(batch(conflicting, item("U" * 22), producer="conflict"))

    def test_invalid_remote_batch_is_preserved_and_does_not_block_valid_batch(self):
        initial = self.store.head()
        invalid = "accounts/account-demo/inbox/listening_capture/2026-11/" + "0" * 64 + ".csv"
        self.store.publish(initial, {invalid: b"invalid"}, "Synthetic invalid batch")
        self.store.publish_batch(batch())
        result = consolidate(self.store, "account-demo")
        self.assertEqual((result.accepted_batches, result.rejected_batches), (1, 1))
        quarantine = invalid.replace("/inbox/", "/quarantine/")
        self.assertEqual(self.store.read(result.commit_id, quarantine), b"invalid")

    def test_two_accounts_do_not_share_event_keys_or_cache(self):
        for account in ("account-one", "account-two"):
            self.store.publish_batch(batch(account=account))
            consolidate(self.store, account)
        for account in ("account-one", "account-two"):
            snapshot = Snapshot.load(self.store, account)
            self.assertEqual(snapshot.manifest["account_key"], account)
            self.assertEqual(len(snapshot.rows("listening_events")), 1)

    def test_stale_publication_conflicts_without_losing_files(self):
        original = self.store.head()
        current = self.store.publish(original, {"test/one.csv": b"one"}, "First")
        with self.assertRaises(PublicationConflict):
            self.store.publish(original, {"test/two.csv": b"two"}, "Stale")
        self.assertEqual(self.store.head(), current)
        self.assertEqual(self.store.list_paths(current), ["test/one.csv"])

    def test_consolidator_recalculates_after_concurrent_batch_publication(self):
        self.store.publish_batch(batch())
        original_publish = self.store.publish
        injected = False

        def concurrent(expected, changes, message):
            nonlocal injected
            if not injected and message == "Consolidate capture batches":
                injected = True
                self.store.publish_batch(batch(item("U" * 22), producer="concurrent"))
            return original_publish(expected, changes, message)

        with patch.object(self.store, "publish", side_effect=concurrent):
            result = consolidate(self.store, "account-demo")
        current = Snapshot.load(self.store, "account-demo")
        self.assertEqual(result.accepted_batches, 2)
        self.assertEqual(len(current.rows("listening_events")), 2)
        self.assertEqual(current.pending_batches(), 0)

    def test_failure_before_update_keeps_inbox_and_no_partial_snapshot(self):
        self.store.publish_batch(batch())
        before = self.store.head()
        with patch.object(self.store, "publish", side_effect=StorageError("Synthetic disk failure")):
            with self.assertRaises(StorageError):
                consolidate(self.store, "account-demo")
        self.assertEqual(self.store.head(), before)
        self.assertIsNone(Snapshot.load(self.store, "account-demo").manifest)
        self.assertEqual(consolidate(self.store, "account-demo").saved_events, 1)

    def test_uncertain_batch_publication_can_be_recovered_by_identity(self):
        original_publish = self.store.publish

        def uncertain(*args, **kwargs):
            original_publish(*args, **kwargs)
            raise StorageError("Synthetic failure after ref update")

        with patch.object(self.store, "publish", side_effect=uncertain):
            with self.assertRaises(StorageError):
                self.store.publish_batch(batch())
        self.assertEqual(self.store.publish_batch(batch()).status, "already_saved")

    def test_uncertain_consolidation_recovers_without_duplicate_history(self):
        self.store.publish_batch(batch())
        original_publish = self.store.publish

        def uncertain(*args, **kwargs):
            original_publish(*args, **kwargs)
            raise StorageError("Synthetic failure after ref update")

        with patch.object(self.store, "publish", side_effect=uncertain):
            with self.assertRaises(StorageError):
                consolidate(self.store, "account-demo")
        after = self.store.head()
        self.assertEqual(consolidate(self.store, "account-demo").commit_id, after)
        self.assertEqual(len(Snapshot.load(self.store, "account-demo").rows("listening_events")), 1)

    def test_conflict_retries_are_bounded(self):
        self.store.publish_batch(batch())
        before = self.store.head()
        with patch.object(self.store, "publish", side_effect=PublicationConflict("Synthetic conflict")) as publish:
            with self.assertRaises(PublicationConflict):
                consolidate(self.store, "account-demo", attempts=2)
        self.assertEqual(publish.call_count, 2)
        self.assertEqual(self.store.head(), before)

    def test_checksum_size_and_row_count_are_verified(self):
        self.store.publish_batch(batch())
        consolidate(self.store, "account-demo")
        snapshot = Snapshot.load(self.store, "account-demo")
        manifest = copy.deepcopy(snapshot.manifest)
        part = manifest["datasets"]["tracks"]["partitions"][0]
        for key, value in (("sha256", "0" * 64), ("byte_size", 0), ("row_count", 99)):
            changed = copy.deepcopy(manifest)
            changed["datasets"]["tracks"]["partitions"][0][key] = value
            commit = self.store.publish(
                self.store.head(),
                {"accounts/account-demo/manifest.json": canonical_json(changed).encode()},
                "Synthetic corrupt descriptor",
            )
            with self.assertRaises(ValidationError):
                Snapshot.load(self.store, "account-demo", commit).rows("tracks")
        self.assertEqual(len(snapshot.rows("tracks")), 1)
        self.assertEqual(part["sha256"], hashlib.sha256(self.store.read(
            snapshot.commit_id, f"accounts/account-demo/{part['path']}",
        )).hexdigest())

    def test_batch_limit_leaves_remaining_input_durably_pending(self):
        self.store.publish_batch(batch())
        self.store.publish_batch(batch(item("U" * 22), producer="second"))
        self.assertEqual(consolidate(self.store, "account-demo", batch_limit=1).accepted_batches, 1)
        self.assertEqual(Snapshot.load(self.store, "account-demo").pending_batches(), 1)
        consolidate(self.store, "account-demo")
        self.assertEqual(len(Snapshot.load(self.store, "account-demo").rows("listening_events")), 2)

    def test_partition_failure_does_not_remove_input(self):
        self.store.publish_batch(batch())
        before = self.store.head()
        with self.assertRaises(ValidationError):
            consolidate(self.store, "account-demo", max_bytes=1)
        self.assertEqual(self.store.head(), before)
        self.assertEqual(Snapshot.load(self.store, "account-demo").pending_batches(), 1)
