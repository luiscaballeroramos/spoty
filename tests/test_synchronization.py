import unittest
import json
import sys
from contextlib import redirect_stdout
from io import StringIO
from types import ModuleType
from unittest.mock import MagicMock, Mock, patch

from spotifyapi.errors import SpotifyRateLimitError, SpotifyTimeoutError
from services.synchronization import SyncResult, SyncStatus, SynchronizationService


class SynchronizationServiceTest(unittest.TestCase):
    def _service(self, response=None, error=None):
        spotify = Mock()
        if error:
            spotify.get_recently_played.side_effect = error
        else:
            spotify.get_recently_played.return_value = response
        database_factory = Mock()
        service = SynchronizationService(
            database_url="unused",
            spotify_factory=Mock(return_value=spotify),
            database_factory=database_factory,
            sleep=lambda _seconds: None,
            delay=lambda _low, _high: 0,
        )
        return service, spotify, database_factory

    def _liked_service(self, pages, database_result=(True, 0, [])):
        spotify = Mock()
        spotify.get_liked_songs.side_effect = pages
        database = MagicMock()
        database.__enter__.return_value = database
        database.reserve_liked_track_sync.return_value = (4, 2)
        database.apply_liked_track_snapshot.return_value = database_result
        service = SynchronizationService(
            database_url="unused",
            spotify_factory=Mock(return_value=spotify),
            database_factory=Mock(return_value=database),
            sleep=lambda _seconds: None,
            delay=lambda _low, _high: 0,
        )
        return service, spotify, database

    def test_empty_recent_history_is_completed_without_opening_database(self):
        """Verify Empty recent history is completed without opening database."""
        service, spotify, database_factory = self._service({"items": []})

        result = service.sync_listening_events()

        self.assertEqual(result.status, SyncStatus.COMPLETED)
        self.assertEqual(result.counters["fetched_items"], 0)
        spotify.get_recently_played.assert_called_once_with(limit=50)
        database_factory.assert_not_called()

    def test_rate_limit_is_deferred_and_has_nonzero_cli_status(self):
        """Verify Rate limit is deferred and has nonzero cli status."""
        service, _, database_factory = self._service(
            error=SpotifyRateLimitError("get recently played", 30)
        )

        result = service.sync_listening_events()

        self.assertEqual(result.status, SyncStatus.DEFERRED)
        self.assertEqual(result.retry_after, 30)
        self.assertEqual(result.exit_code, 1)
        database_factory.assert_not_called()

    def test_incomplete_event_is_partial_and_not_cli_success(self):
        """Verify Incomplete event is partial and not cli success."""
        service, _, database_factory = self._service(
            {"items": [{"track": {}, "played_at": None}]}
        )

        result = service.sync_listening_events()

        self.assertEqual(result.status, SyncStatus.PARTIAL)
        self.assertEqual(result.counters["skipped_invalid_events"], 1)
        self.assertEqual(result.exit_code, 1)
        database_factory.assert_not_called()

    def test_liked_sync_downloads_all_pages_before_applying_snapshot(self):
        """Verify Liked sync downloads all pages before applying snapshot."""
        pages = [
            {
                "items": [{"track": {"id": "first"}}],
                "offset": 0, "limit": 1, "total": 2, "next": "next-page",
            },
            {
                "items": [{"track": {"id": "second"}}],
                "offset": 1, "limit": 1, "total": 2, "next": None,
            },
        ]
        service, spotify, database = self._liked_service(pages, (True, 2, []))

        result = service.sync_liked_tracks(limit=1)

        self.assertEqual(result.status, SyncStatus.COMPLETED)
        self.assertEqual(result.counters["pages"], 2)
        database.apply_liked_track_snapshot.assert_called_once_with(
            4, 2, ["first", "second"]
        )
        self.assertEqual(spotify.get_liked_songs.call_count, 2)

    def test_liked_sync_accepts_verified_empty_library(self):
        """Verify Liked sync accepts verified empty library."""
        page = {"items": [], "offset": 0, "limit": 50, "total": 0, "next": None}
        service, _, database = self._liked_service([page], (True, 0, ["stale"]))

        result = service.sync_liked_tracks()

        self.assertEqual(result.status, SyncStatus.COMPLETED)
        self.assertEqual(result.counters["removed_count"], 1)
        database.apply_liked_track_snapshot.assert_called_once_with(4, 2, [])

    def test_liked_sync_timeout_never_applies_snapshot(self):
        """Verify Liked sync timeout never applies snapshot."""
        first_page = {
            "items": [{"track": {"id": "first"}}],
            "offset": 0, "limit": 1, "total": 2, "next": "next-page",
        }
        service, _, database = self._liked_service(
            [first_page, SpotifyTimeoutError("get liked songs")]
        )

        result = service.sync_liked_tracks(limit=1)

        self.assertEqual(result.status, SyncStatus.FAILED)
        database.apply_liked_track_snapshot.assert_not_called()

    def test_liked_sync_rate_limit_is_deferred_with_retry_after(self):
        """Verify Liked sync rate limit is deferred with retry after."""
        service, _, database = self._liked_service(
            [SpotifyRateLimitError("get liked songs", 45)]
        )

        result = service.sync_liked_tracks()

        self.assertEqual(result.status, SyncStatus.DEFERRED)
        self.assertEqual(result.retry_after, 45)
        self.assertEqual(result.exit_code, 1)
        database.apply_liked_track_snapshot.assert_not_called()

    def test_liked_sync_with_superseded_snapshot_is_deferred(self):
        """Verify Liked sync with superseded snapshot is deferred."""
        page = {"items": [], "offset": 0, "limit": 50, "total": 0, "next": None}
        service, _, _ = self._liked_service([page], (False, 0, []))

        result = service.sync_liked_tracks()

        self.assertEqual(result.status, SyncStatus.DEFERRED)
        self.assertEqual(result.exit_code, 1)

    def test_liked_sync_rejects_partial_offset(self):
        """Verify Liked sync rejects partial offset."""
        service, spotify, _ = self._liked_service([])

        result = service.sync_liked_tracks(offset=1)

        self.assertEqual(result.status, SyncStatus.FAILED)
        spotify.get_liked_songs.assert_not_called()

    def test_liked_sync_rejects_missing_pagination_metadata(self):
        """Verify Liked sync rejects missing pagination metadata."""
        service, _, database = self._liked_service([{"items": []}])

        result = service.sync_liked_tracks()

        self.assertEqual(result.status, SyncStatus.FAILED)
        database.apply_liked_track_snapshot.assert_not_called()

    def test_liked_sync_marks_missing_track_id_partial(self):
        """Verify Liked sync marks missing track id partial."""
        page = {
            "items": [{"track": {}}],
            "offset": 0, "limit": 50, "total": 1, "next": None,
        }
        service, _, database = self._liked_service([page])

        result = service.sync_liked_tracks()

        self.assertEqual(result.status, SyncStatus.PARTIAL)
        database.apply_liked_track_snapshot.assert_not_called()

    def test_liked_sync_rejects_page_larger_than_library_total(self):
        """Verify Liked sync rejects page larger than library total."""
        page = {
            "items": [{"track": {"id": "first"}}, {"track": {"id": "second"}}],
            "offset": 0, "limit": 2, "total": 1, "next": None,
        }
        service, _, database = self._liked_service([page])

        result = service.sync_liked_tracks(limit=2)

        self.assertEqual(result.status, SyncStatus.FAILED)
        database.apply_liked_track_snapshot.assert_not_called()

    def test_registration_entrypoint_only_delegates_to_shared_service(self):
        """Verify Registration entrypoint only delegates to shared service."""
        result = SyncResult("listening_events", SyncStatus.COMPLETED)
        service = Mock()
        service.sync_listening_events.return_value = result
        with patch("_registration.SynchronizationService", return_value=service):
            from _registration import register_listeningevents

            self.assertIs(register_listeningevents(), result)
        service.sync_listening_events.assert_called_once_with()

    def test_liked_entrypoint_only_delegates_to_shared_service(self):
        """Verify Liked entrypoint only delegates to shared service."""
        result = SyncResult("liked_tracks", SyncStatus.COMPLETED)
        service = Mock()
        service.sync_liked_tracks.return_value = result
        with patch("_liked_tracks.SynchronizationService", return_value=service):
            from _liked_tracks import save_liked_track_ids

            self.assertIs(save_liked_track_ids(limit=20), result)
        service.sync_liked_tracks.assert_called_once_with(limit=20, offset=0)

    def test_configuration_failure_is_returned_as_failed_result(self):
        """Verify Configuration failure is returned as failed result."""
        service = SynchronizationService()
        with patch.dict(sys.modules, {"config": ModuleType("config")}):
            result = service.sync_listening_events()

        self.assertEqual(result.status, SyncStatus.FAILED)
        self.assertEqual(result.exit_code, 1)
        self.assertEqual(result.message, "Synchronization failed (ImportError).")

    def test_registration_cli_prints_json_and_exits_nonzero_for_partial_result(self):
        """Verify Registration cli prints json and exits nonzero for partial result."""
        result = SyncResult(
            "listening_events", SyncStatus.PARTIAL, {"skipped_invalid_events": 1}
        )
        service = Mock()
        service.sync_listening_events.return_value = result
        output = StringIO()
        with (
            patch("_registration.SynchronizationService", return_value=service),
            redirect_stdout(output),
            self.assertRaises(SystemExit) as exited,
        ):
            from _registration import main

            main()

        self.assertEqual(exited.exception.code, 1)
        self.assertEqual(json.loads(output.getvalue())["status"], "partial")
