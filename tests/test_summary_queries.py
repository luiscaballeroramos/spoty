import unittest
from unittest.mock import MagicMock, patch

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
        return (
            SummaryQueryService("postgresql://test.neon.tech/spoty", connect),
            connect,
            cursor,
        )

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

    def test_non_neon_database_url_fails_before_connecting(self):
        """Reject an unsafe database URL at query time without opening a connection."""
        service, connect, _ = self._service([])
        service._database_url = "postgresql://localhost/spoty"

        with self.assertRaisesRegex(ValueError, "Neon PostgreSQL URL"):
            service.library_counts()

        connect.assert_not_called()

    def test_missing_database_url_is_rejected_when_query_runs(self):
        """Defer missing database configuration errors until the first query."""
        connect = MagicMock()
        service = SummaryQueryService(connect_factory=connect)

        with patch.dict("os.environ", {"DATABASE_URL": ""}):
            with self.assertRaisesRegex(ValueError, "Missing DATABASE_URL"):
                service.library_counts()

        connect.assert_not_called()
