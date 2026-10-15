import unittest
import json
from contextlib import nullcontext
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import Mock, patch

from metadata_sources.lastfm import LastFmAPIError
from register.enrichment import MetadataRepository
from services.enrichment import EnrichmentResult, EnrichmentService


class MemoryMetadataRepository:
    def __init__(self, candidate):
        self.candidate = candidate
        self.records = {}
        self.database_open = False
        self.save_started = False

    def get_candidates(
        self,
        _database_url,
        limit,
        *,
        include_lastfm,
        include_musicbrainz,
        include_acousticbrainz,
    ):
        self.database_open = True
        try:
            if limit < 1:
                raise ValueError("Candidate limit must be positive.")
            track_id = self.candidate["spotify_track_id"]
            lastfm_pending = include_lastfm and ("lastfm", track_id) not in self.records
            musicbrainz_record = self.records.get(("musicbrainz", track_id))
            musicbrainz_pending = include_musicbrainz and musicbrainz_record is None
            acoustic_pending = include_acousticbrainz and (
                musicbrainz_record is not None
                and musicbrainz_record["match_status"] == "matched"
                and any(
                    ("acousticbrainz", track_id, level) not in self.records
                    for level in ("low-level", "high-level")
                )
            )
            if not (lastfm_pending or musicbrainz_pending or acoustic_pending):
                return []
            return [{
                **self.candidate,
                "needs_lastfm": lastfm_pending,
                "needs_musicbrainz": musicbrainz_pending,
                "needs_acousticbrainz_low_level": acoustic_pending and (
                    ("acousticbrainz", track_id, "low-level") not in self.records
                ),
                "needs_acousticbrainz_high_level": acoustic_pending and (
                    ("acousticbrainz", track_id, "high-level") not in self.records
                ),
                "recording_mbid": (
                    musicbrainz_record.get("recording_mbid")
                    if musicbrainz_record
                    else None
                ),
            }]
        finally:
            self.database_open = False

    def save_results(self, _database_url, records):
        self.database_open = True
        self.save_started = True
        inserted = 0
        try:
            for record in records:
                key = (record["provider"], record["spotify_track_id"])
                if record["provider"] == "acousticbrainz":
                    key += (record["analysis_level"],)
                if key not in self.records:
                    self.records[key] = record
                    inserted += 1
        finally:
            self.database_open = False
        return inserted


class MetadataRepositoryTest(unittest.TestCase):
    def test_candidate_query_joins_primary_artist_and_filters_pending_sources(self):
        """Read source-pending tracks and resolve the Spotify primary artist."""
        rows = [{
            "spotify_track_id": "spotify-id",
            "title": "Title",
            "duration_ms": 1000,
            "artist_name": "Artist",
            "needs_lastfm": True,
            "needs_musicbrainz": True,
            "needs_acousticbrainz_low_level": False,
            "needs_acousticbrainz_high_level": False,
            "recording_mbid": None,
        }]

        class Cursor:
            def execute(self, query, parameters):
                self.query = query
                self.parameters = parameters

            def fetchall(self):
                return rows

        class Database:
            def __init__(self):
                self.cursor = Cursor()

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        database = Database()
        repository = MetadataRepository(database_factory=lambda _url: database)

        result = repository.get_candidates(
            "unused",
            5,
            include_lastfm=True,
            include_musicbrainz=True,
            include_acousticbrainz=True,
        )

        self.assertEqual(result, rows)
        self.assertIn("t.artists_ids::jsonb ->> 0", database.cursor.query)
        self.assertIn("needs_acousticbrainz_low_level", database.cursor.query)
        self.assertIn("needs_acousticbrainz_high_level", database.cursor.query)
        self.assertEqual(database.cursor.parameters, (True, True, True, 5))

    def test_save_results_uses_source_tables_and_conflict_safe_inserts(self):
        """Persist provider records in short transactions without cross-provider mixing."""
        class Cursor:
            def __init__(self):
                self.executions = []
                self.rowcount = 1

            def execute(self, query, parameters):
                self.executions.append((query, parameters))

        class Database:
            def __init__(self):
                self.cursor = Cursor()
                self.transaction_count = 0

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def transaction(self):
                self.transaction_count += 1
                return nullcontext()

        database = Database()
        repository = MetadataRepository(database_factory=lambda _url: database)
        records = [
            {
                "provider": "lastfm",
                "spotify_track_id": "spotify-id",
                "match_status": "matched",
                "normalized_data": {"title": "Title"},
                "raw_response": {"track": {"name": "Title"}},
            },
            {
                "provider": "musicbrainz",
                "spotify_track_id": "spotify-id",
                "match_status": "not_found",
                "raw_response": {"recordings": []},
            },
            {
                "provider": "acousticbrainz",
                "spotify_track_id": "spotify-id",
                "recording_mbid": "recording-id",
                "analysis_level": "low-level",
                "match_status": "not_found",
            },
        ]

        inserted = repository.save_results("unused", records)

        self.assertEqual(inserted, 3)
        self.assertEqual(database.transaction_count, 1)
        statements = [query for query, _ in database.cursor.executions]
        self.assertIn("INSERT INTO lastfm_track_metadata", statements[0])
        self.assertIn("INSERT INTO musicbrainz_recording_metadata", statements[1])
        self.assertIn("INSERT INTO acousticbrainz_analysis_metadata", statements[2])
        self.assertTrue(all("ON CONFLICT" in query for query in statements))


class EnrichmentServiceTest(unittest.TestCase):
    def setUp(self):
        self.candidate = {
            "spotify_track_id": "spotify-track",
            "title": "Cafe del Mar",
            "duration_ms": 100000,
            "artist_name": "Artist",
        }

    def test_enrich_pending_reads_calls_providers_and_saves_idempotently(self):
        """Run the DB-provider-DB flow with raw responses and no open DB during HTTP."""
        repository = MemoryMetadataRepository(self.candidate)
        lastfm_payload = {
            "track": {
                "id": "lastfm-id",
                "mbid": "recording-id",
                "name": "Café del Mar",
                "artist": {"name": "Artist"},
                "album": {"title": "Album"},
                "duration": "100000",
                "listeners": "120",
                "playcount": "300",
                "toptags": {"tag": [{"name": "electronic"}]},
                "album": {
                    "title": "Album",
                    "image": [{"#text": "https://images.test/cover.jpg", "size": "large"}],
                },
                "wiki": {"summary": "summary"},
            }
        }
        recording = {
            "id": "recording-id",
            "title": "Cafe del Mar",
            "length": 100000,
            "first-release-date": "1993",
            "artist-credit": [{
                "name": "Artist",
                "artist": {"id": "artist-mbid", "name": "Artist"},
            }],
            "genres": [{"name": "electronic"}],
            "tags": [{"name": "chillout"}],
            "releases": [{"id": "release-id", "title": "Album", "date": "1993-06"}],
        }
        musicbrainz_payload = {"recordings": [recording]}

        class LastFm:
            def get_track(inner_self, *, artist, track):
                self.assertFalse(repository.database_open)
                self.assertFalse(repository.save_started)
                self.assertEqual((artist, track), ("Artist", "Cafe del Mar"))
                return lastfm_payload

        class MusicBrainz:
            def search_recordings(inner_self, title, artist):
                self.assertFalse(repository.database_open)
                self.assertFalse(repository.save_started)
                self.assertEqual((title, artist), ("Cafe del Mar", "Artist"))
                return musicbrainz_payload

            def get_entity(inner_self, entity, mbid, includes=None):
                self.assertFalse(repository.database_open)
                self.assertFalse(repository.save_started)
                self.assertEqual((entity, mbid), ("recording", "recording-id"))
                self.assertIn("genres", includes)
                return recording

        class AcousticBrainz:
            def get_analysis(inner_self, mbid, level):
                self.assertFalse(repository.database_open)
                self.assertFalse(repository.save_started)
                self.assertEqual(mbid, "recording-id")
                key = "lowlevel" if level == "low-level" else "highlevel"
                return {key: {
                    "danceability": {"value": 0.8},
                    "genre_dortmund": {"value": "electronic", "probability": 0.9},
                }}

        service = EnrichmentService(
            "unused",
            lastfm_api_key="test-key",
            repository=repository,
            lastfm_factory=lambda _key: LastFm(),
            musicbrainz_factory=lambda _user_agent: MusicBrainz(),
            acousticbrainz_factory=AcousticBrainz,
        )

        result = service.enrich_pending(limit=10)

        self.assertEqual(result.candidate_tracks, 1)
        self.assertEqual(result.records_saved, 4)
        self.assertEqual(result.matched, {
            "lastfm": 1,
            "musicbrainz": 1,
            "acousticbrainz": 2,
        })
        self.assertEqual(len(repository.records), 4)
        self.assertEqual(
            repository.records[("lastfm", "spotify-track")]["raw_response"],
            lastfm_payload,
        )
        self.assertEqual(
            repository.records[("musicbrainz", "spotify-track")]["recording_mbid"],
            "recording-id",
        )
        self.assertEqual(
            repository.records[("lastfm", "spotify-track")]["normalized_data"]["album_images"],
            ["https://images.test/cover.jpg"],
        )
        self.assertEqual(
            repository.records[("musicbrainz", "spotify-track")]["normalized_data"]["releases"][0]["date"],
            "1993-06",
        )
        self.assertEqual(
            repository.records[("acousticbrainz", "spotify-track", "high-level")]["normalized_data"]["highlevel"]["genre_dortmund"]["value"],
            "electronic",
        )

        second_result = service.enrich_pending(limit=10)

        self.assertEqual(second_result.candidate_tracks, 0)
        self.assertEqual(second_result.records_saved, 0)
        self.assertEqual(len(repository.records), 4)

    def test_existing_high_level_analysis_is_not_requested_again(self):
        """Skip both AcousticBrainz levels after their results are already stored."""
        repository = MemoryMetadataRepository(self.candidate)
        repository.records[("musicbrainz", "spotify-track")] = {
            "provider": "musicbrainz",
            "match_status": "matched",
            "recording_mbid": "recording-id",
        }
        repository.records[("acousticbrainz", "spotify-track", "high-level")] = {
            "provider": "acousticbrainz",
            "analysis_level": "high-level",
            "match_status": "found",
        }
        repository.records[("acousticbrainz", "spotify-track", "low-level")] = {
            "provider": "acousticbrainz",
            "analysis_level": "low-level",
            "match_status": "found",
        }
        acousticbrainz = Mock()
        service = EnrichmentService(
            "unused",
            repository=repository,
            musicbrainz_factory=lambda _user_agent: Mock(),
            acousticbrainz_factory=lambda: acousticbrainz,
        )

        result = service.enrich_pending()

        self.assertEqual(result.candidate_tracks, 0)
        self.assertEqual(result.records_saved, 0)
        acousticbrainz.get_analysis.assert_not_called()

    def test_fetches_only_the_acousticbrainz_level_missing_from_storage(self):
        """Resume a partial analysis by fetching only its missing low-level response."""
        repository = MemoryMetadataRepository(self.candidate)
        repository.records[("musicbrainz", "spotify-track")] = {
            "provider": "musicbrainz",
            "match_status": "matched",
            "recording_mbid": "recording-id",
        }
        repository.records[("acousticbrainz", "spotify-track", "high-level")] = {
            "provider": "acousticbrainz",
            "analysis_level": "high-level",
            "match_status": "found",
        }
        acousticbrainz = Mock()
        acousticbrainz.get_analysis.return_value = {
            "lowlevel": {"rhythm": {"bpm": 120}}
        }
        service = EnrichmentService(
            "unused",
            repository=repository,
            musicbrainz_factory=lambda _user_agent: Mock(),
            acousticbrainz_factory=lambda: acousticbrainz,
        )

        result = service.enrich_pending()

        self.assertEqual(result.records_saved, 1)
        acousticbrainz.get_analysis.assert_called_once_with("recording-id", "low-level")
        self.assertEqual(
            repository.records[("acousticbrainz", "spotify-track", "low-level")]
            ["normalized_data"],
            {"lowlevel": {"rhythm": {"bpm": 120}}},
        )


class EnrichmentServiceAndCliTest(unittest.TestCase):
    def setUp(self):
        self.candidate = {
            "spotify_track_id": "spotify-track",
            "title": "Cafe del Mar",
            "duration_ms": 100000,
            "artist_name": "Artist",
        }

    def test_cli_emits_json_and_returns_failure_for_provider_errors(self):
        """Serialize partial provider failures and use a nonzero exit code."""
        from _enrichment import main

        result = EnrichmentResult(
            candidate_tracks=2,
            records_saved=1,
            provider_errors={"lastfm": 1},
        )
        output = StringIO()
        with patch("_enrichment.run_enrichment", return_value=result) as run:
            with redirect_stdout(output), self.assertRaises(SystemExit) as raised:
                main(["--limit", "7"])

        payload = json.loads(output.getvalue())
        self.assertEqual(raised.exception.code, 1)
        self.assertEqual(payload["status"], "partial")
        self.assertEqual(payload["provider_errors"]["lastfm"], 1)
        run.assert_called_once_with(limit=7)

    def test_cli_rejects_nonpositive_limit_before_running_service(self):
        """Reject a nonpositive batch size before invoking enrichment."""
        from _enrichment import main

        with patch("_enrichment.run_enrichment") as run:
            with self.assertRaises(SystemExit) as raised:
                main(["--limit", "0"])

        self.assertEqual(raised.exception.code, 2)
        run.assert_not_called()

    def test_cli_defers_cleanly_when_metadata_migration_is_missing(self):
        """Report missing enrichment tables as deferred without failing liked sync."""
        from _enrichment import main

        error = RuntimeError("relation does not exist")
        error.sqlstate = "42P01"
        output = StringIO()
        with patch("_enrichment.run_enrichment", side_effect=error):
            with redirect_stdout(output), self.assertRaises(SystemExit) as raised:
                main(["--limit", "10"])

        self.assertEqual(raised.exception.code, 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["status"], "deferred")
        self.assertEqual(payload["reason"], "metadata_migration_required")

    def test_streamlit_action_calls_shared_service_with_configured_optional_key(self):
        """Pass the submitted batch size and optional Last.fm key to the shared service."""
        from app_views.summary import run_metadata_enrichment

        expected = Mock()
        with patch.dict("os.environ", {
            "LASTFM_API_KEY": "test-key",
            "MUSICBRAINZ_USER_AGENT": "Spoty tests",
        }):
            with patch("services.enrichment.EnrichmentService") as service_factory:
                service_factory.return_value.enrich_pending.return_value = expected

                result = run_metadata_enrichment(limit=23)

        self.assertIs(result, expected)
        service_factory.assert_called_once()
        self.assertEqual(
            service_factory.call_args.kwargs["lastfm_api_key"], "test-key"
        )
        self.assertEqual(
            service_factory.call_args.kwargs["musicbrainz_user_agent"], "Spoty tests"
        )
        service_factory.return_value.enrich_pending.assert_called_once_with(limit=23)

    def test_provider_failure_does_not_block_other_sources(self):
        """Continue MusicBrainz after a transient Last.fm failure."""
        repository = MemoryMetadataRepository(self.candidate)
        musicbrainz = Mock(return_value={"recordings": []})

        class LastFm:
            def get_track(self, **_kwargs):
                raise LastFmAPIError(10, "Invalid API key", {"error": 10})

        service = EnrichmentService(
            "unused",
            lastfm_api_key="test-key",
            repository=repository,
            lastfm_factory=lambda _key: LastFm(),
            musicbrainz_factory=lambda _user_agent: type(
                "MusicBrainz", (), {"search_recordings": staticmethod(musicbrainz)}
            )(),
            acousticbrainz_factory=lambda: Mock(),
        )

        result = service.enrich_pending()

        musicbrainz.assert_called_once_with("Cafe del Mar", "Artist")
        self.assertEqual(result.provider_errors["lastfm"], 1)
        self.assertEqual(result.not_found["musicbrainz"], 1)
        self.assertIn(("musicbrainz", "spotify-track"), repository.records)

    def test_ambiguous_musicbrainz_results_are_not_linked_or_sent_to_acousticbrainz(self):
        """Do not associate or analyze recordings when exact matches are ambiguous."""
        repository = MemoryMetadataRepository(self.candidate)
        same_recording = {
            "id": "recording-a",
            "title": "Cafe del Mar",
            "length": 100000,
            "artist-credit": [{"name": "Artist", "artist": {"name": "Artist"}}],
        }
        musicbrainz = Mock(return_value={
            "recordings": [same_recording, {**same_recording, "id": "recording-b"}]
        })
        acousticbrainz = Mock()
        service = EnrichmentService(
            "unused",
            repository=repository,
            musicbrainz_factory=lambda _user_agent: type(
                "MusicBrainz", (), {"search_recordings": staticmethod(musicbrainz)}
            )(),
            acousticbrainz_factory=lambda: acousticbrainz,
        )

        result = service.enrich_pending()

        self.assertEqual(result.ambiguous_musicbrainz, 1)
        self.assertEqual(
            repository.records[("musicbrainz", "spotify-track")]["match_status"],
            "ambiguous",
        )
        acousticbrainz.get_analysis.assert_not_called()


if __name__ == "__main__":
    unittest.main()
