import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

from streamlit_lab import renderer
from streamlit_lab.blocks import Position

with patch.dict(sys.modules, {
    "register.artist": MagicMock(),
    "services.reproduction": MagicMock(),
}):
    from app_views import interface_layout
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
        self.layout_streamlit_patch = patch.object(
            interface_layout, "st", self.streamlit
        )
        self.layout_streamlit_patch.start()
        self.addCleanup(self.layout_streamlit_patch.stop)
        self.renderer_streamlit_patch = patch.object(
            renderer, "st", self.streamlit
        )
        self.renderer_streamlit_patch.start()
        self.addCleanup(self.renderer_streamlit_patch.stop)

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

    def test_track_blocks_use_automatic_responsive_layout(self):
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
            self.assertAlmostEqual(image.height.value / title.height.value, 4)
            self.assertEqual(image.height.min_px, 0)
            self.assertEqual(title.height.min_px, 0)
            self.assertAlmostEqual(image.height.max_px / title.height.max_px, 4)
            self.assertEqual(image.portrait_height.unit, "dvh")
            self.assertAlmostEqual(image.portrait_height.value / title.portrait_height.value, 4)
            self.assertAlmostEqual(image.portrait_height.value + title.portrait_height.value, 100 * weight / 4)
            self.assertAlmostEqual(image.portrait_height.max_px + title.portrait_height.max_px, 720 * weight / 4)
            self.assertAlmostEqual(image.portrait_height.offset_px + title.portrait_height.offset_px, 64 * weight / 4)

    def test_controls_use_the_shared_block_style_for_each_button(self):
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
        self.assertEqual(
            set(style_by_key),
            {
                "reproduction_previous",
                "reproduction_play_pause",
                "reproduction_corner_action",
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
            self.assertIsNotNone(style_by_key[key].portrait)
        self.assertEqual(
            style_by_key["reproduction_play_pause"].portrait.width,
            "max(2.6rem, min(75vw, calc((100dvh - 11rem) / 2)))",
        )
        self.assertEqual(
            style_by_key["reproduction_play_pause"].background_image,
            "https://example.com/cover.jpg",
        )
        self.assertIs(
            style_by_key["reproduction_corner_action"],
            reproduction.REPRODUCTION_CORNER_ACTION_STYLE,
        )
        self.assertEqual(
            style_by_key["reproduction_corner_action"].aspect_ratio, "1 / 1"
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

    def test_resume_restores_context_when_spotify_loses_it(self):
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

    def test_playback_poll_persists_track_for_a_new_session(self):
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

    def test_resume_recognizes_original_device_with_replaced_id(self):
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
        self.spotify.devices.return_value = {
            "devices": [
                {
                    "id": "renewed-device-id",
                    "name": "Living room speaker",
                    "type": "Speaker",
                    "is_active": True,
                }
            ]
        }

        self.click_play_pause({**self.playback, "is_playing": False})

        self.spotify.start_playback.assert_called_once_with(
            device_id="renewed-device-id",
            uris=["spotify:track:track-id"],
            position_ms=72_000,
        )
        self.spotify.transfer_playback.assert_not_called()
        self.assertEqual(
            self.session_state[reproduction.PAUSED_PLAYBACK_KEY]["device"]["id"],
            "renewed-device-id",
        )

    def test_resume_does_not_guess_between_duplicate_device_matches(self):
        self.session_state[reproduction.PAUSED_PLAYBACK_KEY] = {
            **self.playback,
            "device": {
                "id": "expired-device-id",
                "name": "Living room speaker",
                "type": "Speaker",
            },
            "paused_at": reproduction.time.monotonic() - 120,
        }
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

        self.spotify.start_playback.assert_not_called()
        self.spotify.transfer_playback.assert_not_called()
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
