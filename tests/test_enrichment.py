import unittest
import json
from contextlib import nullcontext
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import Mock, patch

from metadata_sources.lastfm import LastFmAPIError
from register.enrichment import MetadataRepository
from services.enrichment import EnrichmentResult, EnrichmentService
from services.enrichment_contract import EnrichmentReport, combine_reports
from services.metadata_enrichment import MetadataEnrichmentService


class EnrichmentContractTest(unittest.TestCase):
    def test_all_sources_share_error_and_exit_semantics(self):
        """Transient outages stay partial; auth, invalid data and incomplete runs fail visibly."""
        for provider in ("lastfm", "musicbrainz", "acousticbrainz", "smarterplaylists"):
            with self.subTest(provider=provider):
                transient = EnrichmentReport(provider, "partial", {"track": 2}, 1,
                                             {provider: {"timeout": 1}})
                self.assertEqual(transient.exit_code, 0)
                self.assertEqual(json.loads(transient.to_json())["status"], "partial")
                invalid = EnrichmentReport(provider, "partial", {}, 0,
                                           {provider: {"invalid_response": 1}})
                self.assertEqual(invalid.exit_code, 1)
                self.assertEqual(EnrichmentReport(provider, "incomplete", {}, 0).exit_code, 1)

    def test_refresh_reports_keep_bulk_counts_and_provider_provenance(self):
        """Normalize refresh counters without discarding bulk progress or source identity."""
        result = EnrichmentReport.from_refresh({"operation": "smarterplaylists_refresh",
            "status": "completed", "candidates": {"track": 20, "artist": 10},
            "records_saved": 30, "errors": {}, "scope": "all_due", "remaining": {"track": 0}})
        payload = json.loads(result.to_json())
        self.assertEqual(payload["operation"], "metadata_enrichment")
        self.assertEqual(payload["provider"], "smarterplaylists")
        self.assertEqual(payload["scope"], "all_due")
        combined = combine_reports([result], skipped=["lastfm"])
        self.assertEqual(combined.records_saved, 30)
        self.assertEqual(combined.details["skipped_providers"], ["lastfm"])

    def test_transient_bulk_stops_with_unattempted_entities_are_incomplete(self):
        """Do not report success when rate limiting leaves bulk snapshot IDs unvisited."""
        report = EnrichmentReport.from_refresh({"status": "partial", "scope": "all_due",
            "candidates": {"track": 100}, "records_saved": 5, "remaining": {"track": 50},
            "errors": {"rate_limited": 50}, "dry_run": False})
        self.assertEqual(report.status, "incomplete")
        self.assertEqual(report.exit_code, 1)


class UnifiedMetadataServiceTest(unittest.TestCase):
    def test_budget_exhaustion_names_every_unstarted_source(self):
        """Account for all four selected providers without issuing queries after the deadline."""
        ticks = iter([0, 61, 61, 61, 61])
        legacy = Mock()
        service = MetadataEnrichmentService("unused", lastfm_api_key="key",
            session_cookie="private", legacy_service=legacy, clock=lambda: next(ticks))
        result = service.run(all_due=True, max_minutes=1)
        reports = result.details["provider_results"]
        self.assertEqual(len(reports), 4)
        self.assertTrue(all(report["not_started"] for report in reports))
        self.assertEqual(result.status, "incomplete")
        self.assertEqual(result.exit_code, 1)
        legacy.get_candidates.assert_not_called()
        legacy.enrich_pending.assert_not_called()

    def test_routes_each_provider_without_invoking_the_other_procedure(self):
        """Use a shared result while preserving source-owned execution and tables."""
        legacy = Mock()
        legacy.enrich_pending.return_value = EnrichmentResult(1, 1, provider="musicbrainz")
        smarter = Mock()
        smarter.refresh.return_value = {"status": "completed", "candidates": {"track": 1},
                                       "records_saved": 1, "errors": {}}
        service = MetadataEnrichmentService("unused", legacy_service=legacy, smarter_service=smarter)
        self.assertEqual(service.run("musicbrainz").provider, "musicbrainz")
        smarter.refresh.assert_not_called()
        legacy.enrich_pending.reset_mock()
        self.assertEqual(service.run("smarterplaylists").provider, "smarterplaylists")
        legacy.enrich_pending.assert_not_called()

    def test_all_sources_are_independent_and_skip_unconfigured_optional_sources(self):
        """Continue after a failed source and select AcousticBrainz after persisted MusicBrainz work."""
        legacy = Mock()
        calls = []
        def run(*, limit, provider):
            calls.append(provider)
            if provider == "musicbrainz":
                raise RuntimeError("must-not-appear")
            return EnrichmentResult(0, 0, provider=provider)
        legacy.enrich_pending.side_effect = run
        result = MetadataEnrichmentService("unused", legacy_service=legacy).run()
        self.assertEqual(calls, ["musicbrainz", "acousticbrainz"])
        self.assertEqual(set(result.details["skipped_providers"]), {"lastfm", "smarterplaylists"})
        self.assertEqual(result.exit_code, 1)
        self.assertNotIn("must-not-appear", result.to_json())

    def test_classic_bulk_snapshot_does_not_repeat_failed_ids(self):
        """Share bulk semantics with Smarter Playlists without reselecting failed rows."""
        legacy = Mock()
        legacy.get_candidates.return_value = [{"id": index} for index in range(7)]
        visited = []
        def process(rows, *, provider):
            visited.extend(row["id"] for row in rows)
            return EnrichmentResult(len(rows), 0, provider=provider,
                provider_errors={provider: len(rows)},
                provider_error_details={provider: {"timeout": len(rows)}})
        legacy.enrich_candidates.side_effect = process
        result = MetadataEnrichmentService("unused", legacy_service=legacy).run(
            "musicbrainz", all_due=True, batch_size=3)
        self.assertEqual(visited, list(range(7)))
        self.assertEqual(result.status, "partial")
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.details["remaining"], {"track": 0})

    def test_shared_bulk_preview_never_runs_provider_adapters(self):
        """Preview pending counts for every source through the same public interface."""
        legacy = Mock()
        legacy.get_candidates.return_value = [{"id": 1}]
        smarter = Mock()
        smarter.refresh_all.return_value = {"status": "completed", "candidates": {"track": 2},
            "records_saved": 0, "errors": {}, "dry_run": True}
        result = MetadataEnrichmentService("unused", lastfm_api_key="key", legacy_service=legacy,
            smarter_service=smarter).run(all_due=True, dry_run=True)
        self.assertEqual(len(result.details["provider_results"]), 4)
        legacy.enrich_candidates.assert_not_called()
        legacy.enrich_pending.assert_not_called()
        self.assertEqual(result.records_saved, 0)
        self.assertTrue(result.details["dry_run"])
        self.assertEqual(result.details["scope"], "all_due")


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

    def test_single_provider_never_constructs_other_adapters(self):
        """Isolate provider calls and writes, including AcousticBrainz using a stored MBID."""
        for provider in ("lastfm", "musicbrainz", "acousticbrainz"):
            with self.subTest(provider=provider):
                repository = MemoryMetadataRepository(self.candidate)
                if provider == "acousticbrainz":
                    repository.records[("musicbrainz", "spotify-track")] = {
                        "match_status": "matched", "recording_mbid": "recording-id",
                    }
                recording = {"id": "recording-id", "title": "Cafe del Mar", "length": 100000,
                             "artist-credit": [{"name": "Artist", "artist": {"name": "Artist"}}]}
                lastfm = Mock()
                lastfm.get_track.return_value = {"track": {"name": "Cafe del Mar", "artist": "Artist"}}
                musicbrainz = Mock()
                musicbrainz.search_recordings.return_value = {"recordings": [recording]}
                musicbrainz.get_entity.return_value = recording
                acousticbrainz = Mock()
                acousticbrainz.get_analysis.side_effect = lambda _mbid, level: {
                    "lowlevel" if level == "low-level" else "highlevel": {"value": 1}}
                factories = {"lastfm": Mock(return_value=lastfm),
                             "musicbrainz": Mock(return_value=musicbrainz),
                             "acousticbrainz": Mock(return_value=acousticbrainz)}
                service = EnrichmentService("unused", lastfm_api_key="test-key", repository=repository,
                    lastfm_factory=factories["lastfm"], musicbrainz_factory=factories["musicbrainz"],
                    acousticbrainz_factory=factories["acousticbrainz"])
                result = service.enrich_pending(provider=provider)
                self.assertEqual(result.provider, provider)
                self.assertGreater(result.matched[provider], 0)
                for name, factory in factories.items():
                    self.assertEqual(factory.call_count, 1 if name == provider else 0)
                    if name != provider:
                        self.assertEqual(result.matched[name], 0)

    def test_provider_selection_filters_candidates_and_requires_lastfm_key(self):
        """Avoid selecting other providers' pending rows and fail explicit Last.fm without a key."""
        repository = Mock()
        repository.get_candidates.return_value = []
        service = EnrichmentService("unused", lastfm_api_key="key", repository=repository)
        for provider in ("lastfm", "musicbrainz", "acousticbrainz"):
            with self.subTest(provider=provider):
                service.enrich_pending(limit=7, provider=provider)
                repository.get_candidates.assert_called_with("unused", 7,
                    include_lastfm=provider == "lastfm", include_musicbrainz=provider == "musicbrainz",
                    include_acousticbrainz=provider == "acousticbrainz")
        with self.assertRaises(ValueError):
            EnrichmentService("unused", repository=repository).enrich_pending(provider="lastfm")
        with self.assertRaises(ValueError):
            service.enrich_pending(provider="unknown")

    def test_snapshot_candidates_can_be_processed_without_requerying(self):
        """Use explicit candidate batches so bulk retries cannot select the same failing IDs forever."""
        repository = Mock()
        musicbrainz = Mock()
        musicbrainz.search_recordings.return_value = {"recordings": []}
        service = EnrichmentService("unused", repository=repository,
            musicbrainz_factory=lambda _agent: musicbrainz)
        result = service.enrich_candidates([self.candidate], provider="musicbrainz")
        self.assertEqual(result.not_found["musicbrainz"], 1)
        repository.get_candidates.assert_not_called()
        repository.save_results.assert_called_once()

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

    def test_cli_forwards_the_explicit_provider(self):
        """Send source selection through the CLI without invoking the combined mode."""
        from _enrichment import main

        result = EnrichmentResult(0, 0, provider="musicbrainz")
        output = StringIO()
        with patch("_enrichment.run_enrichment", return_value=result) as run:
            with redirect_stdout(output), self.assertRaises(SystemExit) as raised:
                main(["--provider", "musicbrainz", "--limit", "5"])
        self.assertEqual(raised.exception.code, 0)
        run.assert_called_once_with(limit=5, provider="musicbrainz")
        self.assertEqual(json.loads(output.getvalue())["provider"], "musicbrainz")

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
            with patch("services.metadata_enrichment.MetadataEnrichmentService") as service_factory:
                service_factory.return_value.run.return_value = expected

                result = run_metadata_enrichment(limit=23)

        self.assertIs(result, expected)
        service_factory.assert_called_once()
        self.assertEqual(
            service_factory.call_args.kwargs["lastfm_api_key"], "test-key"
        )
        self.assertEqual(
            service_factory.call_args.kwargs["musicbrainz_user_agent"], "Spoty tests"
        )
        service_factory.return_value.run.assert_called_once_with(limit=23)

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

    def test_musicbrainz_failures_are_diagnosed_without_marking_tracks_not_found(self):
        """Keep transient failures pending and report them without failing the batch."""
        import requests

        repository = MemoryMetadataRepository(self.candidate)
        musicbrainz = Mock()
        response = Mock(status_code=503)
        error = requests.HTTPError("private-response-do-not-log", response=response)
        musicbrainz.search_recordings.side_effect = [error, {"recordings": []}]
        service = EnrichmentService(
            "unused", repository=repository,
            musicbrainz_factory=lambda _agent: musicbrainz,
            acousticbrainz_factory=lambda: Mock(),
        )
        first = service.enrich_pending()
        self.assertEqual(first.exit_code, 0)
        self.assertEqual(json.loads(first.to_json())["status"], "partial")
        self.assertEqual(first.provider_error_details, {"musicbrainz": {"http_503": 1}})
        self.assertEqual(first.not_found["musicbrainz"], 0)
        self.assertNotIn(("musicbrainz", "spotify-track"), repository.records)
        self.assertNotIn("private-response", first.to_json())
        second = service.enrich_pending()
        self.assertEqual(second.exit_code, 0)
        self.assertEqual(second.not_found["musicbrainz"], 1)

    def test_nontransient_provider_errors_still_fail_the_batch(self):
        """Fail enrichment for malformed provider responses, unlike temporary outages."""
        result = EnrichmentResult(
            candidate_tracks=1,
            records_saved=0,
            provider_errors={"acousticbrainz": 1},
            provider_error_details={"acousticbrainz": {"invalid_response": 1}},
        )

        self.assertEqual(result.exit_code, 1)
        self.assertEqual(json.loads(result.to_json())["status"], "partial")

    def test_invalid_musicbrainz_lookup_is_a_provider_error_not_a_batch_crash(self):
        """Reject malformed lookup details inside the provider error boundary."""
        repository = MemoryMetadataRepository(self.candidate)
        musicbrainz = Mock()
        musicbrainz.search_recordings.return_value = {"recordings": [{
            "id": "recording-id", "title": "Cafe del Mar", "length": 100000,
            "artist-credit": [{"name": "Artist", "artist": {"name": "Artist"}}],
        }]}
        musicbrainz.get_entity.return_value = {"id": "recording-id"}
        service = EnrichmentService(
            "unused", repository=repository,
            musicbrainz_factory=lambda _agent: musicbrainz,
            acousticbrainz_factory=lambda: Mock(),
        )
        result = service.enrich_pending()
        self.assertEqual(result.provider_error_details, {"musicbrainz": {"invalid_response": 1}})
        self.assertEqual(result.records_saved, 0)

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
