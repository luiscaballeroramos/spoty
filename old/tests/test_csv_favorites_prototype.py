import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from spoty.domain.favorites import FavoritesBatch, snapshot_ids
from spoty.domain.listening import ValidationError, canonical_json
from spoty.services.consolidation import consolidate
from spoty.services.favorites import (
    SYNC_SCHEMA, FavoriteCaptureRejected, FavoriteOperationPending,
    FavoriteQueryService, FavoriteRevisionBlocked, FavoritesCoordinator,
)
from spoty.storage.csv_codec import (
    V2_SCHEMAS, decode_batch, decode_csv, encode_batch, encode_csv,
)
from spoty.storage.local_git import LocalGitStore, PublicationConflict, StorageError, batch_path
from spoty.storage.snapshots import Snapshot, descriptor


ACCOUNT = "account-demo"
TRACK = "T" * 22
OTHER = "U" * 22
START = "2026-11-06T10:00:00Z"
END = "2026-11-06T10:05:00Z"


def identity(number):
    return f"{number:032x}"


def pages(*ids, size=50):
    if not ids:
        return [{"items": [], "offset": 0, "limit": size, "total": 0, "next": None}]
    return [
        {
            "items": [{"added_at": START, "track": {"id": track_id, "name": "Captured favorite"}}
                      for track_id in ids[offset:offset + size]],
            "offset": offset, "limit": size, "total": len(ids),
            "next": "next-page" if offset + size < len(ids) else None,
        }
        for offset in range(0, len(ids), size)
    ]


def change_batch(revision=1):
    return FavoritesBatch(ACCOUNT, "demo", END, ({
        "revision": revision, "request_id": identity(revision), "operation": "change",
        "started_at": START, "track_id": TRACK, "desired_liked": True, "liked": True,
        "resolution": "confirmed",
    },))


class FavoritesContractTest(unittest.TestCase):
    def test_complete_pages_and_empty_snapshot(self):
        self.assertEqual(snapshot_ids(pages(TRACK, OTHER, size=1)), [TRACK, OTHER])
        self.assertEqual(snapshot_ids(pages()), [])

    def test_incomplete_duplicates_changed_total_and_invalid_items_rejected(self):
        complete = pages(TRACK, OTHER, size=1)
        malformed = [
            [], complete[:1], complete[1:], pages(TRACK, TRACK),
            [{**complete[0], "total": 3}, complete[1]],
            [{**complete[0], "items": []}, complete[1]],
            [{**pages(TRACK)[0], "next": "unexpected"}],
            [{**pages(TRACK)[0], "items": [{"track": None}]}],
            [{**pages(TRACK)[0], "items": [{"track": {"id": "bad"}}]}],
            [{**pages(TRACK)[0], "offset": True}],
        ]
        for value in malformed:
            with self.subTest(pages=value), self.assertRaises(ValidationError):
                snapshot_ids(value)

    def test_batch_round_trip_and_nested_revalidation(self):
        batch = change_batch()
        self.assertEqual(decode_batch(encode_batch(batch)), batch)
        batch.payloads[0]["liked"] = False
        with self.assertRaises(ValidationError):
            encode_batch(batch)

    def test_invalid_revision_request_and_observation_rejected(self):
        payload = change_batch().payloads[0]
        for update in (
            {"revision": True}, {"revision": 0}, {"revision": 10**20},
            {"request_id": "../x"}, {"liked": 1}, {"track_id": "bad"},
            {"resolution": "assumed"}, {"started_at": "2026-11-07T10:00:00Z"},
            {"access_token": "secret"},
        ):
            with self.subTest(update=update), self.assertRaises(ValidationError):
                FavoritesBatch(ACCOUNT, "demo", END, ({**payload, **update},))


@unittest.skipUnless(shutil.which("git"), "Git required for favorites prototype")
class FavoritesGitTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.store = LocalGitStore.initialize(Path(temporary.name) / "data.git")
        self.coordinator = FavoritesCoordinator(self.store, ACCOUNT, "demo")

    def snapshot(self):
        return Snapshot.load(self.store, ACCOUNT)

    def ids(self):
        return FavoriteQueryService(self.snapshot()).liked_ids()

    def resolve(self, number, liked=True, **options):
        return self.coordinator.resolve_change(
            identity(number), liked, END, resolution=options.get("resolution", "confirmed"),
            remote_settled=options.get("remote_settled", True),
        )

    def change(self, number, track_id=TRACK, desired=True):
        self.coordinator.begin_change(identity(number), track_id, desired, START)
        return self.resolve(number, desired)

    def full_snapshot(self, number, *ids):
        self.coordinator.reserve_snapshot(identity(number), START)
        return self.coordinator.submit_snapshot(identity(number), END, pages(*ids))

    def test_change_is_durable_before_confirmation_and_consolidation(self):
        reservation = self.coordinator.begin_change(identity(1), TRACK, True, START)
        self.assertEqual(reservation.status, "reserved")
        recovered = FavoritesCoordinator(self.store, ACCOUNT, "recovery")
        self.assertEqual(recovered.pending_change()["request_id"], identity(1))
        self.assertEqual(self.snapshot().pending_batches(), 0)
        self.assertEqual(self.ids(), set())
        result = self.resolve(1)
        self.assertEqual(result.status, "saved")
        self.assertIsNone(recovered.pending_change())
        pinned = self.snapshot()
        self.assertEqual(pinned.pending_batches("favorites_capture"), 1)
        consolidated = consolidate(self.store, ACCOUNT)
        self.assertEqual((consolidated.saved_favorites, consolidated.removed_favorites), (1, 0))
        self.assertEqual(self.ids(), {TRACK})
        self.assertEqual(FavoriteQueryService(pinned).liked_ids(), set())
        self.assertEqual(self.snapshot().rows("tracks"), [])
        self.assertTrue(FavoriteQueryService(self.snapshot()).is_liked(TRACK))
        receipt = self.snapshot().rows("receipts")[0]
        self.assertEqual((receipt["saved_events"], receipt["saved_metadata"], receipt["saved_favorites"]), (0, 0, 1))

    def test_pending_operation_blocks_other_changes_and_full_downloads(self):
        self.coordinator.begin_change(identity(1), TRACK, True, START)
        with self.assertRaises(FavoriteOperationPending):
            self.coordinator.begin_change(identity(2), OTHER, True, START)
        with self.assertRaises(FavoriteOperationPending):
            self.coordinator.reserve_snapshot(identity(3), START)
        with self.assertRaises(ValidationError):
            self.resolve(1, remote_settled=False)
        self.assertIsNotNone(self.coordinator.pending_change())

    def test_same_request_retry_does_not_authorize_another_remote_dispatch(self):
        self.coordinator.begin_change(identity(1), TRACK, True, START)
        retry = self.coordinator.begin_change(identity(1), TRACK, True, END)
        self.assertEqual(retry.status, "already_pending")
        self.resolve(1)
        again = self.coordinator.begin_change(identity(1), TRACK, True, END)
        self.assertEqual(again.status, "already_resolved")
        with self.assertRaises(PublicationConflict):
            self.coordinator.begin_change(identity(1), TRACK, False, END)

    def test_uncertain_operation_reconciles_observed_state_not_old_intent(self):
        self.coordinator.begin_change(identity(1), TRACK, True, START)
        self.assertIsNotNone(self.coordinator.pending_change())
        with self.assertRaises(ValidationError):
            self.resolve(1, False)
        self.resolve(1, False, resolution="reconciled")
        consolidate(self.store, ACCOUNT)
        self.assertEqual(self.ids(), set())
        self.assertEqual(self.snapshot().rows("favorites_replica_state")[0]["applied_revision"], 1)
        self.coordinator.begin_change(identity(2), OTHER, True, START)

    def test_like_unlike_and_same_state_are_applied_in_revision_order(self):
        self.change(1)
        self.change(2, desired=False)
        self.change(3, OTHER)
        self.change(4, OTHER)
        result = consolidate(self.store, ACCOUNT)
        self.assertEqual((result.accepted_batches, result.saved_favorites, result.removed_favorites), (4, 2, 1))
        self.assertEqual(self.ids(), {OTHER})
        self.assertEqual(self.snapshot().rows("favorites_replica_state")[0]["applied_revision"], 4)

    def test_full_download_removes_stale_ids_and_empty_library_is_valid(self):
        self.change(1)
        self.full_snapshot(2, OTHER)
        result = consolidate(self.store, ACCOUNT)
        self.assertEqual((result.saved_favorites, result.removed_favorites), (2, 1))
        self.assertEqual(self.ids(), {OTHER})
        self.full_snapshot(3)
        self.assertEqual(consolidate(self.store, ACCOUNT).removed_favorites, 1)
        self.assertEqual(self.ids(), set())

    def test_download_started_before_a_change_is_preserved_as_superseded(self):
        self.coordinator.reserve_snapshot(identity(1), START)
        self.change(2)
        publication = self.coordinator.submit_snapshot(identity(1), END, pages())
        self.assertEqual(publication.status, "superseded")
        self.assertIsNone(publication.batch_id)
        consolidate(self.store, ACCOUNT)
        self.assertEqual(self.ids(), {TRACK})
        path = f"accounts/{ACCOUNT}/coordination/favorites/snapshots/{identity(1)}.csv"
        source = decode_csv(SYNC_SCHEMA, self.store.read(self.store.head(), path))[0]
        self.assertEqual(source["pages"], pages())
        self.assertEqual(source["status"], "superseded")

    def test_download_cannot_replace_replica_while_remote_result_is_pending(self):
        self.coordinator.reserve_snapshot(identity(1), START)
        self.coordinator.begin_change(identity(2), TRACK, True, START)
        publication = self.coordinator.submit_snapshot(identity(1), END, pages(OTHER))
        self.assertEqual(publication.status, "superseded")
        self.assertEqual(self.snapshot().pending_batches(), 0)
        self.resolve(2)
        consolidate(self.store, ACCOUNT)
        self.assertEqual(self.ids(), {TRACK})

    def test_newer_reservation_supersedes_older_even_if_it_finishes_first(self):
        self.coordinator.reserve_snapshot(identity(1), START)
        self.coordinator.reserve_snapshot(identity(2), START)
        older = self.coordinator.submit_snapshot(identity(1), END, pages(TRACK))
        self.assertEqual(older.status, "superseded")
        self.coordinator.submit_snapshot(identity(2), END, pages(OTHER))
        consolidate(self.store, ACCOUNT)
        self.assertEqual(self.ids(), {OTHER})

    def test_invalid_full_capture_is_saved_with_error_without_deleting_favorites(self):
        self.change(1)
        consolidate(self.store, ACCOUNT)
        self.coordinator.reserve_snapshot(identity(2), START)
        incomplete = pages(TRACK, OTHER, size=1)[:1]
        with self.assertRaises(FavoriteCaptureRejected):
            self.coordinator.submit_snapshot(identity(2), END, incomplete)
        self.assertEqual(self.ids(), {TRACK})
        self.assertEqual(self.snapshot().pending_batches(), 0)
        path = f"accounts/{ACCOUNT}/coordination/favorites/snapshots/{identity(2)}.csv"
        row = decode_csv(SYNC_SCHEMA, self.store.read(self.store.head(), path))[0]
        self.assertEqual((row["status"], row["error_code"]), ("invalid", "invalid_favorites_snapshot"))
        self.assertEqual(row["pages"], incomplete)
        with self.assertRaises(FavoriteCaptureRejected):
            self.coordinator.submit_snapshot(identity(2), END, incomplete)

    def test_resolved_change_retry_after_archive_does_not_increment_revision(self):
        first = self.change(1)
        consolidate(self.store, ACCOUNT)
        second = self.resolve(1)
        self.assertEqual(second.status, "already_saved")
        self.assertEqual(first.batch_id, second.batch_id)
        with self.assertRaises(PublicationConflict):
            self.resolve(1, False, resolution="reconciled")
        self.assertEqual(consolidate(self.store, ACCOUNT).accepted_batches, 0)
        self.assertEqual(self.snapshot().rows("favorites_replica_state")[0]["applied_revision"], 1)

    def test_snapshot_retry_and_original_page_archive(self):
        publication = self.full_snapshot(1, TRACK, OTHER)
        again = self.coordinator.submit_snapshot(identity(1), "2026-11-06T10:06:00Z", pages(TRACK, OTHER))
        self.assertEqual((again.status, again.batch_id), ("already_saved", publication.batch_id))
        with self.assertRaises(PublicationConflict):
            self.coordinator.submit_snapshot(identity(1), END, pages(OTHER))
        consolidate(self.store, ACCOUNT)
        archive = next(path for path in self.store.list_paths(self.store.head()) if "/archive/favorites_capture/" in path)
        batch = decode_batch(self.store.read(self.store.head(), archive))
        self.assertEqual(batch.payloads[0]["pages"], pages(TRACK, OTHER))

    def test_publication_failure_after_commit_can_be_recovered_without_a_second_action(self):
        original = self.store.publish

        def uncertain(*args, **kwargs):
            original(*args, **kwargs)
            raise StorageError("Failure after commit")

        with patch.object(self.store, "publish", side_effect=uncertain):
            with self.assertRaises(StorageError):
                self.coordinator.begin_change(identity(1), TRACK, True, START)
        self.assertEqual(self.coordinator.begin_change(identity(1), TRACK, True, START).status, "already_pending")
        with patch.object(self.store, "publish", side_effect=uncertain):
            with self.assertRaises(StorageError):
                self.resolve(1)
        self.assertEqual(self.resolve(1).status, "already_saved")
        consolidate(self.store, ACCOUNT)
        self.assertEqual(self.ids(), {TRACK})

    def test_failed_git_publication_keeps_request_pending(self):
        self.coordinator.begin_change(identity(1), TRACK, True, START)
        with patch.object(self.store, "publish", side_effect=StorageError("Synthetic failure")):
            with self.assertRaises(StorageError):
                self.resolve(1)
        self.assertIsNotNone(self.coordinator.pending_change())
        self.assertEqual(self.snapshot().pending_batches(), 0)

    def test_concurrent_reservations_are_serialized_by_compare_and_swap(self):
        original = self.store.publish
        injected = False

        def concurrent(expected, changes, message):
            nonlocal injected
            if message == "Reserve favorite change" and not injected:
                injected = True
                self.coordinator.begin_change(identity(2), OTHER, True, START)
            return original(expected, changes, message)

        with patch.object(self.store, "publish", side_effect=concurrent):
            with self.assertRaises(FavoriteOperationPending):
                self.coordinator.begin_change(identity(1), TRACK, True, START)
        self.assertEqual(self.coordinator.pending_change()["request_id"], identity(2))

    def test_concurrent_change_during_full_capture_commit_supersedes_download(self):
        self.coordinator.reserve_snapshot(identity(1), START)
        original = self.store.publish
        injected = False

        def concurrent(expected, changes, message):
            nonlocal injected
            if message == "Record favorite snapshot" and not injected:
                injected = True
                self.change(2)
            return original(expected, changes, message)

        with patch.object(self.store, "publish", side_effect=concurrent):
            result = self.coordinator.submit_snapshot(identity(1), END, pages(OTHER))
        self.assertEqual(result.status, "superseded")
        consolidate(self.store, ACCOUNT)
        self.assertEqual(self.ids(), {TRACK})

    def test_concurrent_favorite_confirmation_restarts_consolidation_in_order(self):
        self.change(1)
        original = self.store.publish
        injected = False

        def concurrent(expected, changes, message):
            nonlocal injected
            if message == "Consolidate capture batches" and not injected:
                injected = True
                self.change(2, desired=False)
            return original(expected, changes, message)

        with patch.object(self.store, "publish", side_effect=concurrent):
            result = consolidate(self.store, ACCOUNT)
        self.assertEqual(result.accepted_batches, 2)
        self.assertEqual(self.ids(), set())

    def test_batch_limit_keeps_order_across_consolidation_runs(self):
        self.change(1)
        self.change(2, desired=False)
        self.change(3, OTHER)
        for revision, expected in ((1, {TRACK}), (2, set()), (3, {OTHER})):
            result = consolidate(self.store, ACCOUNT, batch_limit=1)
            self.assertEqual(result.accepted_batches, 1)
            self.assertEqual(self.ids(), expected)
            self.assertEqual(self.snapshot().rows("favorites_replica_state")[0]["applied_revision"], revision)

    def test_favorite_batches_cannot_bypass_coordination(self):
        batch = change_batch()
        with self.assertRaises(ValidationError):
            self.store.publish_batch(batch)
        self.store.publish(self.store.head(), {batch_path(batch): encode_batch(batch)}, "Synthetic ungated batch")
        before = self.store.head()
        with self.assertRaises(FavoriteRevisionBlocked):
            consolidate(self.store, ACCOUNT)
        self.assertEqual(self.store.head(), before)

    def test_corrupt_or_missing_revision_blocks_instead_of_skipping_changes(self):
        self.change(1)
        self.change(2, desired=False)
        paths = self.store.list_paths(self.store.head())
        first = next(path for path in paths if "/inbox/favorites_capture/00000000000000000001/" in path)
        self.store.publish(self.store.head(), {first: None}, "Synthetic missing inbox revision")
        before = self.store.head()
        with self.assertRaises(FavoriteRevisionBlocked):
            consolidate(self.store, ACCOUNT)
        self.assertEqual(self.store.head(), before)
        self.assertEqual(self.ids(), set())

    def test_missing_last_capture_is_not_mistaken_for_an_empty_queue(self):
        self.change(1)
        first = next(path for path in self.store.list_paths(self.store.head()) if "/inbox/favorites_capture/" in path)
        self.store.publish(self.store.head(), {first: None}, "Synthetic missing final inbox capture")
        with self.assertRaises(FavoriteRevisionBlocked):
            consolidate(self.store, ACCOUNT)

    def test_revision_reference_checksum_prevents_tampering(self):
        publication = self.change(1)
        path = next(path for path in self.store.list_paths(self.store.head()) if "/inbox/favorites_capture/" in path)
        batch = decode_batch(self.store.read(self.store.head(), path))
        changed = FavoritesBatch(ACCOUNT, "demo", END, ({
            **batch.payloads[0], "liked": False, "resolution": "reconciled",
        },))
        changes = {path: None, batch_path(changed): encode_batch(changed)}
        self.store.publish(self.store.head(), changes, "Synthetic changed favorite capture")
        with self.assertRaises(FavoriteRevisionBlocked):
            consolidate(self.store, ACCOUNT)
        self.assertNotEqual(publication.batch_id, changed.batch_id)

    def test_account_isolation(self):
        other = FavoritesCoordinator(self.store, "other-account", "other")
        self.coordinator.begin_change(identity(1), TRACK, True, START)
        other.begin_change(identity(1), OTHER, True, START)
        other.resolve_change(identity(1), True, END, resolution="confirmed", remote_settled=True)
        consolidate(self.store, "other-account")
        self.assertEqual(self.ids(), set())
        self.assertEqual(FavoriteQueryService(Snapshot.load(self.store, "other-account")).liked_ids(), {OTHER})
        self.assertIsNotNone(self.coordinator.pending_change())

    def test_v2_snapshot_is_readable_and_atomically_upgraded(self):
        manifest = {
            "schema_version": 2, "account_key": ACCOUNT, "snapshot_id": "a" * 32,
            "parent_snapshot_id": None, "published_at": START, "datasets": {},
        }
        changes = {}
        receipt = {
            "batch_id": "b" * 64, "batch_kind": "metadata_capture", "outcome": "consolidated",
            "processed_at": END, "snapshot_id": "a" * 32, "saved_events": 0,
            "duplicate_events": 0, "error_code": None, "saved_metadata": 3, "duplicate_metadata": 1,
        }
        for dataset, schema in V2_SCHEMAS.items():
            data = encode_csv(schema, [receipt] if dataset == "receipts" else [])
            path = f"datasets/{dataset}/bucket-00/part-0000.csv"
            changes[f"accounts/{ACCOUNT}/{path}"] = data
            manifest["datasets"][dataset] = {
                "schema_version": schema.version, "bucket_count": 16,
                "partitions": [descriptor(path, data, schema)],
            }
        changes[f"accounts/{ACCOUNT}/manifest.json"] = canonical_json(manifest).encode()
        commit = self.store.publish(self.store.head(), changes, "Synthetic v2 snapshot")
        old = Snapshot.load(self.store, ACCOUNT, commit)
        self.assertEqual(old.rows("liked_tracks"), [])
        self.assertEqual(old.rows("receipts"), [{**receipt, "saved_favorites": 0, "removed_favorites": 0}])
        self.change(1)
        consolidate(self.store, ACCOUNT)
        self.assertEqual(self.snapshot().manifest["schema_version"], 6)
        self.assertEqual(self.ids(), {TRACK})
        self.assertEqual(old.rows("liked_tracks"), [])
        self.assertEqual(len(self.snapshot().rows("receipts")), 2)

    def test_coordination_corruption_is_explicit(self):
        self.coordinator.begin_change(identity(1), TRACK, True, START)
        path = f"accounts/{ACCOUNT}/coordination/favorites/changes/{identity(1)}.csv"
        self.store.publish(self.store.head(), {path: None}, "Synthetic missing pending journal")
        with self.assertRaises(ValidationError):
            self.coordinator.pending_change()

    def test_retry_limit_is_bounded(self):
        with patch.object(self.store, "publish", side_effect=PublicationConflict("Synthetic")) as publish:
            with self.assertRaises(PublicationConflict):
                self.coordinator.begin_change(identity(1), TRACK, True, START)
        self.assertEqual(publish.call_count, 3)
