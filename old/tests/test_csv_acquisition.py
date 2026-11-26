import copy
import hashlib
import unittest
from unittest.mock import patch

from spoty.domain.listening import ValidationError
from spoty.services.acquisition import CsvAcquisitionService
from spoty.services.favorites import (
    REVISION_SCHEMA, SYNC_SCHEMA, FavoriteCaptureRejected, FavoriteOperationPending,
    FavoritesCoordinator,
)
from spoty.storage.contracts import PublicationConflict, StorageError
from spoty.storage.csv_codec import decode_batch, decode_csv


ACCOUNT = "account-demo"
TRACK = "T" * 22
OTHER = "U" * 22
START = "2026-11-06T10:00:00Z"
END = "2026-11-06T10:05:00Z"
MBID = "01234567-89ab-cdef-0123-456789abcdef"


def identity(number):
    return f"{number:032x}"


def spotify_item():
    artist = {"id": "R" * 22, "name": "Artist"}
    return {
        "played_at": START, "context": {"uri": "spotify:playlist:original"},
        "unknown": {"preserved": [False, 0, None]},
        "track": {
            "id": TRACK, "name": "Track", "duration_ms": 180000,
            "artists": [artist], "album": {
                "id": "A" * 22, "name": "Album", "artists": [artist], "total_tracks": 1,
            },
        },
    }


def page(ids=(), *, offset=0, total=None, next_page=None, limit=50):
    return {
        "items": [{"added_at": START, "track": {"id": value, "extra": False}} for value in ids],
        "offset": offset, "limit": limit,
        "total": len(ids) if total is None else total, "next": next_page,
    }


class MemoryStore:
    def __init__(self):
        self.commits = {"0" * 40: {}}
        self.current = "0" * 40
        self.fail_next = None
        self.fail_after_publish = False
        self.writes = []

    def head(self):
        return self.current

    def list_paths(self, commit, prefix=None):
        return sorted(path for path in self.commits[commit] if prefix is None or path.startswith(prefix))

    def read(self, commit, path):
        return self.commits[commit][path]

    def publish(self, expected, changes, message):
        if self.fail_next:
            error, self.fail_next = self.fail_next, None
            raise error
        if expected != self.current:
            raise PublicationConflict("Head moved.")
        files = self.commits[self.current].copy()
        for path, data in changes.items():
            if data is None:
                files.pop(path, None)
            else:
                files[path] = data
        self.current = hashlib.sha1(str(len(self.commits)).encode()).hexdigest()
        self.commits[self.current] = files
        self.writes.append(dict(changes))
        if self.fail_after_publish:
            self.fail_after_publish = False
            raise StorageError("Lost publication response.")
        return self.current

    @property
    def files(self):
        return self.commits[self.current]

    def batches(self):
        return [decode_batch(data) for path, data in self.files.items() if "/inbox/" in path]


class FakeSpotify:
    def __init__(self, pages=(), recent=None, liked=None):
        self.pages = list(pages)
        self.recent = recent if recent is not None else {"items": []}
        self.liked = {TRACK: True} if liked is None else liked
        self.calls = []
        self.mutations = []
        self.mutation_result = True
        self.mutation_error = None
        self.on_read = None

    def _read(self):
        if self.on_read:
            self.on_read()

    def get_recently_played(self, limit=50):
        self.calls.append(("recent", limit))
        self._read()
        if isinstance(self.recent, Exception):
            raise self.recent
        return self.recent

    def get_liked_songs(self, limit=50, offset=0):
        self.calls.append(("page", limit, offset))
        self._read()
        result = self.pages.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def are_tracks_liked(self, ids):
        self.calls.append(("contains", ids))
        self._read()
        if isinstance(self.liked, Exception):
            raise self.liked
        return self.liked

    def like_track(self, track):
        return self._mutate("like", track)

    def unlike_track(self, track):
        return self._mutate("unlike", track)

    def _mutate(self, action, track):
        self.mutations.append((action, track))
        self._read()
        if self.mutation_error:
            raise self.mutation_error
        return self.mutation_result


class CsvAcquisitionTest(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore()
        self.spotify = FakeSpotify()
        self.service = CsvAcquisitionService(self.store, ACCOUNT, "capture", spotify=self.spotify)

    def snapshot(self, number=1, **options):
        return self.service.acquire_favorites_snapshot(
            identity(number), START, lambda: END, **options,
        )

    def pending(self):
        self.service.favorites.begin_change(identity(1), TRACK, True, START)

    def recover(self, **options):
        return self.service.recover_pending_favorite(lambda: END, **options)

    def snapshot_row(self, number=1):
        path = f"accounts/{ACCOUNT}/coordination/favorites/snapshots/{identity(number)}.csv"
        return decode_csv(SYNC_SCHEMA, self.store.files[path])[0]

    def test_recent_preserves_original_item_and_retry_identity(self):
        item = spotify_item()
        original = copy.deepcopy(item)
        self.spotify.recent = {"items": [item]}
        result = self.service.acquire_recent_listening(END, limit=20)
        self.assertEqual(result.status, "saved")
        self.assertEqual(self.spotify.calls, [("recent", 20)])
        self.assertEqual(self.store.batches()[0].payloads[0]["spotify_item"], original)
        item["unknown"]["preserved"].append("changed after capture")
        self.assertEqual(self.store.batches()[0].payloads[0]["spotify_item"], original)
        again = self.service.publish_recent_listening({"items": [original]}, END)
        self.assertEqual((again.status, again.batch_id), ("already_saved", result.batch_id))
        self.assertEqual(len(self.store.writes), 1)

    def test_empty_listening_and_metadata_do_not_write(self):
        self.assertIsNone(self.service.acquire_recent_listening(END))
        self.assertIsNone(self.service.publish_enrichment([], END))
        self.assertIsNone(self.service.publish_smarterplaylists([], END))
        self.assertEqual(self.store.files, {})

    def test_invalid_listening_is_atomic_without_skipping_or_mutating(self):
        valid = spotify_item()
        invalid = {**valid, "played_at": "bad"}
        response = {"items": [valid, invalid]}
        original = copy.deepcopy(response)
        with self.assertRaises(ValidationError):
            self.service.publish_recent_listening(response, END)
        self.assertEqual(response, original)
        self.assertEqual(self.store.files, {})
        for response in ({}, {"items": None}, None):
            with self.subTest(response=response), self.assertRaises(ValidationError):
                self.service.publish_recent_listening(response, END)

    def test_capture_and_durable_publication_errors_propagate(self):
        error = RuntimeError("Injected capture failure.")
        self.spotify.recent = error
        with self.assertRaises(RuntimeError) as caught:
            self.service.acquire_recent_listening(END)
        self.assertIs(caught.exception, error)
        self.assertFalse(self.store.files)
        error = StorageError("Injected persistence failure.")
        self.store.fail_next = error
        with self.assertRaises(StorageError) as caught:
            self.service.publish_recent_listening({"items": [spotify_item()]}, END)
        self.assertIs(caught.exception, error)
        self.assertFalse(self.store.files)

    def test_provider_statuses_published_as_existing_metadata_batches(self):
        records = [
            {"provider": "lastfm", "spotify_track_id": TRACK, "match_status": "matched",
             "normalized_data": {"title": "Original"}, "raw_response": {"track": {"x": 0}}},
            {"provider": "lastfm", "spotify_track_id": OTHER, "match_status": "not_found"},
            {"provider": "musicbrainz", "spotify_track_id": TRACK, "match_status": "matched",
             "recording_mbid": MBID, "normalized_data": {"recording_mbid": MBID}},
            {"provider": "musicbrainz", "spotify_track_id": OTHER, "match_status": "ambiguous",
             "raw_response": {"recordings": [{"id": MBID}]}},
            {"provider": "musicbrainz", "spotify_track_id": "V" * 22, "match_status": "not_found"},
            {"provider": "acousticbrainz", "spotify_track_id": TRACK, "match_status": "found",
             "recording_mbid": MBID, "analysis_level": "low-level",
             "normalized_data": {"lowlevel": {"rhythm": {"bpm": 0}}}},
            {"provider": "acousticbrainz", "spotify_track_id": TRACK, "match_status": "not_found",
             "recording_mbid": MBID, "analysis_level": "high-level"},
        ]
        original = copy.deepcopy(records)
        calls = []

        def fetch():
            calls.append("one bounded acquisition")
            return records

        result = self.service.acquire_enrichment(fetch, END)
        self.assertEqual(calls, ["one bounded acquisition"])
        self.assertEqual(records, original)
        batch = self.store.batches()[0]
        self.assertEqual(batch.batch_kind, "metadata_capture")
        self.assertEqual(len(batch.payloads), 7)
        self.assertTrue(all(record["fetched_at"] == END for record in batch.payloads))
        self.assertEqual(self.service.publish_enrichment(records, END).batch_id, result.batch_id)
        self.assertEqual(len(self.store.writes), 1)

    def test_invalid_provider_status_and_secret_never_write(self):
        base = {"provider": "lastfm", "spotify_track_id": TRACK, "match_status": "not_found"}
        for update in (
            {"match_status": "failed"}, {"normalized_data": {}},
            {"raw_response": {"access_token": "must not persist"}},
        ):
            with self.subTest(update=update), self.assertRaises(ValidationError):
                self.service.publish_enrichment([{**base, **update}], END)
        self.assertFalse(self.store.files)

    def test_provider_errors_are_not_reclassified_as_not_found(self):
        error = TimeoutError("Injected timeout.")

        def fetch():
            raise error

        with self.assertRaises(TimeoutError) as caught:
            self.service.acquire_enrichment(fetch, END)
        self.assertIs(caught.exception, error)
        self.assertFalse(self.store.files)
        self.store.fail_next = StorageError("Injected write failure.")
        with self.assertRaises(StorageError):
            self.service.publish_enrichment([
                {"provider": "lastfm", "spotify_track_id": TRACK, "match_status": "not_found"},
            ], END)
        self.assertFalse(self.store.files)

    def test_smarter_capture_uses_metadata_api_and_preserves_original(self):
        capture = {"captured_at": START, "entities": {"track": {
            "values": {"id": TRACK, "uri": f"spotify:track:{TRACK}", "name": "Track", "energy": 0},
            "conflicts": {}, "missing_requested": [],
            "raw": {"status": "complete", "tracks": [
                {"id": TRACK, "uri": f"spotify:track:{TRACK}", "name": "Track", "energy": 0},
            ]},
        }}}
        self.service.publish_smarterplaylists([capture], END, live_track=True)
        self.assertEqual(self.store.batches()[0].payloads[0]["capture"], capture)

    def test_existing_provider_capture_client_returns_missing_without_inventing_records(self):
        calls = []

        class Provider:
            def capture(self, entity, candidates):
                calls.append((entity, candidates))
                return [], [TRACK]

        candidates = [{"id": TRACK}]
        result = self.service.acquire_smarterplaylists(Provider(), "track", candidates, END)
        self.assertEqual(calls, [("track", candidates)])
        self.assertEqual(result.missing_ids, (TRACK,))
        self.assertIsNone(result.publication)
        self.assertFalse(self.store.files)

    def test_listening_lost_commit_response_is_idempotently_publishable(self):
        response = {"items": [spotify_item()]}
        self.store.fail_after_publish = True
        with self.assertRaises(StorageError):
            self.service.publish_recent_listening(response, END)
        head = self.store.head()
        self.assertEqual(self.service.publish_recent_listening(response, END).status, "already_saved")
        self.assertEqual(self.store.head(), head)
        self.assertEqual(len(self.store.batches()), 1)

    def test_full_snapshot_has_durable_revision_and_no_projection_writes(self):
        original = [
            page([TRACK], total=2, next_page="opaque", limit=1),
            page([OTHER], offset=1, total=2, limit=1),
        ]
        self.spotify.pages = copy.deepcopy(original)
        result = self.snapshot(limit=1)
        self.assertEqual((result.status, result.pages), ("saved", 2))
        self.assertEqual(self.spotify.calls, [("page", 1, 0), ("page", 1, 1)])
        batch = self.store.batches()[0]
        self.assertEqual(batch.revision, 1)
        self.assertEqual(batch.payloads[0]["pages"], original)
        path = f"accounts/{ACCOUNT}/coordination/favorites/revisions/{1:020d}.csv"
        revision = decode_csv(REVISION_SCHEMA, self.store.files[path])[0]
        self.assertEqual(revision["batch_id"], result.publication.batch_id)
        self.assertEqual(self.snapshot_row()["status"], "published")
        self.assertTrue(all("/inbox/" in path or "/coordination/" in path for path in self.store.files))
        self.assertFalse(any("/manifests/" in path or "/datasets/" in path for path in self.store.files))

    def test_empty_snapshot_is_explicitly_complete(self):
        self.spotify.pages = [page()]
        result = self.snapshot()
        self.assertEqual((result.status, result.pages), ("saved", 1))
        self.assertEqual(self.store.batches()[0].payloads[0]["pages"], [page()])

    def test_incomplete_duplicate_and_inconsistent_pages_preserved_not_published(self):
        cases = [
            [page([TRACK], total=2)],
            [page([TRACK], next_page="unexpected")],
            [page([], total=1, next_page="never advances")],
            [page([TRACK], total=2, next_page="opaque"),
             page([TRACK], offset=1, total=2)],
            [page([TRACK], total=2, next_page="opaque"),
             page([OTHER], offset=1, total=3)],
            [page([TRACK], offset=1)],
            [page([TRACK], limit=0)],
            [page([TRACK], total=True)],
        ]
        for number, observed in enumerate(cases, 1):
            with self.subTest(pages=observed):
                self.spotify.pages = copy.deepcopy(observed)
                with self.assertRaises(FavoriteCaptureRejected):
                    self.snapshot(number)
                self.assertEqual(self.snapshot_row(number)["status"], "invalid")
                self.assertEqual(self.snapshot_row(number)["pages"], observed)
                self.assertFalse(self.store.batches())

    def test_interruption_leaves_reservation_and_retry_restarts_at_zero(self):
        first = page([TRACK], total=2, next_page="opaque")
        error = TimeoutError("Interrupted pagination.")
        self.spotify.pages = [first, error]
        with self.assertRaises(TimeoutError) as caught:
            self.snapshot()
        self.assertIs(caught.exception, error)
        self.assertEqual(self.snapshot_row()["status"], "reserved")
        self.assertFalse(self.store.batches())
        self.spotify.calls.clear()
        self.spotify.pages = [page([OTHER])]
        restarted = CsvAcquisitionService(self.store, ACCOUNT, "recovery", spotify=self.spotify)
        result = restarted.acquire_favorites_snapshot(identity(1), START, lambda: END)
        self.assertEqual(result.status, "saved")
        self.assertEqual(self.spotify.calls, [("page", 50, 0)])
        batch = self.store.batches()[0]
        self.assertEqual(batch.producer, "capture")
        self.assertEqual(batch.payloads[0]["pages"], [page([OTHER])])

    def test_finalized_snapshot_retry_never_downloads_again(self):
        self.spotify.pages = [page([TRACK])]
        self.snapshot()
        head = self.store.head()
        result = self.snapshot()
        self.assertEqual(result.status, "already_published")
        self.assertEqual(len(self.spotify.calls), 1)
        self.assertEqual(self.store.head(), head)

    def test_rejected_snapshot_retry_preserves_rejection_without_network(self):
        self.spotify.pages = [page([TRACK], total=2)]
        with self.assertRaises(FavoriteCaptureRejected):
            self.snapshot()
        head = self.store.head()
        with self.assertRaises(FavoriteCaptureRejected):
            self.snapshot()
        self.assertEqual(len(self.spotify.calls), 1)
        self.assertEqual(self.store.head(), head)

    def test_newer_generation_supersedes_capture_without_publishing_revision(self):
        self.spotify.pages = [page([TRACK])]
        self.spotify.on_read = lambda: self.service.favorites.reserve_snapshot(identity(2), START)
        result = self.snapshot()
        self.assertEqual(result.status, "superseded")
        self.assertEqual(self.snapshot_row()["status"], "superseded")
        self.assertFalse(self.store.batches())

    def test_snapshot_reservation_failure_prevents_network(self):
        self.store.fail_next = StorageError("Reservation failed.")
        with self.assertRaises(StorageError):
            self.snapshot()
        self.assertFalse(self.spotify.calls)
        self.assertFalse(self.store.files)

    def test_snapshot_publication_failure_is_retriable_without_revision_gap(self):
        self.spotify.pages = [page([TRACK])]
        self.spotify.on_read = lambda: setattr(self.store, "fail_next", StorageError("Write failed."))
        with self.assertRaises(StorageError):
            self.snapshot()
        self.assertEqual(self.snapshot_row()["status"], "reserved")
        self.assertFalse(self.store.batches())
        self.spotify.on_read = None
        self.spotify.pages = [page([TRACK])]
        self.assertEqual(self.snapshot().status, "saved")
        self.assertEqual(self.store.batches()[0].revision, 1)

    def test_crash_after_snapshot_commit_recovers_without_fetching(self):
        self.spotify.pages = [page([TRACK])]
        self.spotify.on_read = lambda: setattr(self.store, "fail_after_publish", True)
        with self.assertRaises(StorageError):
            self.snapshot()
        self.spotify.on_read = None
        self.assertEqual(self.snapshot().status, "already_published")
        self.assertEqual(len(self.spotify.calls), 1)
        self.assertEqual(len(self.store.batches()), 1)

    def test_pending_change_blocks_snapshot_before_network(self):
        self.pending()
        head = self.store.head()
        with self.assertRaises(FavoriteOperationPending):
            self.snapshot(2)
        self.assertFalse(self.spotify.calls)
        self.assertEqual(self.store.head(), head)

    def test_recovery_requires_settlement_and_records_observation_not_intent(self):
        self.pending()
        head = self.store.head()
        with self.assertRaises(ValidationError):
            self.recover()
        self.assertFalse(self.spotify.calls)
        self.assertEqual(self.store.head(), head)
        self.spotify.liked = {TRACK: False}
        result = self.recover(remote_settled=True)
        self.assertEqual(result.status, "saved")
        self.assertEqual(self.spotify.calls, [("contains", [TRACK])])
        payload = self.store.batches()[0].payloads[0]
        self.assertFalse(payload["liked"])
        self.assertTrue(payload["desired_liked"])
        self.assertEqual(payload["resolution"], "reconciled")
        self.assertIsNone(self.service.favorites.pending_change())
        head = self.store.head()
        self.assertIsNone(self.recover(remote_settled=True))
        self.assertEqual(self.store.head(), head)
        self.assertEqual(len(self.spotify.calls), 1)

    def test_recovery_rejects_changed_pending_request_before_provider_observation(self):
        self.pending()
        head = self.store.head()
        with self.assertRaises(FavoriteOperationPending):
            self.recover(
                remote_settled=True,
                expected_request_id=identity(2),
            )
        self.assertFalse(self.spotify.calls)
        self.assertIsNotNone(self.service.favorites.pending_change())
        self.assertEqual(self.store.head(), head)

    def test_change_favorite_journals_before_dispatch_and_retry_never_replays(self):
        mutation_head = []
        self.spotify.on_read = lambda: mutation_head.append(
            (self.store.head(), self.service.favorites.pending_change())
        )
        result = self.service.change_favorite(identity(1), TRACK, True, START, lambda: END)
        self.assertEqual(result.status, "saved")
        self.assertEqual(self.spotify.mutations, [("like", TRACK)])
        self.assertEqual(len(mutation_head), 1)
        self.assertEqual(mutation_head[0][1]["request_id"], identity(1))
        self.assertEqual(mutation_head[0][1]["status"], "pending")
        payload = self.store.batches()[0].payloads[0]
        self.assertEqual((payload["desired_liked"], payload["liked"], payload["resolution"]),
                         (True, True, "confirmed"))

        retry = self.service.change_favorite(identity(1), TRACK, True, START, lambda: END)
        self.assertEqual(retry.status, "already_resolved")
        self.assertEqual(self.spotify.mutations, [("like", TRACK)])
        self.assertEqual(len(self.store.batches()), 1)

    def test_failed_or_unconfirmed_change_stays_pending_and_requires_observation(self):
        timeout = TimeoutError("Remote response was lost.")
        self.spotify.mutation_error = timeout
        with self.assertRaises(TimeoutError) as caught:
            self.service.change_favorite(identity(1), TRACK, True, START, lambda: END)
        self.assertIs(caught.exception, timeout)
        self.assertIsNotNone(self.service.favorites.pending_change())
        self.assertEqual(self.spotify.mutations, [("like", TRACK)])

        self.spotify.mutation_error = None
        retry = self.service.change_favorite(identity(1), TRACK, True, START, lambda: END)
        self.assertEqual(retry.status, "already_pending")
        self.assertEqual(self.spotify.mutations, [("like", TRACK)])

        self.spotify.liked = {TRACK: True}
        recovered = self.recover(remote_settled=True)
        self.assertEqual(recovered.status, "saved")
        payload = self.store.batches()[0].payloads[0]
        self.assertEqual((payload["liked"], payload["resolution"]), (True, "reconciled"))
        self.assertEqual(self.spotify.mutations, [("like", TRACK)])

    def test_false_mutation_result_does_not_claim_success_or_dispatch_on_retry(self):
        self.spotify.mutation_result = False
        with self.assertRaises(FavoriteOperationPending):
            self.service.change_favorite(identity(1), TRACK, False, START, lambda: END)
        self.assertIsNotNone(self.service.favorites.pending_change())
        retry = self.service.change_favorite(identity(1), TRACK, False, START, lambda: END)
        self.assertEqual(retry.status, "already_pending")
        self.assertEqual(self.spotify.mutations, [("unlike", TRACK)])

    def test_resolution_write_failure_never_replays_remote_change(self):
        error = StorageError("Local resolution could not be published.")
        with patch.object(self.service.favorites, "resolve_change", side_effect=error):
            with self.assertRaises(StorageError) as caught:
                self.service.change_favorite(identity(1), TRACK, True, START, lambda: END)
        self.assertIs(caught.exception, error)
        self.assertIsNotNone(self.service.favorites.pending_change())
        self.assertEqual(self.spotify.mutations, [("like", TRACK)])

        retry = self.service.change_favorite(identity(1), TRACK, True, START, lambda: END)
        self.assertEqual(retry.status, "already_pending")
        self.assertEqual(self.spotify.mutations, [("like", TRACK)])

        self.spotify.liked = {TRACK: True}
        self.assertEqual(self.recover(remote_settled=True).status, "saved")
        self.assertEqual(self.spotify.mutations, [("like", TRACK)])

    def test_recovery_invalid_or_failed_observation_leaves_pending_untouched(self):
        self.pending()
        head = self.store.head()
        for response in ({}, {TRACK: 1}, {TRACK: None}, {TRACK: True, OTHER: False}, [True]):
            self.spotify.liked = response
            with self.subTest(response=response), self.assertRaises(ValidationError):
                self.recover(remote_settled=True)
            self.assertEqual(self.store.head(), head)
            self.assertIsNotNone(self.service.favorites.pending_change())
        error = TimeoutError("Observation failed.")
        self.spotify.liked = error
        with self.assertRaises(TimeoutError) as caught:
            self.recover(remote_settled=True)
        self.assertIs(caught.exception, error)
        self.assertEqual(self.store.head(), head)

    def test_recovery_publication_failure_remains_pending_and_can_retry(self):
        self.pending()
        self.store.fail_next = StorageError("Recovery write failed.")
        with self.assertRaises(StorageError):
            self.recover(remote_settled=True)
        self.assertIsNotNone(self.service.favorites.pending_change())
        self.assertFalse(self.store.batches())
        self.assertEqual(self.recover(remote_settled=True).status, "saved")
        self.assertEqual(self.store.batches()[0].revision, 1)

    def test_recovery_lost_commit_response_retry_does_not_observe_or_write(self):
        self.pending()
        self.store.fail_after_publish = True
        with self.assertRaises(StorageError):
            self.recover(remote_settled=True)
        head = self.store.head()
        self.assertIsNone(self.recover(remote_settled=True))
        self.assertEqual(self.store.head(), head)
        self.assertEqual(len(self.spotify.calls), 1)

    def test_accounts_stay_pinned_and_other_account_pending_is_not_recovered(self):
        other = FavoritesCoordinator(self.store, "other-account", "other")
        other.begin_change(identity(1), OTHER, False, START)
        head = self.store.head()
        self.assertIsNone(self.recover(remote_settled=True))
        self.assertEqual(self.store.head(), head)
        self.assertFalse(self.spotify.calls)
        self.service.publish_recent_listening({"items": [spotify_item()]}, END)
        self.service.publish_enrichment([
            {"provider": "lastfm", "spotify_track_id": TRACK, "match_status": "not_found"},
        ], END)
        self.assertTrue(all(batch.account_key == ACCOUNT for batch in self.store.batches()))
        self.assertIsNotNone(other.pending_change())
        self.pending()
        self.recover(remote_settled=True)
        self.assertIsNotNone(other.pending_change())

    def test_invalid_arguments_fail_before_network_or_writes(self):
        for limit in (0, 51, True, 1.5):
            with self.subTest(limit=limit):
                with self.assertRaises(ValidationError):
                    self.service.acquire_recent_listening(END, limit=limit)
                with self.assertRaises(ValidationError):
                    self.snapshot(limit=limit)
        with self.assertRaises(ValidationError):
            self.service.acquire_recent_listening("bad")
        with self.assertRaises(ValidationError):
            self.snapshot(number=-1)
        self.assertFalse(self.spotify.calls)
        self.assertFalse(self.store.files)


if __name__ == "__main__":
    unittest.main()
