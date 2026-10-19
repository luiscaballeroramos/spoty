"""Opt-in PostgreSQL checks inside a disposable, rolled-back schema."""

import copy
import json
import os
import unittest
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from register.migrations import apply_migrations
from register.smarterplaylists import SmarterPlaylistsRepository
from services.smarterplaylists import normalize_capture


@unittest.skipUnless(os.getenv("SPOTY_TEST_DATABASE_URL"), "Explicit isolated PostgreSQL test URL required")
class PostgreSQLCaptureTest(unittest.TestCase):
    def test_migration_import_refresh_views_and_transaction_rollback(self):
        """Exercise real SQL without changing the application schema or retaining test data."""
        from config import require_database_url

        payload = json.loads((Path(__file__).resolve().parents[1] /
                              "experiments/smarterplaylists/report.json").read_text(encoding="utf-8"))
        batch = normalize_capture(payload)
        schema = "spoty_validation_" + uuid4().hex
        with psycopg.connect(require_database_url(os.environ["SPOTY_TEST_DATABASE_URL"]),
                             autocommit=True) as connection:
            with connection.transaction():
                connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
                connection.execute(sql.SQL("SET LOCAL search_path TO {}").format(sql.Identifier(schema)))
                versions = apply_migrations(connection)
                self.assertIn("0003_smarterplaylists_metadata", versions)
                self.assertIn("0004_smarterplaylists_refresh_state", versions)
                self.assertIn("0005_remove_non_spotify_core_columns", versions)
                self.assertIn("0006_prune_empty_catalog_columns", versions)
                removed_columns = connection.execute("""
                    SELECT table_name, column_name FROM information_schema.columns
                    WHERE table_schema = current_schema()
                      AND (table_name, column_name) IN (
                        ('artists', 'start_year'), ('artists', 'end_year'), ('artists', 'followers'),
                        ('artists', 'images'), ('artists', 'popularity'),
                        ('albums', 'disc_number'), ('albums', 'popularity'),
                        ('tracks', 'genres'), ('tracks', 'images'),
                        ('tracks', 'acousticness'), ('tracks', 'danceability'), ('tracks', 'energy'),
                        ('tracks', 'instrumentalness'), ('tracks', 'key'), ('tracks', 'liveness'),
                        ('tracks', 'loudness'), ('tracks', 'mode'), ('tracks', 'speechiness'),
                        ('tracks', 'tempo'), ('tracks', 'time_signature'), ('tracks', 'valence')
                      )
                """).fetchall()
                self.assertEqual(removed_columns, [])
                self.assertIsNotNone(connection.execute("""
                    SELECT column_name FROM information_schema.columns
                    WHERE table_schema = current_schema()
                      AND table_name = 'tracks' AND column_name = 'disc_number'
                """).fetchone())
                album_view_columns = connection.execute("""
                    SELECT column_name FROM information_schema.columns
                    WHERE table_schema = current_schema()
                      AND table_name = 'smarterplaylists_album_details'
                """).fetchall()
                self.assertNotIn("core_popularity", [row[0] for row in album_view_columns])
                self.assertEqual(apply_migrations(connection), [])
                values = {record.entity: record.normalized_data for record in batch.records}
                connection.execute("INSERT INTO artists (id, name) VALUES (%s, %s)",
                                   (values["artist"]["id"], values["artist"]["name"]))
                connection.execute("INSERT INTO albums (id, name, artists, total_tracks) VALUES (%s,%s,%s,%s)",
                                   (values["album"]["id"], values["album"]["name"],
                                    json.dumps([values["artist"]["id"]]), values["album"]["total_tracks"]))
                connection.execute("""INSERT INTO tracks
                    (id, name, duration_ms, album_id, disc_number, artists_ids)
                    VALUES (%s,%s,%s,%s,%s,%s)""",
                    (values["track"]["id"], values["track"]["name"], values["track"]["duration_ms"],
                     values["album"]["id"], 1, json.dumps([values["artist"]["id"]])))
                failed_track_id = "Z" * 22
                connection.execute("INSERT INTO tracks (id, name) VALUES (%s, %s)",
                                   (failed_track_id, "Previously attempted"))

                class Database:
                    def __enter__(self):
                        self.cursor = connection.cursor(row_factory=dict_row)
                        return self

                    def __exit__(self, *_args):
                        self.cursor.close()

                    def transaction(self):
                        return connection.transaction()

                repository = SmarterPlaylistsRepository(lambda _url: Database())
                due = repository.get_refresh_candidates("unused", limit=1)
                self.assertEqual(due["track"][0]["id"], values["track"]["id"])
                never_attempted = repository.get_refresh_candidates(
                    "unused", limit=None, only_unattempted=True)
                self.assertEqual({row["id"] for row in never_attempted["track"]},
                                 {values["track"]["id"], failed_track_id})
                self.assertEqual(repository.import_batch("unused", batch)["records_saved"], 3)
                self.assertEqual(repository.import_batch("unused", batch)["status"], "already_imported")
                repository.record_refresh_outcome(
                    "unused", "track", failed_track_id, "error", error_code="test_error")
                remaining_never_attempted = repository.get_refresh_candidates(
                    "unused", limit=None, only_unattempted=True)
                self.assertEqual(remaining_never_attempted["track"], [])
                feature = connection.execute("SELECT energy, energy_provenance FROM smarterplaylists_track_features").fetchone()
                self.assertAlmostEqual(feature[0], values["track"]["energy"])
                self.assertEqual(feature[1]["resource"], "smarterplaylists")
                self.assertEqual(connection.execute("SELECT resource_followers FROM smarterplaylists_artist_details").fetchone()[0],
                                 values["artist"]["followers"])
                self.assertEqual(connection.execute("SELECT resource_total_tracks FROM smarterplaylists_album_details").fetchone()[0],
                                 values["album"]["total_tracks"])

                older = copy.deepcopy(payload)
                older["captured_at"] = "2026-10-14T00:00:00Z"
                for row in older["entities"]["track"]["raw"]["tracks"]:
                    row["energy"] = 0.2
                older["entities"]["track"]["values"]["energy"] = 0.2
                repository.import_batch("unused", normalize_capture(older))
                stored = repository.get_metadata("unused", "track", values["track"]["id"])
                self.assertEqual(stored["normalized_data"]["energy"], values["track"]["energy"])
                self.assertEqual(stored["last_success_run_id"], batch.capture_id)

                partial = copy.deepcopy(payload)
                partial["captured_at"] = "2026-10-16T00:00:00Z"
                del partial["entities"]["track"]["values"]["energy"]
                for row in partial["entities"]["track"]["raw"]["tracks"]:
                    row.pop("energy", None)
                partial["entities"]["track"]["missing_requested"].append("energy")
                repository.import_batch("unused", normalize_capture(partial))
                refreshed = repository.get_metadata("unused", "track", values["track"]["id"])
                self.assertEqual(refreshed["field_metadata"]["energy"], stored["field_metadata"]["energy"])
                before = connection.execute("SELECT COUNT(*) FROM smarterplaylists_import_runs").fetchone()[0]
                conflict = copy.deepcopy(payload)
                for row in conflict["entities"]["track"]["raw"]["tracks"]:
                    row["energy"] = 0.3
                conflict["entities"]["track"]["values"]["energy"] = 0.3
                with self.assertRaisesRegex(ValueError, "same time"):
                    repository.import_batch("unused", normalize_capture(conflict))
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM smarterplaylists_import_runs").fetchone()[0], before)
                self.assertAlmostEqual(connection.execute("SELECT energy FROM tracks").fetchone()[0], 0.1)
                repository.record_refresh_outcome("unused", "track", values["track"]["id"], "error", error_code="test_error")
                state = connection.execute("""SELECT consecutive_failures, next_attempt_at > NOW()
                    FROM smarterplaylists_refresh_state WHERE entity_type='track'""").fetchone()
                self.assertEqual(state, (1, True))
                repository.record_refresh_outcome("unused", "track", values["track"]["id"], "error", error_code="test_error")
                self.assertEqual(connection.execute("SELECT consecutive_failures FROM smarterplaylists_refresh_state").fetchone()[0], 2)
                repository.record_refresh_outcome("unused", "track", values["track"]["id"], "partial")
                self.assertEqual(connection.execute("SELECT consecutive_failures FROM smarterplaylists_refresh_state").fetchone()[0], 0)
                self.assertEqual(repository.get_refresh_candidates("unused", limit=1)["track"], [])
                self.assertEqual(repository.get_refresh_candidates(
                    "unused", limit=1, only_unattempted=True)["track"], [])
                raise psycopg.Rollback()
            self.assertIsNone(connection.execute("SELECT to_regnamespace(%s)", (schema,)).fetchone()[0])


if __name__ == "__main__":
    unittest.main()
