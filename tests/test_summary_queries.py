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

    def test_top_listened_genres_counts_full_listens_and_returns_ten(self):
        """Count full listens per distinct genre with overlapping shares and ten results."""
        service, _, cursor = self._service([
            {
                "total_listens": 100,
                "classified_listens": 60,
                "listened_tracks": 20,
                "classified_tracks": 12,
                "genre": "retro soul",
                "weighted_plays": 60,
            },
            {
                "total_listens": 100,
                "classified_listens": 60,
                "listened_tracks": 20,
                "classified_tracks": 12,
                "genre": "jazz",
                "weighted_plays": 45,
            },
        ])

        result = service.top_listened_genres()

        self.assertEqual(result["classified_listens"], 60)
        self.assertEqual(result["total_listens"], 100)
        self.assertEqual(result["classified_tracks"], 12)
        self.assertEqual(result["genres"][0]["genre"], "retro soul")
        self.assertEqual(result["genres"][0]["weighted_listens"], 60)
        self.assertEqual(result["genres"][0]["share_percent"], 100)
        self.assertEqual(result["genres"][1]["share_percent"], 75)
        query, parameters = cursor.execute.call_args.args
        self.assertIn("SELECT DISTINCT ta.track_id, lower(btrim(genre.value))", query)
        self.assertIn("SUM(et.plays) AS weighted_plays", query)
        self.assertNotIn("et.plays / gc.genre_count", query)
        self.assertEqual(parameters, (10,))

    def test_genre_families_deduplicate_tracks_and_traverse_all_parents(self):
        """Resolve approved labels to root families without dividing listens."""
        service, _, cursor = self._service([])
        self.assertEqual(service.top_listened_genres(grouping="families")["genres"], [])
        query, parameters = cursor.execute.call_args.args
        self.assertIn("WITH RECURSIVE", query)
        self.assertIn("SELECT DISTINCT original.track_id, family.id", query)
        self.assertIn("mapping.status = 'approved'", query)
        self.assertIn("mapping.source = 'smarterplaylists'", query)
        self.assertIn("relation.child_id = family.id", query)
        self.assertEqual(parameters, (10,))
        with self.assertRaises(ValueError):
            service.top_listened_genres(grouping="unsupported")

    def test_metadata_overview_maps_sources_and_fields(self):
        """Keep source coverage separate and preserve zero-valued field counts."""
        service, connect, cursor = self._service([
            {"total": 10, "enriched": 4, "source": "SmarterPlaylists", "source_available": 4,
             "field": "danceability", "field_available": 3},
            {"total": 10, "enriched": 4, "source": "Spotify", "source_available": 10,
             "field": "popularity", "field_available": 10},
        ])

        result = service.metadata_overview("tracks")

        self.assertEqual(result["total"], 10)
        self.assertEqual(result["enriched"], 4)
        sources = {source["source"]: source for source in result["sources"]}
        self.assertEqual(sources["SmarterPlaylists"]["fields"], [{"field": "danceability", "available": 3}])
        self.assertEqual(sources["Last.fm"]["available"], 0)
        query = cursor.execute.call_args.args[0].as_string()
        self.assertIn('SELECT * FROM "tracks"', query)
        self.assertIn("COUNT(DISTINCT id)", query)
        self.assertIn("'[]'::jsonb", query)
        self.assertIn("metadata.match_status = 'found'", query)
        connect.assert_called_once()

    def test_metadata_overview_supports_artists_albums_and_empty_catalogs(self):
        """Select the matching provider table without querying track-only providers."""
        for entity in ("artists", "albums"):
            with self.subTest(entity=entity):
                service, _, cursor = self._service([])
                result = service.metadata_overview(entity)
                self.assertEqual(result["total"], 0)
                self.assertEqual(result["enriched"], 0)
                self.assertEqual(len(result["sources"]), 2)
                query = cursor.execute.call_args.args[0].as_string()
                self.assertIn(f'SELECT * FROM "{entity}"', query)
                self.assertNotIn("lastfm_track_metadata", query)

    def test_metadata_overview_rejects_unknown_entities_before_querying(self):
        """Reject arbitrary identifiers without opening a database connection."""
        service, connect, _ = self._service([])
        with self.assertRaises(ValueError):
            service.metadata_overview("tracks; DROP TABLE tracks")
        connect.assert_not_called()

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
