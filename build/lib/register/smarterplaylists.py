"""Atomic persistence for offline captures, separate from core Spotify tables."""

from typing import Any, Callable
from datetime import timedelta

from psycopg import sql
from psycopg.types.json import Jsonb

from services.smarterplaylists import CaptureBatch, ENTITIES, merge_metadata


class SmarterPlaylistsRepository:
    def __init__(self, database_factory: Callable[[str], Any] | None = None):
        self._database_factory = database_factory

    def _factory(self):
        if self._database_factory is not None:
            return self._database_factory
        from register.db import SimpleDB

        return SimpleDB

    def import_batch(self, database_url: str, batch: CaptureBatch) -> dict[str, Any]:
        with self._factory()(database_url) as database:
            with database.transaction():
                cursor = database.cursor
                cursor.execute("SELECT pg_advisory_xact_lock(1936748391)")
                cursor.execute(
                    "SELECT capture_id FROM smarterplaylists_import_runs WHERE capture_id = %s",
                    (batch.capture_id,),
                )
                if cursor.fetchone() is not None:
                    return {"status": "already_imported", "capture_id": batch.capture_id,
                            "records_saved": 0}
                for record in batch.records:
                    cursor.execute(sql.SQL("SELECT id FROM {} WHERE id = %s FOR KEY SHARE").format(
                        sql.Identifier({"track": "tracks", "artist": "artists", "album": "albums"}[record.entity])
                    ), (record.spotify_id,))
                    if cursor.fetchone() is None:
                        raise ValueError(f"Unknown local {record.entity}: {record.spotify_id}")
                details = {"normalization_version": 1, "upstream_freshness": "unknown",
                           "entities": [{"entity": record.entity, "id": record.spotify_id,
                                         "status": record.status} for record in batch.records]}
                cursor.execute("""INSERT INTO smarterplaylists_import_runs
                    (capture_id, captured_at, record_count, details) VALUES (%s, %s, %s, %s)""",
                    (batch.capture_id, batch.captured_at, len(batch.records), Jsonb(details)))
                for record in batch.records:
                    table = sql.Identifier(f"smarterplaylists_{record.entity}_metadata")
                    key = sql.Identifier(f"spotify_{record.entity}_id")
                    cursor.execute(sql.SQL("SELECT normalized_data, field_metadata FROM {} WHERE {} = %s FOR UPDATE")
                                   .format(table, key), (record.spotify_id,))
                    existing = cursor.fetchone() or {}
                    values, metadata = merge_metadata(dict(existing), record, batch.capture_id)
                    cursor.execute(sql.SQL("""INSERT INTO {} AS stored (
                        {}, normalized_data, field_metadata, raw_response, missing_requested,
                        captured_at, last_attempt_status, last_success_run_id
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT ({}) DO UPDATE SET
                        normalized_data = EXCLUDED.normalized_data,
                        field_metadata = EXCLUDED.field_metadata,
                        raw_response = CASE WHEN EXCLUDED.captured_at >= stored.captured_at
                            THEN EXCLUDED.raw_response ELSE stored.raw_response END,
                        missing_requested = CASE WHEN EXCLUDED.captured_at >= stored.captured_at
                            THEN EXCLUDED.missing_requested ELSE stored.missing_requested END,
                        captured_at = GREATEST(stored.captured_at, EXCLUDED.captured_at),
                        last_attempt_status = EXCLUDED.last_attempt_status,
                        last_success_run_id = CASE WHEN EXCLUDED.captured_at >= stored.captured_at
                            THEN EXCLUDED.last_success_run_id ELSE stored.last_success_run_id END,
                        last_attempt_at = NOW(), last_success_at = NOW(),
                        last_error_code = NULL, next_attempt_at = NULL""").format(table, key, key),
                        (record.spotify_id, Jsonb(values), Jsonb(metadata), Jsonb(record.raw_response),
                         Jsonb(record.missing_requested), batch.captured_at, record.status, batch.capture_id))
        return {"status": "imported", "capture_id": batch.capture_id,
                "records_saved": len(batch.records)}

    def get_metadata(self, database_url: str, entity: str, spotify_id: str):
        if entity not in ENTITIES:
            raise ValueError("Unknown metadata entity.")
        with self._factory()(database_url) as database:
            database.cursor.execute(sql.SQL("SELECT * FROM {} WHERE {} = %s").format(
                sql.Identifier(f"smarterplaylists_{entity}_metadata"),
                sql.Identifier(f"spotify_{entity}_id"),
            ), (spotify_id,))
            row = database.cursor.fetchone()
        return dict(row) if row is not None else None

    def get_refresh_candidates(self, database_url, limit=10, refresh_hours=20):
        if not 1 <= limit <= 100 or not 1 <= refresh_hours <= 168:
            raise ValueError("Invalid refresh batch size or interval.")
        candidates = {}
        with self._factory()(database_url) as database:
            for entity in ENTITIES:
                core = {"track": "tracks", "artist": "artists", "album": "albums"}[entity]
                query = sql.SQL("""SELECT t.id, t.name FROM {} t
                    LEFT JOIN {} m ON m.{} = t.id
                    LEFT JOIN smarterplaylists_refresh_state s
                        ON s.entity_type = %s AND s.spotify_id = t.id
                    WHERE (m.{} IS NULL OR m.captured_at <= NOW() - %s)
                        AND (s.next_attempt_at IS NULL OR s.next_attempt_at <= NOW())
                        AND t.id ~ '^[A-Za-z0-9]{{22}}$'
                    ORDER BY (m.{} IS NOT NULL), s.last_attempt_at ASC NULLS FIRST,
                        m.captured_at ASC NULLS FIRST, t.id
                    LIMIT %s""").format(sql.Identifier(core),
                        sql.Identifier(f"smarterplaylists_{entity}_metadata"),
                        sql.Identifier(f"spotify_{entity}_id"),
                        sql.Identifier(f"spotify_{entity}_id"),
                        sql.Identifier(f"spotify_{entity}_id"))
                database.cursor.execute(query, (entity, timedelta(hours=refresh_hours), limit))
                candidates[entity] = [dict(row) for row in database.cursor.fetchall()]
        return candidates

    def record_refresh_outcome(self, database_url, entity, spotify_id, status, *,
                               refresh_hours=20, error_code=None):
        if entity not in ENTITIES or status not in ("matched", "partial", "error", "deferred"):
            raise ValueError("Invalid refresh outcome.")
        success = status in ("matched", "partial")
        interval = timedelta(hours=refresh_hours) if success else timedelta(minutes=15)
        with self._factory()(database_url) as database:
            with database.transaction():
                database.cursor.execute("""INSERT INTO smarterplaylists_refresh_state AS stored
                    (entity_type, spotify_id, last_status, next_attempt_at, consecutive_failures, last_error_code)
                    VALUES (%s, %s, %s, NOW() + %s, %s, %s)
                    ON CONFLICT (entity_type, spotify_id) DO UPDATE SET
                        last_attempt_at = NOW(), last_status = EXCLUDED.last_status,
                        next_attempt_at = CASE WHEN %s THEN NOW() + %s ELSE
                            NOW() + make_interval(mins => LEAST(1440, 15 * (1 << LEAST(stored.consecutive_failures, 7)))) END,
                        consecutive_failures = CASE WHEN %s THEN 0
                            ELSE LEAST(stored.consecutive_failures + 1, 1000) END,
                        last_error_code = EXCLUDED.last_error_code""",
                    (entity, spotify_id, status, interval, 0 if success else 1, error_code,
                     success, interval, success))
                database.cursor.execute(sql.SQL("""UPDATE {} SET
                    last_attempt_at = NOW(), last_attempt_status = %s, last_error_code = %s,
                    next_attempt_at = (SELECT next_attempt_at FROM smarterplaylists_refresh_state
                        WHERE entity_type = %s AND spotify_id = %s)
                    WHERE {} = %s""").format(sql.Identifier(f"smarterplaylists_{entity}_metadata"),
                                             sql.Identifier(f"spotify_{entity}_id")),
                    (status, error_code, entity, spotify_id, spotify_id))