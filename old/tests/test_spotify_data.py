import sys
import csv
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch


class SpotifyDataMappingTest(unittest.TestCase):
    def test_spotify_timestamps_normalize_z_and_offsets_to_utc(self):
        """Normalize UTC and offset timestamps and reject naive values."""
        with patch.dict(sys.modules, {"register.db": MagicMock()}):
            from register.spotify_data import parse_spotify_timestamp

        utc_value = parse_spotify_timestamp("2025-01-01T12:00:00Z")
        offset_value = parse_spotify_timestamp("2025-01-01T14:00:00+02:00")

        self.assertEqual(utc_value, offset_value)
        self.assertEqual(utc_value.tzinfo, timezone.utc)
        self.assertEqual(int(utc_value.timestamp()), 1735732800)
        with self.assertRaises(ValueError):
            parse_spotify_timestamp("2025-01-01T12:00:00")

    def test_listening_event_rejects_naive_datetime(self):
        """Reject naive event timestamps without writing to the database."""
        with patch.dict(sys.modules, {"register.db": MagicMock()}):
            from register.listeningevent import ListeningEvent

        database = Mock()
        event = ListeningEvent("track-id", datetime(2025, 1, 1), None)

        with self.assertRaises(ValueError):
            event.save(database)
        database.insert.assert_not_called()

    def test_repeated_registration_keeps_one_utc_event(self):
        """The shared registration use case keeps one UTC event in the local CSV."""
        track = {
            "id": "track-id",
            "name": "Track",
            "duration_ms": 180000,
            "track_number": 1,
            "explicit": False,
            "artists": [{"id": "artist-id", "name": "Artist"}],
            "album": {
                "id": "album-id",
                "name": "Album",
                "total_tracks": 1,
                "artists": [{"id": "artist-id", "name": "Artist"}],
                "images": [],
            },
        }
        spotify = Mock()
        spotify.get_recently_played.return_value = {
            "items": [{"track": track, "played_at": "2025-01-01T12:00:00Z"}]
        }
        class Model:
            def save(self, _database, print_only_insert=False):
                return False

        class Event:
            def __init__(self, track_id, played_at, context_uri):
                self.track_id = track_id
                self.played_at = played_at

            def save(self, memory_database, print_only_insert=False):
                return memory_database.insert(
                    "listening_events",
                    {
                        "track_id": self.track_id,
                        "played_at": int(self.played_at.timestamp()),
                    },
                )

        spotify_data = MagicMock()
        spotify_data.parse_spotify_timestamp.side_effect = lambda value: datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )
        spotify_data.models_from_spotify_track.return_value = (
            Model(), Model(), []
        )
        listening_event = MagicMock()
        listening_event.ListeningEvent = Event

        database_factory = Mock()
        with tempfile.TemporaryDirectory() as temporary:
            csv_path = Path(temporary) / "events.csv"
            with (
                patch.dict(sys.modules, {
                    "register.listeningevent": listening_event,
                    "register.spotify_data": spotify_data,
                }),
                patch(
                    "services.listening_event_csv.default_listening_events_csv_path",
                    return_value=csv_path,
                ),
            ):
                from services.synchronization import SynchronizationService

                service = SynchronizationService(
                    database_url="unused",
                    spotify_factory=lambda: spotify,
                    database_factory=database_factory,
                    sleep=lambda _seconds: None,
                    delay=lambda _low, _high: 0,
                )
                first = service.sync_listening_events()
                second = service.sync_listening_events()
            with csv_path.open("r", encoding="utf-8", newline="") as csv_file:
                rows = list(csv.DictReader(csv_file))

        self.assertEqual(first.status.value, "completed", first.message)
        self.assertEqual(first.counters["saved_local_events"], 1)
        self.assertEqual(second.counters["saved_local_events"], 0)
        self.assertEqual(second.counters["skipped_existing_listening_events"], 1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["track_id"], "track-id")
        self.assertEqual(rows[0]["played_at"], "1735732800")
        database_factory.assert_not_called()

    def test_shared_mapper_handles_optional_fields_without_update_operations(self):
        """Map optional track data and insert related models without updates."""
        with patch.dict(sys.modules, {"register.db": MagicMock()}):
            from register.spotify_data import models_from_spotify_track

        spotify_track = {
            "id": "track-id",
            "name": "Track",
            "duration_ms": 180000,
            "track_number": 2,
            "disc_number": 3,
            "artists": [{"id": "track-artist", "name": "Track Artist"}],
            "album": {
                "id": "album-id",
                "name": "Album",
                "total_tracks": 8,
                "artists": [{"id": "album-artist", "name": "Album Artist"}],
            },
        }

        track, album, artists = models_from_spotify_track(spotify_track)

        self.assertEqual(track.id, "track-id")
        self.assertEqual(track.popularity, None)
        self.assertEqual(track.disc_number, 3)
        self.assertEqual(track.artists_ids, ["track-artist"])
        self.assertEqual(album.artists, ["album-artist"])
        self.assertEqual(album.images, [])
        self.assertIsNone(album.release_year)
        self.assertEqual([artist.id for artist in artists], [
            "track-artist", "album-artist"
        ])

        database = Mock()
        track.save(database)
        album.save(database)
        for artist in artists:
            artist.save(database)
        self.assertEqual(database.insert.call_count, 4)
        database.edit.assert_not_called()
