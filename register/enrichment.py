"""Persistence for provider-owned metadata enrichment records."""

from typing import Any, Callable

from psycopg.types.json import Jsonb


class MetadataRepository:
    """Read pending Spotify tracks and persist source-separated results."""

    def __init__(self, database_factory: Callable[[str], Any] | None = None):
        self._database_factory = database_factory

    def _get_database_factory(self):
        if self._database_factory is not None:
            return self._database_factory
        from register.db import SimpleDB

        return SimpleDB

    def get_candidates(
        self,
        database_url: str,
        limit: int,
        *,
        include_lastfm: bool,
        include_musicbrainz: bool,
        include_acousticbrainz: bool,
    ) -> list[dict[str, Any]]:
        if limit < 1:
            raise ValueError("Candidate limit must be positive.")
        if not any((include_lastfm, include_musicbrainz, include_acousticbrainz)):
            return []

        query = """
                        SELECT t.id AS spotify_track_id, t.name AS title,
                                     t.duration_ms, a.name AS artist_name,
                                     lm.spotify_track_id IS NULL AS needs_lastfm,
                                     mb.spotify_track_id IS NULL AS needs_musicbrainz,
                                     mb.recording_mbid,
                   (
                       mb.match_status = 'matched'
                       AND NOT EXISTS (
                           SELECT 1 FROM acousticbrainz_analysis_metadata AS ab
                           WHERE ab.spotify_track_id = t.id
                             AND ab.analysis_level = 'low-level'
                       )
                   ) AS needs_acousticbrainz_low_level,
                   (
                       mb.match_status = 'matched'
                       AND NOT EXISTS (
                           SELECT 1 FROM acousticbrainz_analysis_metadata AS ab
                           WHERE ab.spotify_track_id = t.id
                             AND ab.analysis_level = 'high-level'
                       )
                   ) AS needs_acousticbrainz_high_level
            FROM tracks AS t
            JOIN artists AS a
              ON a.id = (t.artists_ids::jsonb ->> 0)
                        LEFT JOIN lastfm_track_metadata AS lm
                            ON lm.spotify_track_id = t.id
                        LEFT JOIN musicbrainz_recording_metadata AS mb
                            ON mb.spotify_track_id = t.id
            WHERE (
                (%s AND lm.spotify_track_id IS NULL)
                OR (%s AND mb.spotify_track_id IS NULL)
                OR (%s AND mb.match_status = 'matched' AND (
                    NOT EXISTS (
                        SELECT 1 FROM acousticbrainz_analysis_metadata AS ab
                        WHERE ab.spotify_track_id = t.id
                          AND ab.analysis_level = 'low-level'
                    )
                    OR NOT EXISTS (
                        SELECT 1 FROM acousticbrainz_analysis_metadata AS ab
                        WHERE ab.spotify_track_id = t.id
                          AND ab.analysis_level = 'high-level'
                    )
                ))
            )
            ORDER BY t.id
            LIMIT %s
        """
        factory = self._get_database_factory()
        with factory(database_url) as database:
            database.cursor.execute(
                query,
                (
                    include_lastfm,
                    include_musicbrainz,
                    include_acousticbrainz,
                    limit,
                ),
            )
            rows = database.cursor.fetchall()
        return [dict(row) for row in rows]

    def save_results(self, database_url: str, records: list[dict[str, Any]]) -> int:
        if not records:
            return 0

        statements = {
            "lastfm": """INSERT INTO lastfm_track_metadata
                (spotify_track_id, match_status, normalized_data, raw_response)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (spotify_track_id) DO NOTHING""",
            "musicbrainz": """INSERT INTO musicbrainz_recording_metadata
                (spotify_track_id, match_status, recording_mbid,
                 normalized_data, raw_response)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (spotify_track_id) DO NOTHING""",
            "acousticbrainz": """INSERT INTO acousticbrainz_analysis_metadata
                (spotify_track_id, recording_mbid, analysis_level, match_status,
                 normalized_data, raw_response)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (spotify_track_id, analysis_level) DO NOTHING""",
        }

        inserted = 0
        factory = self._get_database_factory()
        with factory(database_url) as database:
            with database.transaction():
                for record in records:
                    provider = record["provider"]
                    statement = statements[provider]
                    normalized = record.get("normalized_data")
                    raw = record.get("raw_response")
                    values = {
                        "normalized_data": Jsonb(normalized) if normalized is not None else None,
                        "raw_response": Jsonb(raw) if raw is not None else None,
                    }
                    if provider == "lastfm":
                        parameters = (
                            record["spotify_track_id"],
                            record["match_status"],
                            values["normalized_data"],
                            values["raw_response"],
                        )
                    elif provider == "musicbrainz":
                        parameters = (
                            record["spotify_track_id"],
                            record["match_status"],
                            record.get("recording_mbid"),
                            values["normalized_data"],
                            values["raw_response"],
                        )
                    else:
                        parameters = (
                            record["spotify_track_id"],
                            record["recording_mbid"],
                            record["analysis_level"],
                            record["match_status"],
                            values["normalized_data"],
                            values["raw_response"],
                        )
                    database.cursor.execute(statement, parameters)
                    inserted += database.cursor.rowcount
        return inserted
