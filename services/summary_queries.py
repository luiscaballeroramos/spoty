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
            from config import DATABASE_URL

            database_url = DATABASE_URL
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
