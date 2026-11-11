import os
from pathlib import Path
from urllib.parse import urlparse


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
DISPLAY_TIMEZONE = os.getenv("SPOTY_DISPLAY_TIMEZONE", "Europe/Rome").strip()
if not DISPLAY_TIMEZONE:
    DISPLAY_TIMEZONE = "Europe/Rome"

# Load credentials from environment variables.
# Never store real secrets in source code for public repositories.
CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
REDIRECT_URI = os.getenv("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8888/")

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()


def require_database_url(database_url: str | None = None) -> str:
    """Return a configured Neon PostgreSQL URL or fail at the point of use."""
    value = (
        database_url
        if database_url is not None
        else os.getenv("DATABASE_URL", DATABASE_URL)
    ).strip()
    if not value:
        raise ValueError("Missing DATABASE_URL.")

    try:
        parsed_url = urlparse(value)
        hostname = parsed_url.hostname or ""
    except ValueError:
        hostname = ""
        parsed_url = None

    if (
        parsed_url is None
        or parsed_url.scheme not in {"postgres", "postgresql"}
        or not hostname.lower().endswith(".neon.tech")
    ):
        raise ValueError("DATABASE_URL must be a Neon PostgreSQL URL.")
    return value

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
    }
}
DBSCHEMA_ALBUM = {
    "albums": {
        "id": "TEXT PRIMARY KEY NOT NULL",
        "name": "TEXT NOT NULL",
        "artists": "TEXT NOT NULL",  # JSON serialized list of artist IDs
        "tracks": "TEXT",  # JSON serialized list of track IDs
        "total_tracks": "INTEGER NOT NULL",
        "images": "TEXT",  # JSON serialized list of image URLs
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
        "disc_number": "INTEGER",
        "artists_ids": "TEXT NOT NULL",  # JSON serialized list of artist IDs
        "explicit": "INTEGER",  # 0 or 1
        "popularity": "INTEGER",
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
