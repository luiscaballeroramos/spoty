import os
from pathlib import Path
from urllib.parse import quote_plus


def _load_env_file() -> None:
    env_path = Path(__file__).resolve().with_name(".env")
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        if line.startswith("export "):
            line = line[len("export ") :].strip()

        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()

        if not key or key in os.environ:
            continue

        if (
            len(value) >= 2
            and value[0] == value[-1]
            and value[0] in {'"', "'"}
        ):
            value = value[1:-1]

        os.environ[key] = value


_load_env_file()

VERBOSE = True

# Load credentials from environment variables.
# Never store real secrets in source code for public repositories.
CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
REDIRECT_URI = os.getenv("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8888/")

if not CLIENT_ID or not CLIENT_SECRET:
    raise ValueError(
        "Missing Spotify credentials. Set SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET as environment variables."
    )

UTC_OFFSET = "+02:00"  # Adjust this if you want to store times in a specific timezone instead of UTC

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    pg_host = os.getenv("PGHOST", "127.0.0.1")
    pg_port = os.getenv("PGPORT", "5432")
    pg_database = os.getenv("PGDATABASE", "spoty")
    pg_user = os.getenv("PGUSER", "postgres")
    pg_password = os.getenv("PGPASSWORD", "postgres")
    pg_sslmode = os.getenv("PGSSLMODE")
    if not pg_sslmode and pg_host.endswith(".neon.tech"):
        pg_sslmode = "require"
    ssl_query = f"?sslmode={quote_plus(pg_sslmode)}" if pg_sslmode else ""
    DATABASE_URL = "postgresql://neondb_owner:npg_F9mJti7dGkvy@ep-calm-fire-ayoxylsp-pooler.c-5.us-east-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require"

# Backward-compatible alias used by existing scripts.
DBNAME = DATABASE_URL
DBSCHEMA_LISTENINGEVENT = {
    "listening_events": {
        "track_id": "TEXT NOT NULL",
        "played_at": "INTEGER NOT NULL",
        "context_source": "TEXT",
        "context_type": "TEXT",
        "context_id": "TEXT",
        "UNIQUE(track_id, played_at)": "",
    }
}
DBSCHEMA_ARTIST = {
    "artists": {
        "id": "TEXT PRIMARY KEY NOT NULL",
        "name": "TEXT NOT NULL",
        "end_year": "INTEGER",
        "followers": "INTEGER",
        "images": "TEXT",
        "popularity": "INTEGER",
        "start_year": "INTEGER",
    }
}
DBSCHEMA_ALBUM = {
    "albums": {
        "id": "TEXT PRIMARY KEY NOT NULL",
        "name": "TEXT NOT NULL",
        "artists": "TEXT NOT NULL",  # JSON serialized list of artist IDs
        "tracks": "TEXT",  # JSON serialized list of track IDs
        "total_tracks": "INTEGER NOT NULL",
        "disc_number": "INTEGER",
        "images": "TEXT",  # JSON serialized list of image URLs
        "popularity": "INTEGER",
        "release_year": "INTEGER",
    }
}
DBSCHEMA_TRACK = {
    "tracks": {
        "id": "TEXT PRIMARY KEY NOT NULL",
        "name": "TEXT NOT NULL",
        "duration_ms": "INTEGER NOT NULL",
        "album_id": "TEXT NOT NULL",  # Foreign key to albums.id
        "album_track": "INTEGER",
        "artists_ids": "TEXT NOT NULL",  # JSON serialized list of artist IDs
        "acousticness": "REAL",
        "danceability": "REAL",
        "energy": "REAL",
        "explicit": "INTEGER",  # 0 or 1
        "genres": "TEXT",  # JSON serialized list of genres
        "images": "TEXT",  # JSON serialized list of image URLs
        "instrumentalness": "REAL",
        "key": "INTEGER",
        "liveness": "REAL",
        "loudness": "REAL",
        "mode": "INTEGER",
        "popularity": "INTEGER",
        "speechiness": "REAL",
        "tempo": "REAL",
        "time_signature": "INTEGER",
        "valence": "REAL",
    }
}
DBSCHEMA_LIKED_TRACK = {"liked_tracks": {"id": "TEXT PRIMARY KEY NOT NULL"}}

# Combine schemas
DBSCHEMA = {
    **DBSCHEMA_LISTENINGEVENT,
    **DBSCHEMA_ARTIST,
    **DBSCHEMA_ALBUM,
    **DBSCHEMA_TRACK,
    **DBSCHEMA_LIKED_TRACK,
}
