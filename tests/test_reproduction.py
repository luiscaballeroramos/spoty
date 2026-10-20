import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from streamlit_lab import renderer
from streamlit_lab import Position
import requests
from spotifyapi.diagnostics import OPERATIONS, OperationFormatter, OperationLog
from services.errors import LikedTrackReplicaPendingError
from spotifyapi.errors import (
    SpotifyAuthorizationError,
    SpotifyInvalidResponseError,
    SpotifyRateLimitError,
    SpotifyRequestError,
    SpotifyTimeoutError,
    SpotifyUnavailableError,
)

with patch.dict(sys.modules, {
    "config": SimpleNamespace(
        CLIENT_ID="test-client-id",
        CLIENT_SECRET="test-client-secret",
        REDIRECT_URI="http://127.0.0.1:8888/",
        VERBOSE=True,
    ),
    "register.artist": MagicMock(),
    "services.reproduction": MagicMock(),
}):
    from app_views import reproduction
    import spotifyapi.spotifyclient as spotifyclient
    from spotifyapi.spotifyclient import SpotifyClient


class RerunRequested(Exception):
    """Simulate the execution interruption caused by st.rerun during tests."""
    pass


class ReproductionTest(unittest.TestCase):
    """Test playback, UI behavior, and Spotify contracts using mocks and temporary files."""

    def setUp(self):
        """Create isolated session state, mocked clients, and a temporary snapshot with cleanup."""
        self.incidents_patch = patch.object(OPERATIONS, "_incidents", {})
        self.incidents_patch.start()
        self.addCleanup(self.incidents_patch.stop)
        self.playback_temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.playback_temp_dir.cleanup)
        self.playback_path_patch = patch.object(
            reproduction,
            "PLAYBACK_STATE_PATH",
            Path(self.playback_temp_dir.name) / "playback.json",
        )
        self.playback_path_patch.start()
        self.addCleanup(self.playback_path_patch.stop)

        self.session_state = {}
        self.streamlit = MagicMock()
        self.streamlit.session_state = self.session_state
        self.streamlit.columns.return_value = (
            MagicMock(), MagicMock(), MagicMock()
        )
        self.streamlit.button.side_effect = lambda *args, **kwargs: (
            kwargs.get("key") == "reproduction_play_pause"
        )
        self.streamlit.rerun.side_effect = RerunRequested
        self.streamlit_patch = patch.object(reproduction, "st", self.streamlit)
        self.streamlit_patch.start()
        self.addCleanup(self.streamlit_patch.stop)
        self.renderer_streamlit_patch = patch.object(
            renderer, "st", self.streamlit
        )
        self.renderer_streamlit_patch.start()
        self.addCleanup(self.renderer_streamlit_patch.stop)

        self.spotify = MagicMock()
        self.spotify.current_playback.return_value = None
        self.spotify.queue.return_value = None
        self.spotify.current_user_recently_played.return_value = {"items": []}
        self.spotify.current_user_saved_tracks_contains.return_value = [False]
        self.device_error = RuntimeError("device unavailable")
        self.device_error.http_status = 404
        self.spotify.devices.return_value = {
            "devices": [{"id": "original-device", "is_active": True}]
        }
        self.client = SpotifyClient.__new__(SpotifyClient)
        self.client.sp = self.spotify
        self.track = {
            "id": "track-id",
            "uri": "spotify:track:track-id",
            "name": "Song",
            "duration_ms": 240_000,
            "album": {"images": [{"url": "https://example.com/cover.jpg"}]},
        }
        self.playback = {
            "is_playing": True,
            "device": {"id": "original-device"},
            "item": self.track,
            "progress_ms": 72_000,
        }

    def click_play_pause(self, playback):
        """Simulate a play/pause click and require the controls to request a rerun."""
        with self.assertRaises(RerunRequested):
            reproduction._render_playback_controls(
                client=self.client,
                playback=playback,
                is_playing=playback["is_playing"],
                track_id="track-id",
                is_track_liked=False,
                track_name="Song",
                artists_text="Artist",
                previous_track={},
                next_track={},
            )

    def test_like_buttons_target_their_own_track(self):
        """Verify each card targets its own track for liking or unliking without changing playback."""
        for position, track_id, key in (
            ("previous", "previous-id", "reproduction_previous_corner_action"),
            ("current", "track-id", "reproduction_corner_action"),
            ("next", "next-id", "reproduction_next_corner_action"),
        ):
            for liked in (False, True):
                with self.subTest(position=position, liked=liked):
                    self.session_state.clear()
                    self.session_state[reproduction.LIKED_CACHE_KEY] = {
                        "previous-id": liked, "next-id": liked,
                    }
                    self.streamlit.button.side_effect = (
                        lambda *args, **kwargs: kwargs.get("key") == key
                    )
                    with self.assertRaises(RerunRequested):
                        reproduction._render_playback_controls(
                            client=self.client,
                            playback=self.playback,
                            is_playing=True,
                            track_id="track-id",
                            is_track_liked=liked,
                            track_name="Song",
                            artists_text="Artist",
                            previous_track={"id": "previous-id"},
                            next_track={"id": "next-id"},
                        )
                    self.assertEqual(
                        self.session_state["pending_library_action"],
                        {"track_id": track_id, "action": "unlike" if liked else "like"},
                    )
                    self.spotify.previous_track.assert_not_called()
                    self.spotify.next_track.assert_not_called()
                    self.spotify.pause_playback.assert_not_called()

    def test_liked_status_uses_cache_and_pending_action_for_each_track(self):
        """Verify cached liked status avoids duplicate requests and pending actions override it."""
        self.spotify.current_user_saved_tracks_contains.return_value = [True]
        self.assertTrue(reproduction._is_track_liked(self.client, "previous-id"))
        self.assertTrue(reproduction._is_track_liked(self.client, "previous-id"))
        self.spotify.current_user_saved_tracks_contains.assert_called_once_with(
            ["previous-id"]
        )
        self.session_state["pending_library_action"] = {
            "track_id": "previous-id", "action": "unlike",
        }
        self.assertFalse(reproduction._is_track_liked(self.client, "previous-id"))
        self.assertFalse(reproduction._is_track_liked(self.client, None))
        self.assertEqual(self.spotify.current_user_saved_tracks_contains.call_count, 1)

    def test_visible_track_liked_statuses_are_fetched_in_one_batch(self):
        """Fetch unique visible track IDs in one batch and reuse their cached liked status."""
        self.spotify.current_user_saved_tracks_contains.return_value = [
            True, False, True,
        ]

        reproduction._cache_liked_statuses(
            self.client,
            ["track-id", "previous-id", "track-id", None, "next-id"],
        )

        self.spotify.current_user_saved_tracks_contains.assert_called_once_with(
            ["track-id", "previous-id", "next-id"]
        )
        self.assertTrue(reproduction._is_track_liked(self.client, "track-id"))
        self.assertFalse(reproduction._is_track_liked(self.client, "previous-id"))
        self.assertTrue(reproduction._is_track_liked(self.client, "next-id"))
        self.assertEqual(self.spotify.current_user_saved_tracks_contains.call_count, 1)

    def test_view_signature_ignores_playback_progress_but_tracks_visible_changes(self):
        """Verify progress changes preserve the view signature while pausing changes it."""
        view_state = {
            "playback": self.playback.copy(),
            "item": self.track,
            "previous_track": {},
            "next_track": {},
            "is_track_liked": False,
            "is_previous_track_liked": False,
            "is_next_track_liked": False,
        }
        initial_signature = reproduction._reproduction_view_signature(view_state)

        view_state["playback"] = {**self.playback, "progress_ms": 73_000}
        self.assertEqual(
            reproduction._reproduction_view_signature(view_state),
            initial_signature,
        )

        view_state["playback"] = {**self.playback, "is_playing": False}
        self.assertNotEqual(
            reproduction._reproduction_view_signature(view_state),
            initial_signature,
        )

    def test_view_state_check_reruns_only_when_visible_state_changes(self):
        """Verify polling requests a rerun only when visible playback state changes."""
        view_state = {
            "playback": self.playback,
            "item": self.track,
            "previous_track": {},
            "next_track": {},
            "is_track_liked": False,
            "is_previous_track_liked": False,
            "is_next_track_liked": False,
        }
        self.session_state[reproduction.REPRODUCTION_VIEW_SIGNATURE_KEY] = (
            reproduction._reproduction_view_signature(view_state)
        )
        with (
            patch.object(reproduction, "has_spotify_auth_available", return_value=True),
            patch.object(reproduction, "_get_spotify_client", return_value=self.client),
            patch.object(reproduction, "_get_reproduction_view_state", return_value=view_state),
        ):
            reproduction._check_reproduction_view_state()
            self.streamlit.rerun.assert_not_called()

            changed_view_state = {
                **view_state,
                "playback": {**self.playback, "is_playing": False},
            }
            with patch.object(
                reproduction,
                "_get_reproduction_view_state",
                return_value=changed_view_state,
            ):
                with self.assertRaises(RerunRequested):
                    reproduction._check_reproduction_view_state()

        self.streamlit.rerun.assert_called_once_with()

    def test_spotify_429_logs_and_honors_retry_after(self):
        """Verify a 429 preserves its cause, logs the cooldown, and blocks requests until Retry-After expires."""
        rate_limit_error = RuntimeError("rate limited")
        rate_limit_error.http_status = 429
        rate_limit_error.headers = {"Retry-After": "30"}
        operation = Mock(side_effect=[rate_limit_error, "recovered"])

        with (
            patch.object(spotifyclient, "_SPOTIFY_RATE_LIMITED_UNTIL", 0),
            patch("time.monotonic", side_effect=[100, 100, 105, 130]),
            self.assertLogs("spoty.operations", level="INFO") as captured,
        ):
            with self.assertRaises(SpotifyRateLimitError) as first:
                self.client._run("test request", operation)
            self.assertEqual(first.exception.retry_after, 30)
            self.assertIs(first.exception.__cause__, rate_limit_error)
            with self.assertRaises(SpotifyRateLimitError) as cooldown:
                self.client._run("test request", operation)
            self.assertEqual(cooldown.exception.retry_after, 25)
            self.assertEqual(self.client._run("test request", operation), "recovered")

        self.assertEqual(operation.call_count, 2)
        self.assertEqual([record.msg for record in captured.records], ["operation_failed", "operation_recovered"])
        self.assertEqual(captured.records[0].retry_after, 30)
        self.assertEqual(captured.records[1].count, 2)

    def test_spotify_valid_empty_response_is_not_an_error(self):
        """Accept valid empty reads and treat a command returning no content as successful."""
        self.assertIsNone(self.client._run("playback", Mock(return_value=None)))
        self.assertEqual(self.client._run("read", Mock(return_value=[])), [])
        self.assertTrue(self.client._run("command", Mock(return_value=None), command=True))

    def test_operation_logs_exclude_sensitive_exception_text_and_action_arguments(self):
        """Verify diagnostic metadata excludes tokens, callback codes, DSNs, and raw exception messages."""
        observer = OperationLog()
        original = SpotifyTimeoutError("read")
        original.args = ("token=secret-token code=secret-code postgresql://user:password@host/db",)
        with self.assertLogs("spoty.operations", level="WARNING") as captured:
            with self.assertRaises(SpotifyTimeoutError):
                with observer.observe("read secret-token", "spotify"):
                    raise original
        formatted = OperationFormatter().format(captured.records[0])
        for secret in ("secret-token", "secret-code", "password", "postgresql://"):
            self.assertNotIn(secret, formatted)
        self.assertIn('"operation": "operation"', formatted)
        self.assertIn('"error_type": "SpotifyTimeoutError"', formatted)
        self.assertIn('"duration_ms":', formatted)
        self.assertIn(original.run_id, formatted)

    def test_operation_logs_group_repeated_failures_and_report_recovery(self):
        """Verify repeated failures and nested catches log once until a successful operation resets the incident."""
        observer = OperationLog()
        with self.assertLogs("spoty.operations", level="INFO") as captured:
            for attempt in range(3):
                with self.assertRaises(SpotifyTimeoutError):
                    with observer.observe("get current playback"):
                        with observer.observe("get current playback"):
                            raise SpotifyTimeoutError("get current playback")
            with observer.observe("get current playback"):
                pass
            with self.assertRaises(SpotifyTimeoutError):
                with observer.observe("get current playback"):
                    raise SpotifyTimeoutError("get current playback")
        self.assertEqual([record.msg for record in captured.records], ["operation_failed", "operation_recovered", "operation_failed"])
        self.assertEqual(captured.records[1].count, 3)
        self.assertEqual(captured.records[0].run_id, captured.records[1].run_id)
        self.assertNotEqual(captured.records[0].run_id, captured.records[2].run_id)

    def test_invalid_responses_remain_one_incident_until_valid_data_recovers(self):
        """Verify malformed responses stay grouped until response validation succeeds."""
        self.spotify.current_user_saved_tracks_contains.return_value = []
        with self.assertLogs("spoty.operations", level="INFO") as captured:
            for attempt in range(2):
                with self.assertRaises(SpotifyInvalidResponseError):
                    self.client.are_tracks_liked(["track-id"])
            self.spotify.current_user_saved_tracks_contains.return_value = [True]
            self.assertTrue(self.client.is_track_liked("track-id"))
        self.assertEqual([record.msg for record in captured.records], ["operation_failed", "operation_recovered"])
        self.assertEqual(captured.records[1].count, 2)

    def _create_liked_service(self, database_factory):
        """Load the real liked-track service with mocked configuration, client imports, and database access."""
        with patch.dict(sys.modules, {
            "config": SimpleNamespace(DBNAME="unused", VERBOSE=False),
            "register.db": SimpleNamespace(SimpleDB=database_factory),
            "spotifyapi.spotifyclient": SimpleNamespace(SpotifyClient=SpotifyClient),
        }):
            from services.reproduction import ReproductionService
        return ReproductionService(self.client)

    def test_database_failure_logs_safe_metadata_once(self):
        """Verify a local liked-track failure logs one database event without leaking its exception message."""
        database = MagicMock()
        database.__enter__.return_value = database
        database.set_liked_track.side_effect = RuntimeError("postgresql://user:secret-password@host/db")
        service = self._create_liked_service(Mock(return_value=database))
        with (
            patch.object(spotifyclient, "_SPOTIFY_RATE_LIMITED_UNTIL", 0.0),
            self.assertLogs("spoty.operations", level="ERROR") as captured,
        ):
            with self.assertRaises(LikedTrackReplicaPendingError):
                service.save_liked_track("track-id")
        self.spotify.current_user_saved_tracks_add.assert_called_once_with(["track-id"])
        self.assertEqual(len(captured.records), 1)
        self.assertEqual(captured.records[0].provider, "postgresql")
        self.assertEqual(captured.records[0].operation, "save liked track")
        self.assertNotIn("secret-password", OperationFormatter().format(captured.records[0]))

    def test_service_does_not_duplicate_client_failure_logs(self):
        """Verify Spotify failures propagate through the service with one log and no local write."""
        database_factory = Mock()
        service = self._create_liked_service(database_factory)
        self.spotify.current_user_saved_tracks_add.side_effect = requests.Timeout("secret-token")
        with self.assertLogs("spoty.operations", level="WARNING") as captured:
            with self.assertRaises(SpotifyTimeoutError):
                service.save_liked_track("track-id")
        self.assertEqual(len(captured.records), 1)
        self.assertEqual(captured.records[0].provider, "spotify")
        database_factory.assert_not_called()

    def test_initialization_logs_missing_authorization_once(self):
        """Verify missing cached authorization produces one safe initialization event without accessing real tokens."""
        oauth = Mock()
        oauth.cache_handler.get_cached_token.return_value = None
        with (
            patch.dict(spotifyclient.os.environ, {"SPOTIFY_REFRESH_TOKEN": ""}),
            patch.object(spotifyclient, "_create_oauth_manager", return_value=oauth),
            self.assertLogs("spoty.operations", level="WARNING") as captured,
        ):
            with self.assertRaises(SpotifyAuthorizationError):
                SpotifyClient()
        self.assertEqual(len(captured.records), 1)
        self.assertEqual(captured.records[0].operation, "initialize Spotify client")

    def test_missing_spotify_credentials_fail_before_http_request(self):
        """Verify Spotify credentials are required at client use and before any token request."""
        with (
            patch.object(spotifyclient, "CLIENT_ID", None),
            patch.object(spotifyclient, "CLIENT_SECRET", None),
            patch.object(spotifyclient.requests, "post") as post,
            patch.dict(spotifyclient.os.environ, {"SPOTIFY_REFRESH_TOKEN": "test-refresh-token"}),
        ):
            with self.assertRaisesRegex(ValueError, "Missing Spotify credentials"):
                SpotifyClient()
        post.assert_not_called()

    def test_malformed_token_response_logs_without_leaking_token_values(self):
        """Verify invalid token payloads are logged before recovery without exposing refresh tokens or response contents."""
        response = Mock()
        response.json.return_value = {"unexpected": "secret-response"}
        with (
            patch.object(spotifyclient.requests, "post", return_value=response),
            self.assertLogs("spoty.operations", level="WARNING") as captured,
        ):
            with self.assertRaises(SpotifyInvalidResponseError):
                self.client._create_client_from_refresh_token("secret-refresh-token")
        self.assertEqual(len(captured.records), 1)
        formatted = OperationFormatter().format(captured.records[0])
        self.assertNotIn("secret-response", formatted)
        self.assertNotIn("secret-refresh-token", formatted)

    def test_spotify_errors_preserve_category_and_cause(self):
        """Verify HTTP, timeout, connection, and JSON failures retain their category and original cause."""
        for status, expected in (
            (401, SpotifyAuthorizationError),
            (403, SpotifyAuthorizationError),
            (404, SpotifyRequestError),
            (503, SpotifyUnavailableError),
        ):
            with self.subTest(status=status):
                original = RuntimeError("provider detail")
                original.http_status = status
                with self.assertRaises(expected) as caught:
                    self.client._run("read", Mock(side_effect=original))
                self.assertIs(caught.exception.__cause__, original)
                self.assertEqual(caught.exception.http_status, status)
                self.assertNotIn("provider detail", str(caught.exception))
        for original, expected in (
            (requests.Timeout(), SpotifyTimeoutError),
            (requests.ConnectionError(), SpotifyUnavailableError),
            (ValueError("invalid JSON"), SpotifyInvalidResponseError),
        ):
            with self.subTest(expected=expected):
                with self.assertRaises(expected) as caught:
                    self.client._run("read", Mock(side_effect=original))
                self.assertIs(caught.exception.__cause__, original)

    def test_failed_commands_are_not_reported_as_false_or_retried(self):
        """Verify a timed-out command raises SpotifyTimeoutError without returning False or retrying."""
        operation = Mock(side_effect=requests.Timeout())
        with self.assertRaises(SpotifyTimeoutError):
            self.client._run("next", operation, command=True)
        operation.assert_called_once_with()

    def test_liked_response_cannot_create_false_negatives(self):
        """Reject incomplete or malformed liked responses instead of creating false negatives."""
        for response in (None, [], [True], [True, None], {"items": []}):
            with self.subTest(response=response):
                self.spotify.current_user_saved_tracks_contains.return_value = response
                with self.assertRaises(SpotifyInvalidResponseError):
                    self.client.are_tracks_liked(["first", "second"])
        self.spotify.current_user_saved_tracks_contains.return_value = [True, False]
        self.assertEqual(self.client.are_tracks_liked(["first", "second"]), {"first": True, "second": False})

    def test_liked_pages_distinguish_empty_from_invalid(self):
        """Accept an empty liked page but reject responses without a valid items list."""
        self.spotify.current_user_saved_tracks.return_value = {"items": []}
        self.assertEqual(self.client.get_liked_songs(), {"items": []})
        for response in (None, {}, {"items": None}):
            with self.subTest(response=response):
                self.spotify.current_user_saved_tracks.return_value = response
                with self.assertRaises(SpotifyInvalidResponseError):
                    self.client.get_liked_songs()

    def test_playback_failure_does_not_overwrite_valid_cache(self):
        """Verify a playback refresh timeout preserves the existing cache and writes no snapshot."""
        cached = {"playback": self.playback, "fetched_at": 0}
        self.session_state[reproduction.PLAYBACK_CACHE_KEY] = cached
        self.spotify.current_playback.side_effect = requests.Timeout()
        with patch("time.monotonic", return_value=100):
            with self.assertRaises(SpotifyTimeoutError):
                reproduction._get_playback(self.client)
        self.assertIs(self.session_state[reproduction.PLAYBACK_CACHE_KEY], cached)
        self.assertFalse((Path(self.playback_temp_dir.name) / "playback.json").exists())

    def test_liked_failure_does_not_update_cache(self):
        """Verify a liked-status timeout preserves cached values without adding a false negative."""
        self.session_state[reproduction.LIKED_CACHE_KEY] = {"first": True}
        self.spotify.current_user_saved_tracks_contains.side_effect = requests.Timeout()
        with self.assertRaises(SpotifyTimeoutError):
            reproduction._cache_liked_statuses(self.client, ["second"])
        self.assertEqual(self.session_state[reproduction.LIKED_CACHE_KEY], {"first": True})

    def test_view_failure_preserves_last_valid_state_and_recovers(self):
        """Preserve the last valid view as stale during a timeout and clear the error after recovery."""
        valid = {"playback": self.playback, "is_track_liked": True}
        with patch.object(reproduction, "_get_reproduction_view_state", side_effect=[valid, SpotifyTimeoutError("read"), valid]):
            self.assertFalse(reproduction._read_reproduction_view_state(self.client)["is_stale"])
            stale = reproduction._read_reproduction_view_state(self.client)
            self.assertTrue(stale["is_stale"])
            self.assertTrue(stale["is_track_liked"])
            self.assertEqual(stale["playback"], self.playback)
            self.assertFalse(reproduction._read_reproduction_view_state(self.client)["is_stale"])
        self.assertNotIn(reproduction.REPRODUCTION_ERROR_KEY, self.session_state)

    def test_unexpected_view_error_preserves_last_valid_state_without_exposing_message(self):
        """Verify Unexpected view error preserves last valid state without exposing message."""
        valid = {"playback": self.playback, "is_track_liked": True}
        self.session_state[reproduction.LAST_VALID_VIEW_STATE_KEY] = valid
        with patch.object(
            reproduction,
            "_get_reproduction_view_state",
            side_effect=RuntimeError("postgresql://user:secret@host/db"),
        ):
            stale = reproduction._read_reproduction_view_state(self.client)

        self.assertTrue(stale["is_stale"])
        self.assertEqual(stale["playback"], self.playback)
        error = self.session_state[reproduction.REPRODUCTION_ERROR_KEY]
        self.assertEqual(str(error), "Playback data could not be processed.")
        self.assertNotIn("secret", str(error))

    def test_invalid_playback_and_recent_history_are_not_empty_data(self):
        """Reject malformed playback and recent-history responses rather than treating them as empty."""
        self.spotify.current_playback.return_value = []
        with self.assertRaises(SpotifyInvalidResponseError):
            self.client.get_current_playback()
        self.spotify.current_user_recently_played.return_value = {}
        with self.assertRaises(SpotifyInvalidResponseError):
            self.client.get_recently_played()

    def test_restore_fallback_only_handles_explicit_rejections(self):
        """Allow restoration fallback after an explicit 404 but propagate timeouts without repeating the command."""
        original = RuntimeError("context unavailable")
        original.http_status = 404
        self.spotify.start_playback.side_effect = original
        self.assertFalse(reproduction._start_playback_for_restore(self.client, uri=self.track["uri"]))
        self.spotify.start_playback.reset_mock()
        self.spotify.start_playback.side_effect = requests.Timeout()
        with self.assertRaises(SpotifyTimeoutError):
            reproduction._start_playback_for_restore(self.client, uri=self.track["uri"])
        self.spotify.start_playback.assert_called_once()

    def test_transport_does_not_retry_commands(self):
        """Verify token refresh creates a Spotipy transport with automatic retries disabled."""
        response = Mock()
        response.json.return_value = {"access_token": "test-access-token"}
        with (
            patch.object(spotifyclient.requests, "post", return_value=response),
            patch.object(spotifyclient.spotipy, "Spotify") as transport,
        ):
            self.client._create_client_from_refresh_token("test-refresh-token")
        self.assertEqual(transport.call_args.kwargs["retries"], 0)
        self.assertEqual(transport.call_args.kwargs["status_retries"], 0)

    def test_polling_error_reruns_once_and_never_becomes_empty_playback(self):
        """Verify repeated polling timeouts trigger one rerun without fabricating valid playback state."""
        with (
            patch.object(reproduction, "has_spotify_auth_available", return_value=True),
            patch.object(reproduction, "_get_spotify_client", return_value=self.client),
            patch.object(reproduction, "_get_reproduction_view_state", side_effect=SpotifyTimeoutError("read")),
        ):
            with self.assertRaises(RerunRequested):
                reproduction._check_reproduction_view_state()
            reproduction._check_reproduction_view_state()
        self.streamlit.rerun.assert_called_once()
        self.assertIsInstance(self.session_state[reproduction.REPRODUCTION_ERROR_KEY], SpotifyTimeoutError)
        self.assertNotIn(reproduction.LAST_VALID_VIEW_STATE_KEY, self.session_state)

    def test_like_buttons_are_disabled_without_track_id(self):
        """Disable liked buttons without track IDs and prevent pending actions or reruns."""
        self.streamlit.button.side_effect = None
        self.streamlit.button.return_value = True
        for position in ("previous", "current", "next"):
            reproduction._render_like_button(None, False, position)
            self.assertTrue(self.streamlit.button.call_args.kwargs["disabled"])
        self.assertNotIn("pending_library_action", self.session_state)
        self.streamlit.rerun.assert_not_called()

    def test_track_blocks_use_automatic_responsive_layout(self):
        """Verify automatic card placement, portrait proportions, and responsive sizing without st.columns."""
        self.streamlit.button.side_effect = None
        self.streamlit.button.return_value = False

        reproduction._render_playback_controls(
            client=self.client,
            playback=self.playback,
            is_playing=True,
            track_id="track-id",
            is_track_liked=False,
            track_name="Song",
            artists_text="Artist",
            previous_track={"name": "Previous"},
            next_track={"name": "Next"},
        )

        blocks = reproduction.REPRODUCTION_TRACK_BLOCKS
        self.assertTrue(all(block.position is None for block in blocks))
        self.assertIsNone(reproduction.REPRODUCTION_LAYOUT.mobile_breakpoint_px)
        self.assertEqual(
            reproduction.REPRODUCTION_LAYOUT.portrait_row_weights,
            reproduction.REPRODUCTION_COLUMN_RATIOS,
        )
        groups = reproduction.REPRODUCTION_TRACK_GROUPS
        self.assertEqual(
            reproduction.REPRODUCTION_LAYOUT.place(list(groups)),
            {
                "reproduction-previous": Position(1, 1),
                "reproduction-current": Position(1, 2),
                "reproduction-next": Position(1, 3),
            },
        )
        container_keys = [
            call.kwargs.get("key")
            for call in self.streamlit.container.call_args_list
        ]
        self.assertEqual(
            [key for key in container_keys if key and key.startswith("lab-") and not key.startswith("lab-group-")],
            [f"lab-{block.key}" for block in blocks],
        )
        layout_css = self.streamlit.html.call_args_list[1].args[0]
        self.assertNotIn("@media (max-width:", layout_css)
        self.assertIn("@media (orientation: portrait)", layout_css)
        self.assertIn("grid-template-columns: minmax(0, 1fr) !important", layout_css)
        self.assertIn("grid-row: auto !important", layout_css)
        self.assertIn(
            "grid-template-rows: minmax(0, 1fr) minmax(0, 2fr) minmax(0, 1fr) !important",
            layout_css,
        )
        self.assertEqual(self.streamlit.columns.call_count, 0)

        reproduction.REPRODUCTION_GROUP.validate({block.key: block for block in blocks})
        for group in groups:
            self.assertIn(f"lab-group-{group.key}", container_keys)
            self.assertEqual(group.layout.rows, 2)
            self.assertEqual(group.layout.columns, 1)
            self.assertEqual(group.layout.horizontal, "center")
            self.assertEqual(group.layout.vertical, "center")
        for image, title, weight in zip(
            blocks[::2], blocks[1::2], reproduction.REPRODUCTION_COLUMN_RATIOS
        ):
            self.assertEqual(image.height.value, 100)
            self.assertEqual(
                image.height.offset_px,
                reproduction.REPRODUCTION_VIEWPORT_OFFSET_PX,
            )
            self.assertEqual(title.style.background_opacity, 0)
            self.assertEqual(title.height.value, reproduction.REPRODUCTION_TEXT_HEIGHT_PX)
            self.assertEqual(title.height.unit, "px")
            self.assertEqual(image.height.min_px, 0)
            self.assertEqual(title.height.min_px, 0)
            self.assertIsNone(image.height.max_px)
            self.assertIsNone(title.height.max_px)
            self.assertEqual(image.portrait_height.unit, "dvh")
            self.assertAlmostEqual(
                image.portrait_height.value,
                100 * weight / sum(reproduction.REPRODUCTION_COLUMN_RATIOS),
            )
            self.assertEqual(
                image.portrait_height.offset_px,
                reproduction.REPRODUCTION_VIEWPORT_OFFSET_PX
                * weight
                / sum(reproduction.REPRODUCTION_COLUMN_RATIOS),
            )
            self.assertEqual(
                title.portrait_height.value,
                reproduction.REPRODUCTION_TEXT_HEIGHT_PX,
            )
            self.assertEqual(title.portrait_height.unit, "px")
            self.assertIsNone(image.portrait_height.max_px)
            self.assertIsNone(title.portrait_height.max_px)

    def test_track_titles_overlay_the_full_cover_without_intercepting_clicks(self):
        """Verify title overlays cover the artwork without intercepting clicks and keep corner actions positioned."""
        reproduction._render_control_styles()

        css = self.streamlit.html.call_args.args[0]
        self.assertIn("grid-template-rows: minmax(0, 1fr) !important", css)
        self.assertIn("position: absolute !important", css)
        self.assertIn("inset: 0 !important", css)
        self.assertIn("height: 100% !important", css)
        self.assertIn("grid-row: auto !important", css)
        self.assertIn("grid-column: auto !important", css)
        self.assertIn("background: transparent !important", css)
        self.assertIn("pointer-events: none", css)
        self.assertIn("color: #ffffff !important", css)
        self.assertIn("border-radius: 0 !important", css)
        self.assertIn("margin-left: 56px", css)
        self.assertIn("top: 0", css)
        self.assertIn("left: 0", css)
        self.assertIn("right: auto", css)
        self.assertIn("z-index: 4", css)

        style = reproduction.REPRODUCTION_CORNER_ACTION_STYLE
        self.assertEqual(style.width, "56px")
        self.assertEqual(style.height, "48px")
        self.assertEqual(style.min_height, "48px")
        self.assertEqual(style.border_radius, "8px")
        self.assertIsNone(style.aspect_ratio)
        self.assertIsNone(style.portrait)

    def test_track_separators_use_black_and_white_lines_in_both_orientations(self):
        """Verify 4 px black-and-white separators in both orientations and 4 px blur on adjacent artwork."""
        reproduction._render_control_styles()

        css = self.streamlit.html.call_args.args[0]
        self.assertIn(')::after {', css)
        self.assertIn("width: 4px", css)
        self.assertIn("height: 4px", css)
        self.assertIn("left: -2px", css)
        self.assertIn("top: -2px", css)
        self.assertIn("linear-gradient(to right, #000000 50%, #ffffff 50%)", css)
        self.assertIn("linear-gradient(to bottom, #000000 50%, #ffffff 50%)", css)
        self.assertIn("@media (orientation: portrait)", css)
        self.assertIn(".st-key-reproduction_previous", css)
        self.assertIn(".st-key-reproduction_next", css)
        self.assertIn("button::before", css)
        self.assertIn("filter: blur(4px)", css)

    def test_track_info_renders_artist_and_album_below_title(self):
        """Verify track metadata is HTML-escaped, ordered below the title, and paired with accessible controls."""
        reproduction._render_adjacent_track_info(
            {
                "name": "Song <Title>",
                "artists": [{"name": "Artist & Friends"}],
                "album": {"name": 'Album "Deluxe"'},
            },
            "Sin título",
        )

        markup = self.streamlit.markdown.call_args.args[0]
        self.assertIn('reproduction-track-title">Song &lt;Title&gt;</div>', markup)
        self.assertIn('<div class="reproduction-track-details">', markup)
        self.assertIn('<div class="reproduction-track-metadata">', markup)
        self.assertEqual(markup.count('class="reproduction-detail-button"'), 2)
        self.assertEqual(markup.count('type="button"'), 2)
        self.assertNotIn("onclick", markup)
        self.assertIn('aria-label="Artista"', markup)
        self.assertIn('aria-label="Álbum"', markup)
        self.assertIn('aria-hidden="true">person</span>', markup)
        self.assertIn('aria-hidden="true">album</span>', markup)
        self.assertIn('reproduction-track-artists" title="Artist &amp; Friends"', markup)
        self.assertIn('reproduction-track-album" title="Album &quot;Deluxe&quot;"', markup)
        self.assertLess(markup.index("reproduction-track-title"), markup.index("reproduction-track-details"))
        self.assertTrue(self.streamlit.markdown.call_args.kwargs["unsafe_allow_html"])

    def test_track_info_handles_missing_album(self):
        """Verify missing album metadata renders the unknown-album fallback text."""
        reproduction._render_adjacent_track_info({}, "Sin título")

        self.assertIn("Álbum desconocido", self.streamlit.markdown.call_args.args[0])

    def test_controls_use_the_shared_block_style_for_each_button(self):
        """Verify playback buttons reuse shared styles, artwork, proportions, and liked-button styling."""
        self.streamlit.button.side_effect = None
        self.streamlit.button.return_value = False

        with patch.object(
            reproduction, "render_button", wraps=reproduction.render_button
        ) as render_button:
            reproduction._render_playback_controls(
                client=self.client,
                playback=self.playback,
                is_playing=True,
                track_id="track-id",
                is_track_liked=False,
                track_name="Song",
                artists_text="Artist",
                previous_track={},
                next_track={},
            )

        style_by_key = {
            call.kwargs["key"]: call.kwargs["style"]
            for call in render_button.call_args_list
        }
        for call in self.streamlit.button.call_args_list:
            if call.kwargs["key"] in (
                "reproduction_previous",
                "reproduction_play_pause",
                "reproduction_next",
            ):
                self.assertIsNone(call.kwargs.get("help"))
        self.assertEqual(
            set(style_by_key),
            {
                "reproduction_previous",
                "reproduction_play_pause",
                "reproduction_corner_action",
                "reproduction_previous_corner_action",
                "reproduction_next_corner_action",
                "reproduction_next",
            },
        )
        for key in (
            "reproduction_previous",
            "reproduction_play_pause",
            "reproduction_next",
        ):
            self.assertEqual(
                style_by_key[key].color,
                reproduction.REPRODUCTION_CONTROL_STYLE.color,
            )
            self.assertEqual(style_by_key[key].aspect_ratio, "1 / 1")
            self.assertTrue(style_by_key[key].fill_container)
            self.assertIsNone(style_by_key[key].portrait)
            self.assertEqual(style_by_key[key].background_fit, "cover")
        self.assertEqual(
            style_by_key["reproduction_play_pause"].background_image,
            "https://example.com/cover.jpg",
        )
        for key in (
            "reproduction_previous",
            "reproduction_next",
        ):
            self.assertEqual(
                style_by_key[key].background_image,
                "https://example.com/cover.jpg",
            )
        self.assertIs(
            style_by_key["reproduction_corner_action"],
            reproduction.REPRODUCTION_CORNER_ACTION_STYLE,
        )
        self.assertIsNone(
            style_by_key["reproduction_corner_action"].aspect_ratio
        )

    def test_pause_then_resume_after_spotify_loses_playback(self):
        """Verify pausing saves artwork and playback resumes on the original device at 72 seconds after state loss."""
        self.spotify.current_playback.return_value = self.playback
        self.click_play_pause(self.playback)
        self.spotify.pause_playback.assert_called_once_with(device_id="original-device")
        self.assertEqual(
            self.session_state[reproduction.PAUSED_PLAYBACK_KEY]["item"], self.track
        )
        self.assertIsInstance(
            self.session_state[reproduction.PAUSED_PLAYBACK_KEY]["paused_at"], float
        )

        self.spotify.current_playback.return_value = None
        paused = reproduction._get_playback(self.client)
        self.assertFalse(paused["is_playing"])
        self.assertEqual(
            paused["item"]["album"]["images"][0]["url"],
            "https://example.com/cover.jpg",
        )

        self.assertEqual(reproduction._get_playback(self.client), paused)
        self.spotify.devices.return_value["devices"][0]["is_active"] = False
        self.click_play_pause(paused)
        self.spotify.transfer_playback.assert_called_once_with(
            "original-device", force_play=False
        )
        self.spotify.devices.assert_not_called()
        self.spotify.start_playback.assert_called_once_with(
            device_id="original-device",
            uris=["spotify:track:track-id"],
            position_ms=72_000,
        )

    def test_page_entry_refresh_fetches_playback_once(self):
        """Verify page-entry invalidation fetches playback once and subsequent reads use the cache."""
        self.spotify.current_playback.return_value = self.playback

        with patch.object(reproduction.time, "monotonic", return_value=100):
            reproduction.refresh_playback_on_page_entry()
            reproduction._get_playback(self.client)
            reproduction._get_playback(self.client)

        self.spotify.current_playback.assert_called_once_with()

    def test_playing_track_near_end_respects_playback_poll_interval(self):
        """Verify a track near its estimated end still respects the playback polling interval."""
        near_end_playback = {
            **self.playback,
            "progress_ms": self.track["duration_ms"] - 1_000,
        }
        self.spotify.current_playback.return_value = near_end_playback
        self.session_state[reproduction.PLAYBACK_CACHE_KEY] = {
            "playback": near_end_playback,
            "fetched_at": 100,
        }

        clock = Mock()
        clock.monotonic.side_effect = [101, 104, 105]
        with patch.object(reproduction, "time", clock):
            reproduction._get_playback(self.client)
            reproduction._get_playback(self.client)
            reproduction._get_playback(self.client)

        self.spotify.current_playback.assert_called_once_with()

    def test_resume_preserves_queue_when_paused_track_is_still_available(self):
        """Resume a recent pause without replacing the queue, setting a position, or transferring the device."""
        self.session_state[reproduction.PAUSED_PLAYBACK_KEY] = {
            **self.playback, "paused_at": reproduction.time.monotonic()
        }
        paused = {**self.playback, "is_playing": False}
        self.spotify.current_playback.return_value = paused
        self.session_state[reproduction.PLAYBACK_CACHE_KEY] = {
            "playback": paused,
            "fetched_at": 0,
        }
        self.click_play_pause(paused)
        self.spotify.start_playback.assert_called_once_with(
            device_id="original-device", uris=None, position_ms=None
        )
        self.spotify.transfer_playback.assert_not_called()

    def test_resume_restores_context_when_spotify_loses_it(self):
        """Verify resuming restores the saved playlist context, track offset, and position when context is lost."""
        context_uri = "spotify:playlist:playlist-id"
        self.session_state[reproduction.PAUSED_PLAYBACK_KEY] = {
            **self.playback,
            "context": {"uri": context_uri},
            "paused_at": reproduction.time.monotonic(),
        }
        paused = {**self.playback, "is_playing": False, "context": None}
        self.spotify.current_playback.return_value = paused

        self.click_play_pause(paused)

        self.spotify.start_playback.assert_called_once_with(
            device_id="original-device",
            uris=None,
            context_uri=context_uri,
            offset={"uri": "spotify:track:track-id"},
            position_ms=72_000,
        )

    def test_resume_restores_paused_track_when_live_track_changed(self):
        """Restore the saved paused track and position when Spotify reports a different track in the same playlist."""
        context_uri = "spotify:playlist:playlist-id"
        self.session_state[reproduction.PAUSED_PLAYBACK_KEY] = {
            **self.playback,
            "context": {"uri": context_uri},
            "paused_at": reproduction.time.monotonic(),
        }
        changed_track = {**self.track, "id": "other-track"}
        self.spotify.current_playback.return_value = {
            **self.playback,
            "is_playing": False,
            "item": changed_track,
            "context": {"uri": context_uri},
        }

        self.click_play_pause({**self.playback, "is_playing": False})

        self.spotify.start_playback.assert_called_once_with(
            device_id="original-device",
            uris=None,
            context_uri=context_uri,
            offset={"uri": "spotify:track:track-id"},
            position_ms=72_000,
        )

    def test_long_pause_restores_saved_track_and_position(self):
        """Verify resuming after a long pause explicitly restores the saved track URI and 72-second position."""
        self.session_state[reproduction.PAUSED_PLAYBACK_KEY] = {
            **self.playback, "paused_at": reproduction.time.monotonic() - 120
        }
        paused = {**self.playback, "is_playing": False}
        self.spotify.current_playback.return_value = paused
        self.click_play_pause(paused)
        self.spotify.start_playback.assert_called_once_with(
            device_id="original-device",
            uris=["spotify:track:track-id"],
            position_ms=72_000,
        )

    def test_pause_saves_queue_for_long_resume_without_context(self):
        """Save the queue when pausing without context and restore the current track plus queued tracks in order."""
        self.spotify.current_playback.return_value = self.playback
        self.spotify.queue.return_value = {
            "currently_playing": self.track,
            "queue": [
                {"id": "next-id", "uri": "spotify:track:next-id"},
                {"id": "later-id", "uri": "spotify:track:later-id"},
            ],
        }
        self.click_play_pause(self.playback)
        self.assertEqual(
            self.session_state[reproduction.PAUSED_PLAYBACK_KEY]["queue_uris"],
            ["spotify:track:next-id", "spotify:track:later-id"],
        )

        self.session_state[reproduction.PAUSED_PLAYBACK_KEY]["paused_at"] -= 120
        self.spotify.current_playback.return_value = None
        self.click_play_pause({**self.playback, "is_playing": False})
        self.spotify.start_playback.assert_called_once_with(
            device_id="original-device",
            uris=[
                "spotify:track:track-id",
                "spotify:track:next-id",
                "spotify:track:later-id",
            ],
            position_ms=72_000,
        )

    def test_long_pause_restores_playlist_context_and_track_position(self):
        """Restore playlist context, track offset, and saved position after a long pause with no live playback."""
        self.session_state[reproduction.PAUSED_PLAYBACK_KEY] = {
            **self.playback,
            "context": {"uri": "spotify:playlist:playlist-id"},
            "paused_at": reproduction.time.monotonic() - 120,
        }

        self.click_play_pause({**self.playback, "is_playing": False})

        self.spotify.start_playback.assert_called_once_with(
            device_id="original-device",
            uris=None,
            context_uri="spotify:playlist:playlist-id",
            offset={"uri": "spotify:track:track-id"},
            position_ms=72_000,
        )

    def test_persisted_pause_restores_context_and_position_in_new_session(self):
        """Load a persisted pause in a new session and resume its original playlist, track, and position."""
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "playback.json"
            with patch.object(reproduction, "PLAYBACK_STATE_PATH", state_path):
                reproduction._persist_playback(
                    {
                        **self.playback,
                        "context": {"uri": "spotify:playlist:playlist-id"},
                        "paused_at": reproduction.time.monotonic() - 120,
                    }
                )
                self.session_state.clear()
                self.spotify.current_playback.return_value = None

                paused = reproduction._get_playback(self.client)
                self.assertEqual(paused["item"], self.track)
                self.assertFalse(paused["is_playing"])
                self.click_play_pause(paused)

        self.spotify.start_playback.assert_called_once_with(
            device_id="original-device",
            uris=None,
            context_uri="spotify:playlist:playlist-id",
            offset={"uri": "spotify:track:track-id"},
            position_ms=72_000,
        )

    def test_persisted_playing_track_is_available_when_spotify_has_no_playback(self):
        """Recover a persisted playing track and progress as paused when Spotify reports no playback."""
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "playback.json"
            with patch.object(reproduction, "PLAYBACK_STATE_PATH", state_path):
                reproduction._persist_playback(self.playback)
                self.session_state.clear()
                self.spotify.current_playback.return_value = None

                paused = reproduction._get_playback(self.client)

        self.assertEqual(paused["item"], self.track)
        self.assertEqual(paused["progress_ms"], 72_000)
        self.assertFalse(paused["is_playing"])

    def test_playback_poll_persists_track_for_a_new_session(self):
        """Verify playback polling persists a snapshot that restores track and progress in a new session."""
        self.spotify.current_playback.return_value = self.playback
        reproduction._get_playback(self.client)
        self.assertTrue(reproduction.PLAYBACK_STATE_PATH.exists())

        self.session_state.clear()
        self.spotify.current_playback.return_value = None
        restored = reproduction._get_playback(self.client)

        self.assertEqual(restored["item"], self.track)
        self.assertEqual(restored["progress_ms"], 72_000)
        self.assertFalse(restored["is_playing"])

    def test_inactive_device_restores_track_even_when_paused_item_is_reported(self):
        """Transfer to the original device and restore track and position without searching for other devices."""
        self.session_state[reproduction.PAUSED_PLAYBACK_KEY] = self.playback
        self.spotify.current_playback.return_value = None
        self.click_play_pause({**self.playback, "is_playing": False})
        self.spotify.transfer_playback.assert_called_once_with(
            "original-device", force_play=False
        )
        self.spotify.devices.assert_not_called()
        self.spotify.start_playback.assert_called_once_with(
            device_id="original-device",
            uris=["spotify:track:track-id"],
            position_ms=72_000,
        )

    def test_missing_original_device_is_not_replaced_by_another_device(self):
        """Verify an unavailable original device prevents playback without selecting a replacement."""
        self.session_state[reproduction.PAUSED_PLAYBACK_KEY] = self.playback
        self.spotify.current_playback.return_value = None
        self.spotify.transfer_playback.side_effect = self.device_error
        reproduction._render_playback_controls(
            client=self.client,
            playback={**self.playback, "is_playing": False},
            is_playing=False,
            track_id="track-id",
            is_track_liked=False,
            track_name="Song",
            artists_text="Artist",
            previous_track={},
            next_track={},
        )
        self.spotify.start_playback.assert_not_called()
        self.spotify.devices.assert_not_called()
        self.streamlit.error.assert_not_called()

    def test_resume_does_not_search_for_a_replacement_device_id(self):
        """Attempt the expired device ID once without searching for replacements or changing the snapshot."""
        snapshot = {
            **self.playback,
            "device": {
                "id": "expired-device-id",
                "name": "Living room speaker",
                "type": "Speaker",
                "is_active": False,
            },
            "paused_at": reproduction.time.monotonic() - 120,
        }
        self.session_state[reproduction.PAUSED_PLAYBACK_KEY] = snapshot
        self.spotify.current_playback.return_value = None
        self.spotify.transfer_playback.side_effect = self.device_error

        reproduction._handle_play_pause(
            self.client, {**self.playback, "is_playing": False}
        )

        self.spotify.devices.assert_not_called()
        self.spotify.transfer_playback.assert_called_once_with(
            "expired-device-id", force_play=False
        )
        self.spotify.start_playback.assert_not_called()
        self.assertEqual(
            self.session_state[reproduction.PAUSED_PLAYBACK_KEY]["device"]["id"],
            "expired-device-id",
        )

    def test_resume_does_not_guess_between_duplicate_device_matches(self):
        """Verify duplicate device names never justify choosing a replacement for the saved device ID."""
        self.session_state[reproduction.PAUSED_PLAYBACK_KEY] = {
            **self.playback,
            "device": {
                "id": "expired-device-id",
                "name": "Living room speaker",
                "type": "Speaker",
            },
            "paused_at": reproduction.time.monotonic() - 120,
        }
        self.spotify.current_playback.return_value = None
        self.spotify.transfer_playback.side_effect = self.device_error
        self.spotify.devices.return_value = {
            "devices": [
                {
                    "id": "speaker-one",
                    "name": "Living room speaker",
                    "type": "Speaker",
                    "is_active": False,
                },
                {
                    "id": "speaker-two",
                    "name": "Living room speaker",
                    "type": "Speaker",
                    "is_active": True,
                },
            ]
        }

        reproduction._handle_play_pause(
            self.client, {**self.playback, "is_playing": False}
        )

        self.spotify.devices.assert_not_called()
        self.spotify.transfer_playback.assert_called_once_with(
            "expired-device-id", force_play=False
        )
        self.spotify.start_playback.assert_not_called()
        self.streamlit.error.assert_not_called()

    def test_stale_playing_view_resumes_instead_of_pausing(self):
        """Use live paused state to resume playback even when the rendered view still appears to be playing."""
        self.session_state[reproduction.PAUSED_PLAYBACK_KEY] = {
            **self.playback, "paused_at": reproduction.time.monotonic()
        }
        self.spotify.current_playback.return_value = {
            **self.playback, "is_playing": False
        }
        self.click_play_pause(self.playback)
        self.spotify.pause_playback.assert_not_called()
        self.spotify.start_playback.assert_called_once_with(
            device_id="original-device", uris=None, position_ms=None
        )

    def test_missing_item_keeps_saved_cover(self):
        """Preserve the saved track and artwork as paused when Spotify returns no current item."""
        self.session_state[reproduction.PAUSED_PLAYBACK_KEY] = self.playback
        self.spotify.current_playback.return_value = {
            "item": None, "is_playing": False
        }
        paused = reproduction._get_playback(self.client)
        self.assertEqual(paused["item"], self.track)
        self.assertFalse(paused["is_playing"])
        self.assertEqual(reproduction._get_playback(self.client), paused)

    def test_next_discards_saved_paused_track(self):
        """Verify skipping clears paused snapshots and playback cache so the old track cannot reappear."""
        self.spotify.current_playback.return_value = self.playback
        self.session_state[reproduction.PAUSED_PLAYBACK_KEY] = self.playback
        self.session_state[reproduction.PLAYBACK_CACHE_KEY] = {
            "playback": self.playback,
            "fetched_at": reproduction.time.monotonic(),
        }
        self.streamlit.button.side_effect = lambda *args, **kwargs: (
            kwargs.get("key") == "reproduction_next"
        )

        with self.assertRaises(RerunRequested):
            reproduction._render_playback_controls(
                client=self.client,
                playback=self.playback,
                is_playing=True,
                track_id="track-id",
                is_track_liked=False,
                track_name="Song",
                artists_text="Artist",
                previous_track={},
                next_track={},
            )

        self.spotify.next_track.assert_called_once_with(device_id="original-device")
        self.assertNotIn(reproduction.PAUSED_PLAYBACK_KEY, self.session_state)
        self.assertNotIn(reproduction.PLAYBACK_CACHE_KEY, self.session_state)
        self.spotify.current_playback.return_value = None
        self.assertEqual(reproduction._get_playback(self.client), {})

    def test_next_reactivates_original_device_after_idle(self):
        """Reactivate the original idle device and start the queued track instead of issuing next_track."""
        self.session_state[reproduction.PAUSED_PLAYBACK_KEY] = {
            **self.playback,
            "paused_at": reproduction.time.monotonic() - 120,
        }
        self.spotify.queue.return_value = {
            "queue": [{"id": "next-id", "uri": "spotify:track:next-id"}]
        }
        self.spotify.devices.return_value["devices"][0]["is_active"] = False
        self.streamlit.button.side_effect = lambda *args, **kwargs: (
            kwargs.get("key") == "reproduction_next"
        )

        with self.assertRaises(RerunRequested):
            reproduction._render_playback_controls(
                client=self.client,
                playback={**self.playback, "is_playing": False},
                is_playing=False,
                track_id="track-id",
                is_track_liked=False,
                track_name="Song",
                artists_text="Artist",
                previous_track={},
                next_track={"id": "next-id", "uri": "spotify:track:next-id"},
            )

        self.spotify.transfer_playback.assert_called_once_with(
            "original-device", force_play=False
        )
        self.spotify.start_playback.assert_called_once_with(
            device_id="original-device",
            uris=["spotify:track:next-id"],
            position_ms=None,
        )
        self.spotify.next_track.assert_not_called()

    def test_next_after_long_pause_with_stale_live_item(self):
        """Start the next queued URI after a long pause even when Spotify still reports the paused track."""
        self.session_state[reproduction.PAUSED_PLAYBACK_KEY] = {
            **self.playback,
            "paused_at": reproduction.time.monotonic() - 120,
        }
        self.spotify.queue.return_value = {
            "queue": [{"id": "next-id", "uri": "spotify:track:next-id"}]
        }
        self.spotify.current_playback.return_value = {
            **self.playback, "is_playing": False
        }
        self.streamlit.button.side_effect = lambda *args, **kwargs: (
            kwargs.get("key") == "reproduction_next"
        )

        with self.assertRaises(RerunRequested):
            reproduction._render_playback_controls(
                client=self.client,
                playback={**self.playback, "is_playing": False},
                is_playing=False,
                track_id="track-id",
                is_track_liked=False,
                track_name="Song",
                artists_text="Artist",
                previous_track={},
                next_track={"id": "next-id", "uri": "spotify:track:next-id"},
            )

        self.spotify.start_playback.assert_called_once_with(
            device_id="original-device",
            uris=["spotify:track:next-id"],
            position_ms=None,
        )
        self.spotify.next_track.assert_not_called()

    def test_next_without_queued_song_does_not_restart_current_track(self):
        """Verify an empty queue after a long pause neither restarts the current track nor sends next_track."""
        self.session_state[reproduction.PAUSED_PLAYBACK_KEY] = {
            **self.playback,
            "paused_at": reproduction.time.monotonic() - 120,
        }
        self.spotify.queue.return_value = {"queue": []}
        self.streamlit.button.side_effect = lambda *args, **kwargs: (
            kwargs.get("key") == "reproduction_next"
        )

        reproduction._render_playback_controls(
            client=self.client,
            playback={**self.playback, "is_playing": False},
            is_playing=False,
            track_id="track-id",
            is_track_liked=False,
            track_name="Song",
            artists_text="Artist",
            previous_track={},
            next_track={},
        )

        self.spotify.start_playback.assert_not_called()
        self.spotify.next_track.assert_not_called()
        self.streamlit.error.assert_not_called()

    def test_next_after_pause_does_not_use_stale_card_when_queue_unavailable(self):
        """Ignore a stale next-track card when no queue is available after a long pause."""
        self.session_state[reproduction.PAUSED_PLAYBACK_KEY] = {
            **self.playback,
            "paused_at": reproduction.time.monotonic() - 120,
        }
        self.streamlit.button.side_effect = lambda *args, **kwargs: (
            kwargs.get("key") == "reproduction_next"
        )

        reproduction._render_playback_controls(
            client=self.client,
            playback={**self.playback, "is_playing": False},
            is_playing=False,
            track_id="track-id",
            is_track_liked=False,
            track_name="Song",
            artists_text="Artist",
            previous_track={},
            next_track={"id": "stale-id", "uri": "spotify:track:stale-id"},
        )

        self.spotify.start_playback.assert_not_called()
        self.spotify.next_track.assert_not_called()
        self.streamlit.error.assert_not_called()

    def test_next_after_pause_uses_live_queue_instead_of_stale_card(self):
        """Choose the live queue's next track instead of the stale track displayed on the card."""
        self.session_state[reproduction.PAUSED_PLAYBACK_KEY] = {
            **self.playback,
            "paused_at": reproduction.time.monotonic() - 120,
        }
        self.spotify.current_playback.return_value = {
            **self.playback, "is_playing": False
        }
        self.spotify.queue.return_value = {
            "currently_playing": self.track,
            "queue": [{"id": "next-id", "uri": "spotify:track:next-id"}],
        }
        self.streamlit.button.side_effect = lambda *args, **kwargs: (
            kwargs.get("key") == "reproduction_next"
        )

        with self.assertRaises(RerunRequested):
            reproduction._render_playback_controls(
                client=self.client,
                playback={**self.playback, "is_playing": False},
                is_playing=False,
                track_id="track-id",
                is_track_liked=False,
                track_name="Song",
                artists_text="Artist",
                previous_track={},
                next_track=self.track,
            )

        self.spotify.start_playback.assert_called_once_with(
            device_id="original-device",
            uris=["spotify:track:next-id"],
            position_ms=None,
        )

    def test_next_after_long_pause_restores_remaining_queue(self):
        """Verify skipping after a long pause restores all remaining queue URIs in order without a saved position."""
        self.session_state[reproduction.PAUSED_PLAYBACK_KEY] = {
            **self.playback,
            "paused_at": reproduction.time.monotonic() - 120,
            "queue_uris": ["spotify:track:next-id", "spotify:track:later-id"],
        }
        self.spotify.queue.return_value = {
            "currently_playing": self.track,
            "queue": [
                {"id": "next-id", "uri": "spotify:track:next-id"},
                {"id": "later-id", "uri": "spotify:track:later-id"},
            ],
        }
        self.streamlit.button.side_effect = lambda *args, **kwargs: (
            kwargs.get("key") == "reproduction_next"
        )

        with self.assertRaises(RerunRequested):
            reproduction._render_playback_controls(
                client=self.client,
                playback={**self.playback, "is_playing": False},
                is_playing=False,
                track_id="track-id",
                is_track_liked=False,
                track_name="Song",
                artists_text="Artist",
                previous_track={},
                next_track={"id": "next-id", "uri": "spotify:track:next-id"},
            )

        self.spotify.start_playback.assert_called_once_with(
            device_id="original-device",
            uris=["spotify:track:next-id", "spotify:track:later-id"],
            position_ms=None,
        )

    def test_next_after_long_pause_restores_playlist_context(self):
        """Restore the saved playlist context at the next track without applying the previous track's progress."""
        self.session_state[reproduction.PAUSED_PLAYBACK_KEY] = {
            **self.playback,
            "context": {"uri": "spotify:playlist:playlist-id"},
            "paused_at": reproduction.time.monotonic() - 120,
        }
        self.spotify.queue.return_value = {
            "currently_playing": self.track,
            "queue": [{"id": "next-id", "uri": "spotify:track:next-id"}],
        }
        self.streamlit.button.side_effect = lambda *args, **kwargs: (
            kwargs.get("key") == "reproduction_next"
        )

        with self.assertRaises(RerunRequested):
            reproduction._render_playback_controls(
                client=self.client,
                playback={**self.playback, "is_playing": False},
                is_playing=False,
                track_id="track-id",
                is_track_liked=False,
                track_name="Song",
                artists_text="Artist",
                previous_track={},
                next_track={"id": "next-id", "uri": "spotify:track:next-id"},
            )

        self.spotify.start_playback.assert_called_once_with(
            device_id="original-device",
            uris=None,
            context_uri="spotify:playlist:playlist-id",
            offset={"uri": "spotify:track:next-id"},
            position_ms=None,
        )

    def test_adjacent_next_track_refreshes_after_long_pause(self):
        """Refresh the adjacent next track from Spotify when its cached queue is stale after a long pause."""
        self.session_state[reproduction.ADJACENT_TRACKS_CACHE_KEY] = {
            "current_track_id": "track-id",
            "current": self.track,
            "previous": {},
            "next": {},
            "fetched_at": reproduction.time.monotonic() - 120,
        }
        self.spotify.queue.return_value = {
            "queue": [{"id": "next-id", "uri": "spotify:track:next-id"}]
        }

        _, next_track = reproduction._get_adjacent_tracks(self.client, self.track)

        self.assertEqual(next_track["uri"], "spotify:track:next-id")

    def test_adjacent_queue_is_not_refreshed_before_thirty_seconds(self):
        """Verify adjacent queue polling waits 30 seconds and fetches once when that threshold is reached."""
        self.session_state[reproduction.ADJACENT_TRACKS_CACHE_KEY] = {
            "current_track_id": "track-id",
            "current": self.track,
            "previous": {},
            "next": {},
            "fetched_at": 100,
        }

        clock = Mock()
        clock.monotonic.return_value = 120
        with patch.object(reproduction, "time", clock):
            reproduction._get_adjacent_tracks(self.client, self.track)
        self.spotify.queue.assert_not_called()

        self.spotify.queue.return_value = {
            "queue": [{"id": "next-id", "uri": "spotify:track:next-id"}]
        }
        clock = Mock()
        clock.monotonic.side_effect = [130, 130]
        with patch.object(reproduction, "time", clock):
            reproduction._get_adjacent_tracks(self.client, self.track)
        self.spotify.queue.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
