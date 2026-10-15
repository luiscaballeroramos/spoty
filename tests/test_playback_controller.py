import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from services.playback import PlaybackController, PlaybackSnapshotStore


class PlaybackControllerTest(unittest.TestCase):
    def test_snapshot_round_trip_reconstructs_monotonic_pause_time(self):
        """Verify Snapshot round trip reconstructs monotonic pause time."""
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "playback.json"
            store = PlaybackSnapshotStore(
                path, monotonic=lambda: 100, wall_time=lambda: 1000
            )
            playback = {
                "item": {"id": "track-id"},
                "is_playing": False,
                "paused_at": 90,
            }
            store.save(playback)

            restored = PlaybackSnapshotStore(
                path, monotonic=lambda: 200, wall_time=lambda: 1090
            ).load()

        self.assertEqual(restored["paused_at"], 100)
        self.assertNotIn("paused_at", json.loads(path.read_text()) if path.exists() else {})

    def test_get_playback_estimates_cached_progress_without_polling(self):
        """Verify Get playback estimates cached progress without polling."""
        client = Mock()
        state = {
            "playback_cache": {
                "playback": {
                    "is_playing": True,
                    "progress_ms": 10_000,
                    "item": {"duration_ms": 30_000},
                },
                "fetched_at": 10,
            }
        }
        controller = PlaybackController(
            client,
            state,
            Mock(),
            monotonic=lambda: 12,
            playback_cache_key="playback_cache",
            persisted_loaded_key="loaded",
        )

        result = controller.get_playback()

        self.assertEqual(result["progress_ms"], 12_000)
        client.get_current_playback.assert_not_called()

    def test_previous_action_clears_pause_and_records_requested_track(self):
        """Verify Previous action clears pause and records requested track."""
        client = Mock()
        client.previous_track.return_value = True
        store = Mock()
        state = {"paused": {"item": {"id": "old"}}, "cache": {"playback": {}}}
        controller = PlaybackController(
            client,
            state,
            store,
            playback_cache_key="cache",
            paused_playback_key="paused",
            previous_request_key="previous_request",
        )

        self.assertTrue(controller.previous_track({"id": "previous"}))

        self.assertNotIn("cache", state)
        self.assertNotIn("paused", state)
        self.assertEqual(state["previous_request"], "previous")
        store.clear.assert_called_once_with()
