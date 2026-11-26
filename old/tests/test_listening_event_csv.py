import csv
import json
import os
import tempfile
import unittest
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from services.listening_event_capture import (
    LAST_LISTENING_EVENT_SYNC_KEY,
    capture_listening_events_if_due,
)
from services.listening_event_csv import CSV_FIELDS, ListeningEventCsvStore
from services.listening_event_import import ListeningEventCsvImporter
from services.synchronization import SyncResult, SyncStatus, SynchronizationService


PLAYED_AT = int(datetime(2026, 10, 22, 7, tzinfo=timezone.utc).timestamp())


def spotify_item(track_id="track-id", played_at="2026-10-22T07:00:00Z"):
    artist = {"id": "artist-id", "name": "Artist, With Comma"}
    return {
        "played_at": played_at,
        "context": {"uri": "spotify:playlist:playlist-id"},
        "track": {
            "id": track_id,
            "name": 'Track "Title", With Comma',
            "duration_ms": 210_000,
            "track_number": 2,
            "disc_number": 1,
            "artists": [artist],
            "explicit": False,
            "popularity": 42,
            "album": {
                "id": "album-id",
                "name": "Album",
                "artists": [artist],
                "total_tracks": 8,
                "images": [{"url": "https://example.test/cover.jpg"}],
                "release_date": "2020-01-02",
                "release_date_precision": "day",
            },
        },
    }


class ListeningEventCsvTest(unittest.TestCase):
    def test_default_csv_path_is_inside_repository_data_directory(self):
        repository_root = Path(__file__).resolve().parents[1]
        with patch.dict(os.environ, {"SPOTY_LISTENING_EVENTS_CSV": ""}):
            path = ListeningEventCsvStore().path

        self.assertEqual(path, repository_root / "data" / "listening_events.csv")

    def test_csv_append_preserves_payload_and_deduplicates_by_database_key(self):
        item = spotify_item()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "listening_events.csv"
            store = ListeningEventCsvStore(path)

            self.assertEqual(
                store.append_events([
                    (item, "track-id", PLAYED_AT),
                    (item, "track-id", PLAYED_AT),
                ]),
                (1, 1),
            )
            self.assertEqual(
                store.append_events([(item, "track-id", PLAYED_AT)]),
                (0, 1),
            )

            with path.open("r", encoding="utf-8", newline="") as csv_file:
                reader = csv.DictReader(csv_file)
                rows = list(reader)

        self.assertEqual(tuple(reader.fieldnames), CSV_FIELDS)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["context_source"], "spotify")
        self.assertEqual(rows[0]["context_type"], "playlist")
        saved_item = json.loads(rows[0]["spotify_item_json"])
        self.assertEqual(saved_item["track"]["name"], 'Track "Title", With Comma')
        self.assertEqual(
            saved_item["track"]["artists"][0]["name"], "Artist, With Comma"
        )

    def test_sync_saves_events_locally_without_opening_neon(self):
        item = spotify_item()
        spotify = Mock()
        spotify.get_recently_played.return_value = {"items": [item]}
        database_factory = Mock()
        service = SynchronizationService(
            spotify_factory=lambda: spotify,
            database_factory=database_factory,
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "listening_events.csv"
            with patch(
                "services.listening_event_csv.default_listening_events_csv_path",
                return_value=path,
            ):
                first_result = service.sync_listening_events()
                second_result = service.sync_listening_events()

            self.assertEqual(first_result.status, SyncStatus.COMPLETED)
            self.assertEqual(first_result.counters["saved_local_events"], 1)
            self.assertEqual(second_result.counters["saved_local_events"], 0)
            self.assertEqual(
                second_result.counters["skipped_existing_listening_events"], 1
            )
            database_factory.assert_not_called()
            with path.open("r", encoding="utf-8", newline="") as csv_file:
                self.assertEqual(len(list(csv.DictReader(csv_file))), 1)

    def test_import_dry_run_validates_without_opening_database(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "listening_events.csv"
            item = spotify_item()
            ListeningEventCsvStore(path).append_events([
                (item, "track-id", PLAYED_AT)
            ])
            database_factory = Mock()

            result = ListeningEventCsvImporter(
                path, database_factory=database_factory
            ).run()

        self.assertEqual(result["status"], "validated")
        self.assertEqual(result["validated_events"], 1)
        database_factory.assert_not_called()

    def test_import_is_idempotent_and_preserves_csv(self):
        class Database:
            def __init__(self):
                self.rows = {table: set() for table in (
                    "artists", "albums", "tracks", "listening_events"
                )}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def transaction(self):
                return nullcontext()

            def insert(self, table, data, **_kwargs):
                key = (
                    (data["track_id"], data["played_at"])
                    if table == "listening_events"
                    else data["id"]
                )
                if key in self.rows[table]:
                    return False
                self.rows[table].add(key)
                return True

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "listening_events.csv"
            ListeningEventCsvStore(path).append_events([
                (spotify_item(), "track-id", PLAYED_AT)
            ])
            database = Database()
            importer = ListeningEventCsvImporter(
                path, database_factory=lambda _url: database
            )
            with patch("config.require_database_url", return_value="unused"):
                first_result = importer.run(apply=True)
                second_result = importer.run(apply=True)

            self.assertEqual(first_result["inserted_events"], 1)
            self.assertEqual(second_result["inserted_events"], 0)
            self.assertEqual(second_result["skipped_existing_events"], 1)
            self.assertTrue(path.exists())
            self.assertEqual(len(database.rows["tracks"]), 1)
            self.assertEqual(len(database.rows["albums"]), 1)
            self.assertEqual(len(database.rows["artists"]), 1)
            self.assertEqual(len(database.rows["listening_events"]), 1)

    def test_scheduler_runs_immediately_then_waits_five_minutes(self):
        state = {}
        sync = Mock(return_value=SyncResult(
            "listening_events", SyncStatus.COMPLETED
        ))

        self.assertIsNotNone(
            capture_listening_events_if_due(state, now=10, sync=sync)
        )
        self.assertEqual(
            state[LAST_LISTENING_EVENT_SYNC_KEY], 10
        )
        self.assertIsNone(
            capture_listening_events_if_due(state, now=309, sync=sync)
        )
        self.assertIsNotNone(
            capture_listening_events_if_due(state, now=310, sync=sync)
        )
        self.assertEqual(sync.call_count, 2)
