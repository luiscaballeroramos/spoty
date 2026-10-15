import io
import sys
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from types import ModuleType
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
        """Register a repeated Spotify event only once using its UTC timestamp."""
        class MemoryDatabase:
            def __init__(self, _database_url):
                self.events = set()

            def get_listening_event_keys(self, track_ids, min_played_at, max_played_at):
                return {
                    event
                    for event in self.events
                    if event[0] in track_ids
                    and min_played_at <= event[1] <= max_played_at
                }

            def insert(self, table, data, **_kwargs):
                if table != "listening_events":
                    return False
                event = (data["track_id"], data["played_at"])
                if event in self.events:
                    return False
                self.events.add(event)
                return True

        database = MemoryDatabase("unused")
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
        fake_config = ModuleType("config")
        fake_config.DBNAME = "unused"
        fake_config.VERBOSE = False
        fake_db = ModuleType("register.db")
        fake_db.SimpleDB = lambda _database_url: database
        fake_spotify = ModuleType("spotifyapi.spotifyclient")
        fake_spotify.SpotifyClient = lambda: spotify

        with patch.dict(sys.modules, {
            "config": fake_config,
            "register.db": fake_db,
            "spotifyapi.spotifyclient": fake_spotify,
        }):
            import _registration as registration

        with (
            patch.object(registration, "SimpleDB", return_value=database),
            patch.object(registration, "SpotifyClient", return_value=spotify),
            redirect_stdout(io.StringIO()),
        ):
            first = registration.register_listeningevents()
            second = registration.register_listeningevents()

        self.assertEqual(first["inserted_listening_events"], 1)
        self.assertEqual(second["inserted_listening_events"], 0)
        self.assertEqual(second["skipped_existing_listening_events"], 1)
        self.assertEqual(database.events, {("track-id", 1735732800)})

    def test_shared_mapper_handles_optional_fields_without_update_operations(self):
        """Map optional track data and insert related models without updates."""
        with patch.dict(sys.modules, {"register.db": MagicMock()}):
            from register.spotify_data import models_from_spotify_track

        spotify_track = {
            "id": "track-id",
            "name": "Track",
            "duration_ms": 180000,
            "track_number": 2,
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
