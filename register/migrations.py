import psycopg


SMARTERPLAYLISTS_COLUMNS = {
    "normalized_data", "field_metadata", "raw_response", "missing_requested",
    "captured_at", "last_attempt_status", "last_attempt_at", "last_success_at",
    "next_attempt_at", "last_error_code", "last_success_run_id",
}


def _smarterplaylists_statements() -> tuple[str, ...]:
    statements = ["""CREATE TABLE IF NOT EXISTS smarterplaylists_import_runs (
        capture_id TEXT PRIMARY KEY CHECK (capture_id ~ '^[a-f0-9]{64}$'),
        captured_at TIMESTAMPTZ NOT NULL,
        imported_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        record_count INTEGER NOT NULL CHECK (record_count > 0),
        details JSONB NOT NULL CHECK (jsonb_typeof(details) = 'object')
    )"""]
    for entity in ("track", "artist", "album"):
        table = f"smarterplaylists_{entity}_metadata"
        statements.extend((f"""CREATE TABLE IF NOT EXISTS {table} (
            spotify_{entity}_id TEXT PRIMARY KEY,
            normalized_data JSONB NOT NULL CHECK (jsonb_typeof(normalized_data) = 'object'),
            field_metadata JSONB NOT NULL CHECK (jsonb_typeof(field_metadata) = 'object'),
            raw_response JSONB,
            missing_requested JSONB NOT NULL CHECK (jsonb_typeof(missing_requested) = 'array'),
            captured_at TIMESTAMPTZ NOT NULL,
            last_attempt_status TEXT NOT NULL CHECK (
                last_attempt_status IN ('matched', 'partial', 'not_found', 'error', 'deferred')
            ),
            last_attempt_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            last_success_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            next_attempt_at TIMESTAMPTZ,
            last_error_code TEXT,
            last_success_run_id TEXT NOT NULL
        )""", f"CREATE INDEX IF NOT EXISTS {table}_next_attempt_idx ON {table} (next_attempt_at)"))
    statements.extend((
        _smarterplaylists_track_features_view(),
        _smarterplaylists_artist_details_view(),
        _smarterplaylists_album_details_view(),
    ))
    return tuple(statements)


def _smarterplaylists_track_features_view() -> str:
    features = (
        "acousticness", "danceability", "energy", "instrumentalness", "liveness",
        "loudness", "speechiness", "tempo", "valence", "key", "mode", "time_signature",
    )
    projections = []
    for field in features:
        kind = "INTEGER" if field in {"key", "mode", "time_signature"} else "DOUBLE PRECISION"
        projections.extend((
            f'(m.normalized_data ->> \'{field}\')::{kind} AS "{field}"',
            f"m.field_metadata -> '{field}' AS {field}_provenance",
        ))
    return f"""CREATE OR REPLACE VIEW smarterplaylists_track_features AS
        SELECT t.id AS spotify_track_id, t.name, {', '.join(projections)},
            t.popularity AS core_popularity,
            (m.normalized_data ->> 'popularity')::INTEGER AS resource_popularity,
            m.normalized_data, m.field_metadata, m.captured_at
        FROM tracks t LEFT JOIN smarterplaylists_track_metadata m
            ON m.spotify_track_id = t.id"""


def _smarterplaylists_artist_details_view() -> str:
    return """CREATE OR REPLACE VIEW smarterplaylists_artist_details AS
        SELECT a.id AS spotify_artist_id, a.name,
            (m.normalized_data ->> 'followers')::BIGINT AS resource_followers,
            (m.normalized_data ->> 'popularity')::INTEGER AS resource_popularity,
            m.normalized_data, m.field_metadata, m.captured_at
        FROM artists a LEFT JOIN smarterplaylists_artist_metadata m
            ON m.spotify_artist_id = a.id"""


def _smarterplaylists_album_details_view() -> str:
    return """CREATE OR REPLACE VIEW smarterplaylists_album_details AS
        SELECT a.id AS spotify_album_id, a.name,
            (m.normalized_data ->> 'popularity')::INTEGER AS resource_popularity,
            m.normalized_data ->> 'release_date' AS release_date,
            m.normalized_data ->> 'label' AS label,
            (m.normalized_data ->> 'total_tracks')::INTEGER AS resource_total_tracks,
            m.normalized_data, m.field_metadata, m.captured_at
        FROM albums a LEFT JOIN smarterplaylists_album_metadata m
            ON m.spotify_album_id = a.id"""


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
    (
        "0002_metadata_enrichment",
        (
            """CREATE TABLE IF NOT EXISTS lastfm_track_metadata (
                spotify_track_id TEXT PRIMARY KEY NOT NULL,
                match_status TEXT NOT NULL CHECK (match_status IN ('matched', 'not_found')),
                normalized_data JSONB,
                raw_response JSONB,
                fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CHECK (
                    (match_status = 'matched' AND normalized_data IS NOT NULL)
                    OR (match_status = 'not_found' AND normalized_data IS NULL)
                )
            )""",
            """CREATE TABLE IF NOT EXISTS musicbrainz_recording_metadata (
                spotify_track_id TEXT PRIMARY KEY NOT NULL,
                match_status TEXT NOT NULL CHECK (
                    match_status IN ('matched', 'not_found', 'ambiguous')
                ),
                recording_mbid TEXT,
                normalized_data JSONB,
                raw_response JSONB,
                fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CHECK (
                    (match_status = 'matched' AND recording_mbid IS NOT NULL
                        AND normalized_data IS NOT NULL)
                    OR (match_status <> 'matched' AND recording_mbid IS NULL
                        AND normalized_data IS NULL)
                )
            )""",
            """CREATE TABLE IF NOT EXISTS acousticbrainz_analysis_metadata (
                spotify_track_id TEXT NOT NULL,
                recording_mbid TEXT NOT NULL,
                analysis_level TEXT NOT NULL CHECK (
                    analysis_level IN ('low-level', 'high-level')
                ),
                match_status TEXT NOT NULL CHECK (match_status IN ('found', 'not_found')),
                normalized_data JSONB,
                raw_response JSONB,
                fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (spotify_track_id, analysis_level),
                CHECK (
                    (match_status = 'found' AND normalized_data IS NOT NULL)
                    OR (match_status = 'not_found' AND normalized_data IS NULL)
                )
            )""",
        ),
    ),
    ("0003_smarterplaylists_metadata", _smarterplaylists_statements()),
    ("0004_smarterplaylists_refresh_state", (
        """CREATE TABLE IF NOT EXISTS smarterplaylists_refresh_state (
            entity_type TEXT NOT NULL CHECK (entity_type IN ('track', 'artist', 'album')),
            spotify_id TEXT NOT NULL,
            last_attempt_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            last_status TEXT NOT NULL CHECK (last_status IN ('matched', 'partial', 'error', 'deferred')),
            next_attempt_at TIMESTAMPTZ NOT NULL,
            consecutive_failures INTEGER NOT NULL DEFAULT 0 CHECK (consecutive_failures >= 0),
            last_error_code TEXT,
            PRIMARY KEY (entity_type, spotify_id)
        )""",
        """CREATE INDEX IF NOT EXISTS smarterplaylists_refresh_due_idx
            ON smarterplaylists_refresh_state (entity_type, next_attempt_at)""",
    )),
    ("0005_remove_non_spotify_core_columns", (
        "DROP VIEW IF EXISTS smarterplaylists_album_details",
        "ALTER TABLE artists DROP COLUMN IF EXISTS start_year",
        "ALTER TABLE artists DROP COLUMN IF EXISTS end_year",
        "ALTER TABLE albums DROP COLUMN IF EXISTS popularity",
        "ALTER TABLE tracks DROP COLUMN IF EXISTS genres",
        "ALTER TABLE tracks DROP COLUMN IF EXISTS images",
        _smarterplaylists_album_details_view(),
    )),
    ("0006_prune_empty_catalog_columns", (
        "DROP VIEW IF EXISTS smarterplaylists_track_features",
        "DROP VIEW IF EXISTS smarterplaylists_artist_details",
        "ALTER TABLE artists DROP COLUMN IF EXISTS followers",
        "ALTER TABLE artists DROP COLUMN IF EXISTS images",
        "ALTER TABLE artists DROP COLUMN IF EXISTS popularity",
        "ALTER TABLE albums DROP COLUMN IF EXISTS disc_number",
        "ALTER TABLE tracks DROP COLUMN IF EXISTS acousticness",
        "ALTER TABLE tracks DROP COLUMN IF EXISTS danceability",
        "ALTER TABLE tracks DROP COLUMN IF EXISTS energy",
        "ALTER TABLE tracks DROP COLUMN IF EXISTS instrumentalness",
        "ALTER TABLE tracks DROP COLUMN IF EXISTS key",
        "ALTER TABLE tracks DROP COLUMN IF EXISTS liveness",
        "ALTER TABLE tracks DROP COLUMN IF EXISTS loudness",
        "ALTER TABLE tracks DROP COLUMN IF EXISTS mode",
        "ALTER TABLE tracks DROP COLUMN IF EXISTS speechiness",
        "ALTER TABLE tracks DROP COLUMN IF EXISTS tempo",
        "ALTER TABLE tracks DROP COLUMN IF EXISTS time_signature",
        "ALTER TABLE tracks DROP COLUMN IF EXISTS valence",
        "ALTER TABLE tracks ADD COLUMN IF NOT EXISTS disc_number INTEGER",
        _smarterplaylists_track_features_view(),
        _smarterplaylists_artist_details_view(),
    )),
    ("0007_genre_taxonomy", (
        """CREATE TABLE genres (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL CHECK (btrim(name) <> ''),
            introduced_by TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )""",
        """CREATE TABLE genre_relations (
            parent_id TEXT NOT NULL REFERENCES genres(id),
            child_id TEXT NOT NULL REFERENCES genres(id),
            introduced_by TEXT NOT NULL,
            PRIMARY KEY (parent_id, child_id),
            CHECK (parent_id <> child_id)
        )""",
        "CREATE INDEX genre_relations_child_idx ON genre_relations (child_id)",
        """CREATE TABLE genre_label_mappings (
            source TEXT NOT NULL,
            field TEXT NOT NULL,
            normalized_label TEXT NOT NULL CHECK (
                normalized_label <> '' AND normalized_label = lower(btrim(normalized_label))
            ),
            genre_id TEXT REFERENCES genres(id),
            status TEXT NOT NULL CHECK (status IN ('pending', 'approved', 'excluded')),
            introduced_by TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (source, field, normalized_label),
            CHECK ((status = 'approved' AND genre_id IS NOT NULL)
                OR (status IN ('pending', 'excluded') AND genre_id IS NULL))
        )""",
    )),
)

EXPECTED_COLUMNS = {
    "listening_events": {
        "track_id", "played_at", "context_source", "context_type", "context_id"
    },
    "artists": {
        "id", "name"
    },
    "albums": {
        "id", "name", "artists", "tracks", "total_tracks", "images", "release_year"
    },
    "tracks": {
        "id", "name", "duration_ms", "album_id", "album_track", "disc_number",
        "artists_ids", "explicit", "popularity"
    },
    "liked_tracks": {"id"},
    "liked_sync_state": {
        "singleton_id", "generation", "local_revision", "applied_generation"
    },
    "lastfm_track_metadata": {
        "spotify_track_id", "match_status", "normalized_data", "raw_response",
        "fetched_at",
    },
    "musicbrainz_recording_metadata": {
        "spotify_track_id", "match_status", "recording_mbid", "normalized_data",
        "raw_response", "fetched_at",
    },
    "acousticbrainz_analysis_metadata": {
        "spotify_track_id", "recording_mbid", "analysis_level", "match_status",
        "normalized_data", "raw_response", "fetched_at",
    },
}

EXPECTED_COLUMNS["smarterplaylists_import_runs"] = {
    "capture_id", "captured_at", "imported_at", "record_count", "details",
}
EXPECTED_COLUMNS["smarterplaylists_refresh_state"] = {
    "entity_type", "spotify_id", "last_attempt_at", "last_status", "next_attempt_at",
    "consecutive_failures", "last_error_code",
}
EXPECTED_COLUMNS["genres"] = {"id", "name", "introduced_by", "created_at"}
EXPECTED_COLUMNS["genre_relations"] = {"parent_id", "child_id", "introduced_by"}
EXPECTED_COLUMNS["genre_label_mappings"] = {
    "source", "field", "normalized_label", "genre_id", "status", "introduced_by", "created_at",
}
for _entity in ("track", "artist", "album"):
    EXPECTED_COLUMNS[f"smarterplaylists_{_entity}_metadata"] = (
        SMARTERPLAYLISTS_COLUMNS | {f"spotify_{_entity}_id"}
    )


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
                cursor.execute(
                    "INSERT INTO schema_migrations (version) VALUES (%s)",
                    (version,),
                )
                applied_now.append(version)

            _verify_schema(cursor)
    return applied_now


def migrate(database_url: str) -> list[str]:
    from config import require_database_url

    with psycopg.connect(require_database_url(database_url)) as connection:
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
