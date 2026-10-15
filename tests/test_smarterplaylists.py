"""Offline validation and persistence contracts for Smarter Playlists captures."""

import copy
import json
import unittest
from contextlib import nullcontext
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from services.smarterplaylists import import_capture, load_capture, merge_metadata, normalize_capture
from register.smarterplaylists import SmarterPlaylistsRepository
from metadata_sources.smarterplaylists import SmarterPlaylistsAdapter, SmarterPlaylistsError, load_session_cookie
from services.smarterplaylists_refresh import SmarterPlaylistsRefreshService


TRACK_ID = "3n3Ppam7vgaVa1iaRUc9Lp"


def capture():
    values = {"id": TRACK_ID, "uri": f"spotify:track:{TRACK_ID}",
              "name": "Mr. Brightside", "instrumentalness": 0, "explicit": False,
              "energy": 0.918, "artist_popularity": 81, "source_count": 1}
    return {"captured_at": "2026-10-15T14:24:31Z", "entities": {"track": {
        "values": copy.deepcopy(values), "conflicts": {}, "missing_requested": ["tempo"],
        "raw": {"status": "complete", "playlist_uri": None,
                "tracks": [copy.deepcopy(values), copy.deepcopy(values)]},
    }}}


class CaptureNormalizationTest(unittest.TestCase):
    def update_field(self, payload, field, value):
        result = payload["entities"]["track"]
        result["values"][field] = value
        for row in result["raw"]["tracks"]:
            row[field] = value

    def test_normalization_preserves_zero_and_false_and_separates_related_data(self):
        """Keep typed values but do not promote artist or operational fields to tracks."""
        batch = normalize_capture(capture())
        record = batch.records[0]
        self.assertEqual(record.normalized_data["instrumentalness"], 0)
        self.assertIs(record.normalized_data["explicit"], False)
        self.assertNotIn("artist_popularity", record.normalized_data)
        self.assertEqual(record.normalized_data["related_artist"]["popularity"], 81)
        self.assertNotIn("source_count", record.normalized_data)
        self.assertEqual(record.status, "partial")
        self.assertEqual(batch.capture_id, normalize_capture(capture()).capture_id)

    def test_rejects_invalid_or_unsafe_captures(self):
        """Reject identity errors, conflicts, secrets, empty rows and invalid values."""
        for kind in ("identity", "conflict", "secret", "empty", "range", "boolean", "timestamp"):
            with self.subTest(kind=kind):
                payload = capture()
                result = payload["entities"]["track"]
                if kind == "identity":
                    result["raw"]["tracks"][0]["uri"] = "spotify:artist:" + TRACK_ID
                elif kind == "conflict":
                    result["raw"]["tracks"][1]["energy"] = 0.1
                elif kind == "secret":
                    payload["refresh_token"] = "not-a-real-token"
                elif kind == "empty":
                    result["raw"]["tracks"] = []
                elif kind == "timestamp":
                    payload["captured_at"] = "2026-10-15T14:24:31"
                else:
                    field, value = ("energy", 2) if kind == "range" else ("explicit", 0)
                    result["values"][field] = value
                    for row in result["raw"]["tracks"]:
                        row[field] = value
                with self.assertRaises(ValueError):
                    normalize_capture(payload)

    def test_partial_and_older_captures_do_not_erase_or_rejuvenate_fields(self):
        """Preserve absent fields and their original provenance across refreshes."""
        batch = normalize_capture(capture())
        values, metadata = merge_metadata({}, batch.records[0], batch.capture_id)
        existing = {"normalized_data": values, "field_metadata": metadata}
        payload = capture()
        payload["captured_at"] = "2026-10-16T14:24:31Z"
        result = payload["entities"]["track"]
        del result["values"]["energy"]
        for row in result["raw"]["tracks"]:
            del row["energy"]
        refreshed = normalize_capture(payload)
        merged, provenance = merge_metadata(existing, refreshed.records[0], refreshed.capture_id)
        self.assertEqual(merged["energy"], 0.918)
        self.assertEqual(provenance["energy"], metadata["energy"])
        updated = {"normalized_data": merged, "field_metadata": provenance}
        older_values, older_metadata = merge_metadata(updated, batch.records[0], batch.capture_id)
        self.assertEqual(older_values, merged)
        self.assertEqual(older_metadata, provenance)

    def test_related_fields_keep_independent_provenance(self):
        """Preserve nested artist fields when a later capture only supplies its name."""
        batch = normalize_capture(capture())
        values, metadata = merge_metadata({}, batch.records[0], batch.capture_id)
        later = capture()
        later["captured_at"] = "2026-10-16T14:24:31Z"
        result = later["entities"]["track"]
        del result["values"]["artist_popularity"]
        for row in result["raw"]["tracks"]:
            del row["artist_popularity"]
        self.update_field(later, "artist", "The Killers")
        next_batch = normalize_capture(later)
        merged, provenance = merge_metadata(
            {"normalized_data": values, "field_metadata": metadata},
            next_batch.records[0], next_batch.capture_id,
        )
        self.assertEqual(merged["related_artist"]["popularity"], 81)
        self.assertEqual(provenance["related_artist.popularity"], metadata["related_artist.popularity"])
        self.assertNotIn("name", values["related_artist"])

    def test_same_time_contradictions_are_rejected(self):
        """Do not silently choose between differing values captured at the same instant."""
        batch = normalize_capture(capture())
        values, metadata = merge_metadata({}, batch.records[0], batch.capture_id)
        different = capture()
        self.update_field(different, "energy", 0.5)
        next_batch = normalize_capture(different)
        with self.assertRaisesRegex(ValueError, "same time"):
            merge_metadata({"normalized_data": values, "field_metadata": metadata},
                           next_batch.records[0], next_batch.capture_id)

    def test_dates_ranges_unknown_keys_and_timezone_equivalence(self):
        """Validate SQL-compatible ranges, partial dates and unknown pitch sentinels."""
        for field, value in (("release_date", "2004-02-30"), ("popularity", 101),
                             ("duration_ms", 2**31), ("mode", 2), ("key", 12),
                             ("tempo", -1), ("danceability", float("inf"))):
            with self.subTest(field=field):
                payload = capture()
                self.update_field(payload, field, value)
                with self.assertRaises(ValueError):
                    normalize_capture(payload)
        payload = capture()
        self.update_field(payload, "key", -1)
        self.update_field(payload, "release_date", "2004-06")
        record = normalize_capture(payload).records[0]
        self.assertNotIn("key", record.normalized_data)
        self.assertIn("key", record.missing_requested)
        equivalent = capture()
        equivalent["captured_at"] = "2026-10-15T16:24:31+02:00"
        self.assertEqual(normalize_capture(equivalent).capture_id, normalize_capture(capture()).capture_id)

    def test_summary_alone_playlist_outputs_and_false_missing_fields_are_rejected(self):
        """Require original evidence and a consistent declaration of unavailable fields."""
        for case in ("summary", "playlist", "missing"):
            with self.subTest(case=case):
                payload = capture()
                result = payload["entities"]["track"]
                if case == "summary":
                    del result["raw"]
                elif case == "playlist":
                    result["raw"]["playlist_uri"] = "spotify:playlist:unexpected"
                else:
                    result["missing_requested"] = ["energy"]
                with self.assertRaises(ValueError):
                    normalize_capture(payload)


class CaptureRepositoryTest(unittest.TestCase):
    def database(self, rows):
        database = Mock()
        database.__enter__ = Mock(return_value=database)
        database.__exit__ = Mock(return_value=False)
        database.transaction.return_value = nullcontext()
        database.cursor.fetchone.side_effect = rows
        return database

    def test_import_validates_local_identity_and_upserts_only_metadata(self):
        """Write an import and metadata atomically without updating core tables."""
        database = self.database([None, {"id": TRACK_ID}, None])
        repository = SmarterPlaylistsRepository(lambda _url: database)
        result = repository.import_batch("unused", normalize_capture(capture()))
        self.assertEqual(result["records_saved"], 1)
        database.transaction.assert_called_once()
        statements = [str(call.args[0]) for call in database.cursor.execute.call_args_list]
        self.assertTrue(any("ON CONFLICT" in query for query in statements))
        self.assertTrue(any("FOR KEY SHARE" in query for query in statements))
        self.assertFalse(any("UPDATE tracks" in query for query in statements))

    def test_unknown_local_entity_and_repeated_import_do_not_write(self):
        """Reject unknown IDs and skip captures already committed successfully."""
        for rows, expected in (([None, None], "unknown"), ([{"capture_id": "existing"}], "duplicate")):
            with self.subTest(expected=expected):
                database = self.database(rows)
                repository = SmarterPlaylistsRepository(lambda _url: database)
                if expected == "unknown":
                    with self.assertRaisesRegex(ValueError, "Unknown local"):
                        repository.import_batch("unused", normalize_capture(capture()))
                else:
                    result = repository.import_batch("unused", normalize_capture(capture()))
                    self.assertEqual(result["status"], "already_imported")
                statements = [str(call.args[0]) for call in database.cursor.execute.call_args_list]
                self.assertFalse(any("INSERT" in query for query in statements))

    def test_existing_metadata_is_merged_and_reads_are_entity_scoped(self):
        """Retain stored fields and parameterize reads without exposing table selection."""
        batch = normalize_capture(capture())
        existing = {"normalized_data": {"tempo": 148.114}, "field_metadata": {
            "tempo": {"captured_at": "2026-10-14T00:00:00+00:00", "capture_id": "earlier"},
        }}
        database = self.database([None, {"id": TRACK_ID}, existing])
        repository = SmarterPlaylistsRepository(lambda _url: database)
        repository.import_batch("unused", batch)
        parameters = database.cursor.execute.call_args.args[1]
        self.assertEqual(parameters[1].obj["tempo"], 148.114)
        self.assertEqual(parameters[2].obj["tempo"]["capture_id"], "earlier")
        query_database = self.database([{"normalized_data": {"energy": 0.918}}])
        repository = SmarterPlaylistsRepository(lambda _url: query_database)
        self.assertEqual(repository.get_metadata("unused", "track", TRACK_ID)["normalized_data"]["energy"], 0.918)
        with self.assertRaises(ValueError):
            repository.get_metadata("unused", "not_a_table", TRACK_ID)


class CaptureAdapterTest(unittest.TestCase):
    def adapter(self, responses):
        session = Mock()
        session.request.side_effect = [Mock(status_code=status, json=Mock(return_value=body))
                                       for status, body in responses]
        return SmarterPlaylistsAdapter("fake-session", session=session, sleep=Mock()), session

    def inventory(self):
        return [{"name": "Sorter", "params": {"properties": {"attr": {"enum": ["name", "energy"]}}}}]

    def test_adapter_groups_results_and_never_saves_playlists(self):
        """Fetch only marked metadata programs and split batched rows by Spotify ID."""
        payload = capture()
        raw = payload["entities"]["track"]["raw"]
        adapter, session = self.adapter([(200, self.inventory()), (200, []),
            (200, {"pid": "owned"}), (200, {"run_id": "run"}), (200, raw)])
        reports, missing = adapter.capture("track", [{"id": TRACK_ID}])
        self.assertEqual(len(reports), 1)
        self.assertEqual(missing, [])
        self.assertEqual(normalize_capture(reports[0]).records[0].spotify_id, TRACK_ID)
        urls = [call.args[1] for call in session.request.call_args_list]
        self.assertTrue(any(url.endswith("/run?save=false") for url in urls))
        self.assertTrue(all(call.kwargs["allow_redirects"] is False for call in session.request.call_args_list))
        created = session.request.call_args_list[2].kwargs["json"]["program_json"]
        self.assertEqual(created["components"]["source"]["params"]["cache_mode"], "no-cache")
        self.assertTrue(all(node["type"] in {"TrackSource", "Sorter", "GenreFilter", "Concatenate"}
                            for node in created["components"].values()))

    def test_adapter_refuses_unowned_programs_and_expired_sessions(self):
        """Do not modify user programs or treat denied authentication as success."""
        adapter, session = self.adapter([(200, self.inventory()),
            (200, [{"name": "Spoty Daily Metadata track", "pid": "user"}]),
            (200, {"program_json": {}})])
        with self.assertRaisesRegex(SmarterPlaylistsError, "unowned_program"):
            adapter.capture("track", [{"id": TRACK_ID}])
        self.assertTrue(all(call.args[0] == "GET" for call in session.request.call_args_list))
        for status in (302, 401, 403, 429):
            adapter, _session = self.adapter([(status, {})])
            with self.subTest(status=status), self.assertRaises(SmarterPlaylistsError):
                adapter.authenticate()
        with self.assertRaises(SmarterPlaylistsError):
            load_session_cookie(value="invalid;cookie")


class CaptureRefreshTest(unittest.TestCase):
    def test_refresh_closes_adapter_and_persists_success(self):
        """Refresh due entities through the normalizer and short repository operations."""
        repository = Mock()
        repository.get_refresh_candidates.return_value = {"track": [{"id": TRACK_ID}], "artist": [], "album": []}
        repository.import_batch.return_value = {"records_saved": 1}
        adapter = Mock()
        adapter.capture.return_value = ([capture()], [])
        service = SmarterPlaylistsRefreshService("unused", lambda: adapter, repository)
        result = service.refresh()
        self.assertEqual(result["records_saved"], 1)
        self.assertEqual(result["status"], "completed")
        repository.record_refresh_outcome.assert_called_once_with(
            "unused", "track", TRACK_ID, "partial", refresh_hours=20, error_code=None)
        adapter.close.assert_called_once()

    def test_refresh_handles_expired_sessions_and_missing_rows_without_erasure(self):
        """Defer auth failures and retry missing data without overwriting valid metadata."""
        for case in ("auth", "missing"):
            with self.subTest(case=case):
                repository = Mock()
                repository.get_refresh_candidates.return_value = {"track": [{"id": TRACK_ID}]}
                adapter = Mock()
                if case == "auth":
                    adapter.authenticate.side_effect = SmarterPlaylistsError("session_expired")
                else:
                    adapter.capture.return_value = ([], [TRACK_ID])
                result = SmarterPlaylistsRefreshService("unused", lambda: adapter, repository).refresh()
                self.assertIn(result["status"], ("partial", "deferred"))
                repository.import_batch.assert_not_called()
                repository.record_refresh_outcome.assert_called_once()
                adapter.close.assert_called_once()

    def test_refresh_preview_never_builds_an_authenticated_client(self):
        """Inspect database candidates without HTTP, metadata writes or session access."""
        repository = Mock()
        repository.get_refresh_candidates.return_value = {"track": [{"id": TRACK_ID}]}
        factory = Mock(side_effect=AssertionError("Unexpected authentication"))
        result = SmarterPlaylistsRefreshService("unused", factory, repository).refresh(dry_run=True)
        self.assertTrue(result["dry_run"])
        factory.assert_not_called()
        repository.record_refresh_outcome.assert_not_called()
        repository.import_batch.assert_not_called()


class CaptureImportTest(unittest.TestCase):
    def test_dry_run_and_cli_defaults_never_connect_or_request_network(self):
        """Validate files and default CLI invocation without config, DB or HTTP calls."""
        from _smarterplaylists import main

        with TemporaryDirectory() as directory:
            path = Path(directory) / "capture.json"
            path.write_text(json.dumps(capture()), encoding="utf-8")
            output = StringIO()
            with patch("psycopg.connect") as connect, patch("requests.sessions.Session.request") as request:
                with redirect_stdout(output):
                    main([str(path)])
                connect.assert_not_called()
                request.assert_not_called()
            result = json.loads(output.getvalue())
            self.assertTrue(result["dry_run"])
            self.assertEqual(result["records_saved"], 0)
            self.assertEqual(result["local_identity_check"], "not_performed")

    def test_apply_delegates_only_validated_captures(self):
        """Keep offline normalization before the injected repository performs writes."""
        with TemporaryDirectory() as directory:
            path = Path(directory) / "capture.json"
            path.write_text(json.dumps(capture()), encoding="utf-8")
            repository = Mock()
            repository.import_batch.return_value = {"status": "imported", "records_saved": 1}
            result = import_capture(path, dry_run=False, database_url="unused", repository=repository)
            self.assertEqual(result["records_saved"], 1)
            repository.import_batch.assert_called_once()
            with self.assertRaises(ValueError):
                import_capture(path, dry_run=False)

    def test_strict_loader_rejects_duplicate_keys_and_nonfinite_json(self):
        """Reject ambiguous JSON and nonstandard numeric constants before normalization."""
        with TemporaryDirectory() as directory:
            path = Path(directory) / "capture.json"
            for content in ('{"captured_at":1,"captured_at":2}', '{"value":NaN}'):
                with self.subTest(content=content):
                    path.write_text(content, encoding="utf-8")
                    with self.assertRaises(ValueError):
                        load_capture(path)

    def test_cli_rejection_is_structured_and_has_nonzero_exit(self):
        """Report validation failures without echoing capture contents or credentials."""
        from _smarterplaylists import main

        with TemporaryDirectory() as directory:
            path = Path(directory) / "capture.json"
            path.write_text('{"refresh_token":"private"}', encoding="utf-8")
            output = StringIO()
            with redirect_stdout(output), self.assertRaises(SystemExit) as raised:
                main([str(path)])
            self.assertEqual(raised.exception.code, 1)
            self.assertEqual(json.loads(output.getvalue())["status"], "failed")
            self.assertNotIn("private", output.getvalue())


if __name__ == "__main__":
    unittest.main()