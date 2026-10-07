import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

with patch.dict(sys.modules, {
    "register.artist": MagicMock(),
    "services.reproduction": MagicMock(),
}):
    from app_views import reproduction
    from spotifyapi.spotifyclient import SpotifyClient


class RerunRequested(Exception):
    pass


class ReproductionTest(unittest.TestCase):
    def setUp(self):
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

        self.spotify = MagicMock()
        self.spotify.current_playback.return_value = None
        self.spotify.queue.return_value = None
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

    def test_pause_then_resume_after_spotify_loses_playback(self):
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

        self.session_state.pop(reproduction.PLAYBACK_FORCE_REFRESH_UNTIL_KEY, None)
        self.assertEqual(reproduction._get_playback(self.client), paused)
        self.spotify.devices.return_value["devices"][0]["is_active"] = False
        self.click_play_pause(paused)
        self.spotify.transfer_playback.assert_called_once_with(
            "original-device", force_play=False
        )
        self.spotify.start_playback.assert_called_once_with(
            device_id="original-device",
            uris=["spotify:track:track-id"],
            position_ms=72_000,
        )

    def test_resume_preserves_queue_when_paused_track_is_still_available(self):
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

    def test_long_pause_restores_saved_track_and_position(self):
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

    def test_inactive_device_restores_track_even_when_paused_item_is_reported(self):
        self.session_state[reproduction.PAUSED_PLAYBACK_KEY] = self.playback
        paused = {**self.playback, "is_playing": False}
        self.spotify.current_playback.return_value = paused
        self.session_state[reproduction.PLAYBACK_CACHE_KEY] = {
            "playback": paused,
            "fetched_at": 0,
        }
        self.spotify.devices.return_value["devices"][0]["is_active"] = False
        self.click_play_pause(paused)
        self.spotify.transfer_playback.assert_called_once_with(
            "original-device", force_play=False
        )
        self.spotify.start_playback.assert_called_once_with(
            device_id="original-device",
            uris=["spotify:track:track-id"],
            position_ms=72_000,
        )

    def test_missing_original_device_does_not_play_elsewhere(self):
        self.session_state[reproduction.PAUSED_PLAYBACK_KEY] = self.playback
        self.spotify.devices.return_value = {"devices": []}
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
        self.streamlit.error.assert_called_once()

    def test_stale_playing_view_resumes_instead_of_pausing(self):
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
        self.session_state[reproduction.PAUSED_PLAYBACK_KEY] = self.playback
        self.spotify.current_playback.return_value = {
            "item": None, "is_playing": False
        }
        paused = reproduction._get_playback(self.client)
        self.assertEqual(paused["item"], self.track)
        self.assertFalse(paused["is_playing"])
        self.assertEqual(reproduction._get_playback(self.client), paused)

    def test_next_discards_saved_paused_track(self):
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
        self.streamlit.error.assert_called_once()

    def test_next_after_pause_does_not_use_stale_card_when_queue_unavailable(self):
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
        self.streamlit.error.assert_called_once()

    def test_next_after_pause_uses_live_queue_instead_of_stale_card(self):
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


if __name__ == "__main__":
    unittest.main()
