from typing import Any, Callable

from psycopg.rows import dict_row


class SummaryQueryService:
    """Read dashboard summaries and listening history from PostgreSQL."""

    def __init__(
        self,
        database_url: str | None = None,
        connect_factory: Callable[..., Any] | None = None,
    ) -> None:
        self._database_url = database_url
        self._connect_factory = connect_factory

    def _query(self, query: str, parameters: tuple[Any, ...] | None = None):
        database_url = self._database_url
        if database_url is None:
            from config import require_database_url

            database_url = require_database_url()
        else:
            from config import require_database_url

            database_url = require_database_url(database_url)
        connect_factory = self._connect_factory
        if connect_factory is None:
            from psycopg import connect

            connect_factory = connect

        with connect_factory(database_url, row_factory=dict_row) as connection:
            with connection.cursor() as cursor:
                if parameters is None:
                    cursor.execute(query)
                else:
                    cursor.execute(query, parameters)
                return cursor.fetchall()

    def dashboard_signature(self) -> tuple[str, int, int, int, int, int, int]:
        rows = self._query("""
            WITH latest_event AS (
                SELECT track_id, played_at
                FROM listening_events
                ORDER BY played_at DESC, track_id DESC
                LIMIT 1
            )
            SELECT
                COALESCE((SELECT track_id FROM latest_event), '') AS latest_track_id,
                COALESCE((SELECT played_at FROM latest_event), 0) AS latest_played_at,
                (SELECT COUNT(*) FROM tracks) AS total_tracks,
                (SELECT COUNT(*) FROM artists) AS total_artists,
                (SELECT COUNT(*) FROM albums) AS total_albums,
                (SELECT COUNT(*) FROM liked_tracks) AS total_liked_tracks,
                (SELECT COUNT(*) FROM listening_events) AS total_listening_events
        """)
        if not rows:
            return ("", 0, 0, 0, 0, 0, 0)
        row = rows[0]
        return (
            str(row.get("latest_track_id") or ""),
            int(row.get("latest_played_at") or 0),
            int(row.get("total_tracks") or 0),
            int(row.get("total_artists") or 0),
            int(row.get("total_albums") or 0),
            int(row.get("total_liked_tracks") or 0),
            int(row.get("total_listening_events") or 0),
        )

    def library_counts(self) -> dict[str, int]:
        rows = self._query("""
            SELECT
                (SELECT COUNT(*) FROM tracks) AS total_tracks,
                (SELECT COUNT(*) FROM artists) AS total_artists,
                (SELECT COUNT(*) FROM albums) AS total_albums,
                (SELECT COUNT(*) FROM liked_tracks) AS total_liked_tracks,
                (SELECT COUNT(*) FROM listening_events) AS total_listening_events
        """)
        if not rows:
            return {
                "total_tracks": 0,
                "total_artists": 0,
                "total_albums": 0,
                "total_liked_tracks": 0,
                "total_listening_events": 0,
            }
        return {key: int(value or 0) for key, value in rows[0].items()}

    def top_listened_genres(self, limit: int = 10) -> dict[str, Any]:
        """Count each listen fully for every distinct track genre and report coverage."""
        rows = self._query("""
            WITH event_tracks AS (
                SELECT track_id, COUNT(*)::numeric AS plays
                FROM listening_events
                GROUP BY track_id
            ), track_artists AS (
                SELECT et.track_id, et.plays, artist.artist_id
                FROM event_tracks et
                JOIN tracks t ON t.id = et.track_id
                CROSS JOIN LATERAL jsonb_array_elements_text(
                    CASE WHEN jsonb_typeof(t.artists_ids::jsonb) = 'array'
                         THEN t.artists_ids::jsonb ELSE '[]'::jsonb END
                ) AS artist(artist_id)
            ), track_genres AS (
                SELECT DISTINCT ta.track_id, lower(btrim(genre.value)) AS genre
                FROM track_artists ta
                JOIN smarterplaylists_artist_metadata m
                    ON m.spotify_artist_id = ta.artist_id
                CROSS JOIN LATERAL jsonb_array_elements_text(
                    CASE WHEN jsonb_typeof(m.normalized_data->'genres') = 'array'
                         THEN m.normalized_data->'genres' ELSE '[]'::jsonb END
                ) AS genre(value)
                WHERE btrim(genre.value) <> ''
            ), track_genre_counts AS (
                SELECT track_id, COUNT(*) AS genre_count
                FROM track_genres
                GROUP BY track_id
            ), weighted_genres AS (
                SELECT tg.genre, SUM(et.plays) AS weighted_plays
                FROM track_genres tg
                JOIN event_tracks et USING (track_id)
                GROUP BY tg.genre
            ), coverage AS (
                SELECT COALESCE(SUM(et.plays), 0) AS total_listens,
                    COALESCE(SUM(et.plays) FILTER (WHERE gc.track_id IS NOT NULL), 0)
                        AS classified_listens,
                    COUNT(*) AS listened_tracks,
                    COUNT(gc.track_id) AS classified_tracks
                FROM event_tracks et
                LEFT JOIN track_genre_counts gc USING (track_id)
            )
            SELECT coverage.total_listens, coverage.classified_listens,
                coverage.listened_tracks, coverage.classified_tracks,
                top_genres.genre, top_genres.weighted_plays
            FROM coverage
            LEFT JOIN LATERAL (
                SELECT genre, weighted_plays
                FROM weighted_genres
                ORDER BY weighted_plays DESC, genre
                LIMIT %s
            ) AS top_genres ON TRUE
        """, (max(1, min(20, int(limit))),))

        if not rows:
            return {
                "total_listens": 0,
                "classified_listens": 0,
                "listened_tracks": 0,
                "classified_tracks": 0,
                "genres": [],
            }

        first_row = rows[0]
        classified_listens = int(first_row["classified_listens"] or 0)
        genres = [
            {
                "genre": row["genre"],
                "weighted_listens": float(row["weighted_plays"] or 0),
                "share_percent": (
                    100 * float(row["weighted_plays"] or 0) / classified_listens
                    if classified_listens else 0.0
                ),
            }
            for row in rows
            if row["genre"] is not None
        ]
        return {
            "total_listens": int(first_row["total_listens"] or 0),
            "classified_listens": classified_listens,
            "listened_tracks": int(first_row["listened_tracks"] or 0),
            "classified_tracks": int(first_row["classified_tracks"] or 0),
            "genres": genres,
        }

    def recent_listening_events(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self._query("""
            WITH track_stats AS (
                SELECT
                    track_id,
                    COUNT(*) AS track_play_count,
                    CEIL(
                        100.0
                        * ROW_NUMBER() OVER (ORDER BY COUNT(*) DESC, track_id)
                        / NULLIF(COUNT(*) OVER (), 0)
                    )::int AS track_top_percent
                FROM listening_events
                GROUP BY track_id
            )
            SELECT
                le.played_at,
                le.track_id,
                ts.track_play_count,
                ts.track_top_percent,
                t.name AS track_name,
                t.duration_ms,
                t.artists_ids,
                a.name AS album_name,
                a.images AS album_images,
                a.release_year
            FROM listening_events AS le
            LEFT JOIN track_stats AS ts ON le.track_id = ts.track_id
            LEFT JOIN tracks AS t ON le.track_id = t.id
            LEFT JOIN albums AS a ON t.album_id = a.id
            ORDER BY le.played_at DESC
            LIMIT %s
        """, (max(1, limit),))
        return list(rows)

    def artist_names(self, artist_ids: list[str]) -> dict[str, str]:
        if not artist_ids:
            return {}
        rows = self._query(
            "SELECT id, name FROM artists WHERE id = ANY(%s)",
            (list(dict.fromkeys(artist_ids)),),
        )
        return {row["id"]: row["name"] for row in rows}
