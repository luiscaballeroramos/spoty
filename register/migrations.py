import psycopg


MIGRATIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "0001_initial_schema",
        (
            """CREATE TABLE IF NOT EXISTS listening_events (
                track_id TEXT NOT NULL,
                played_at INTEGER NOT NULL,
                context_source TEXT,
                context_type TEXT,
                context_id TEXT,
                UNIQUE(track_id, played_at)
            )""",
            """CREATE TABLE IF NOT EXISTS artists (
                id TEXT PRIMARY KEY NOT NULL,
                name TEXT NOT NULL,
                end_year INTEGER,
                followers INTEGER,
                images TEXT,
                popularity INTEGER,
                start_year INTEGER
            )""",
            """CREATE TABLE IF NOT EXISTS albums (
                id TEXT PRIMARY KEY NOT NULL,
                name TEXT NOT NULL,
                artists TEXT NOT NULL,
                tracks TEXT,
                total_tracks INTEGER NOT NULL,
                disc_number INTEGER,
                images TEXT,
                popularity INTEGER,
                release_year INTEGER
            )""",
            """CREATE TABLE IF NOT EXISTS tracks (
                id TEXT PRIMARY KEY NOT NULL,
                name TEXT NOT NULL,
                duration_ms INTEGER NOT NULL,
                album_id TEXT NOT NULL,
                album_track INTEGER,
                artists_ids TEXT NOT NULL,
                acousticness REAL,
                danceability REAL,
                energy REAL,
                explicit INTEGER,
                genres TEXT,
                images TEXT,
                instrumentalness REAL,
                key INTEGER,
                liveness REAL,
                loudness REAL,
                mode INTEGER,
                popularity INTEGER,
                speechiness REAL,
                tempo REAL,
                time_signature INTEGER,
                valence REAL
            )""",
            """CREATE TABLE IF NOT EXISTS liked_tracks (
                id TEXT PRIMARY KEY NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS liked_sync_state (
                singleton_id SMALLINT PRIMARY KEY CHECK (singleton_id = 1),
                generation BIGINT NOT NULL DEFAULT 0,
                local_revision BIGINT NOT NULL DEFAULT 0,
                applied_generation BIGINT NOT NULL DEFAULT 0
            )""",
            "INSERT INTO liked_sync_state (singleton_id) VALUES (1) ON CONFLICT DO NOTHING",
        ),
    ),
)

EXPECTED_COLUMNS = {
    "listening_events": {
        "track_id", "played_at", "context_source", "context_type", "context_id"
    },
    "artists": {
        "id", "name", "end_year", "followers", "images", "popularity", "start_year"
    },
    "albums": {
        "id", "name", "artists", "tracks", "total_tracks", "disc_number",
        "images", "popularity", "release_year"
    },
    "tracks": {
        "id", "name", "duration_ms", "album_id", "album_track", "artists_ids",
        "acousticness", "danceability", "energy", "explicit", "genres", "images",
        "instrumentalness", "key", "liveness", "loudness", "mode", "popularity",
        "speechiness", "tempo", "time_signature", "valence"
    },
    "liked_tracks": {"id"},
    "liked_sync_state": {
        "singleton_id", "generation", "local_revision", "applied_generation"
    },
}


def _verify_schema(cursor) -> None:
    cursor.execute("""
        SELECT table_name, column_name
        FROM information_schema.columns
        WHERE table_schema = current_schema()
    """)
    actual: dict[str, set[str]] = {}
    for table_name, column_name in cursor.fetchall():
        actual.setdefault(table_name, set()).add(column_name)

    missing = {
        table: sorted(columns - actual.get(table, set()))
        for table, columns in EXPECTED_COLUMNS.items()
        if columns - actual.get(table, set())
    }
    if missing:
        raise RuntimeError(f"Database schema is missing required columns: {missing}")

    cursor.execute("""
        SELECT EXISTS (
            SELECT 1
            FROM pg_constraint
            WHERE conrelid = to_regclass('listening_events')
              AND contype = 'u'
              AND upper(replace(replace(pg_get_constraintdef(oid), ' ', ''), '"', ''))
                  = 'UNIQUE(TRACK_ID,PLAYED_AT)'
        )
    """)
    if not cursor.fetchone()[0]:
        raise RuntimeError(
            "Database schema is missing UNIQUE(track_id, played_at) on listening_events."
        )


def apply_migrations(connection) -> list[str]:
    applied_now = []
    with connection.transaction():
        with connection.cursor() as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version TEXT PRIMARY KEY,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            cursor.execute("SELECT version FROM schema_migrations")
            applied = {row[0] for row in cursor.fetchall()}

            for version, statements in MIGRATIONS:
                if version in applied:
                    continue
                for statement in statements:
                    cursor.execute(statement)
                _verify_schema(cursor)
                cursor.execute(
                    "INSERT INTO schema_migrations (version) VALUES (%s)",
                    (version,),
                )
                applied_now.append(version)

            _verify_schema(cursor)
    return applied_now


def migrate(database_url: str) -> list[str]:
    with psycopg.connect(database_url) as connection:
        return apply_migrations(connection)


def main() -> None:
    from config import DATABASE_URL

    applied = migrate(DATABASE_URL)
    if applied:
        print(f"Applied migrations: {', '.join(applied)}")
    else:
        print("Database schema is already up to date.")


if __name__ == "__main__":
    main()
