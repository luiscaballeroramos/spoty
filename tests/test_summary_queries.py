import unittest
from unittest.mock import MagicMock

from services.summary_queries import SummaryQueryService


class SummaryQueryServiceTest(unittest.TestCase):
    def _service(self, rows):
        connection = MagicMock()
        connection.__enter__.return_value = connection
        cursor = MagicMock()
        cursor.__enter__.return_value = cursor
        cursor.fetchall.return_value = rows
        connection.cursor.return_value = cursor
        connect = MagicMock(return_value=connection)
        return SummaryQueryService("unused", connect), connect, cursor

    def test_dashboard_signature_maps_one_query_result(self):
        """Verify Dashboard signature maps one query result."""
        service, connect, cursor = self._service([{
            "latest_track_id": "track",
            "latest_played_at": "1735732800",
            "total_tracks": 4,
            "total_artists": 3,
            "total_albums": 2,
            "total_liked_tracks": 1,
            "total_listening_events": 9,
        }])

        signature = service.dashboard_signature()

        self.assertEqual(signature, ("track", 1735732800, 4, 3, 2, 1, 9))
        connect.assert_called_once()
        cursor.execute.assert_called_once()

    def test_recent_history_uses_parameterized_limit(self):
        """Verify Recent history uses parameterized limit."""
        service, _, cursor = self._service([])

        self.assertEqual(service.recent_listening_events(limit=25), [])

        query, parameters = cursor.execute.call_args.args
        self.assertIn("LIMIT %s", query)
        self.assertEqual(parameters, (25,))

    def test_artist_names_use_array_parameter_and_skip_empty_query(self):
        """Verify Artist names use array parameter and skip empty query."""
        service, _, cursor = self._service([{"id": "artist", "name": "Artist"}])

        self.assertEqual(service.artist_names(["artist", "artist"]), {"artist": "Artist"})
        self.assertEqual(cursor.execute.call_args.args[1], (["artist"],))
        cursor.reset_mock()
        self.assertEqual(service.artist_names([]), {})
        cursor.execute.assert_not_called()
