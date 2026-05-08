VERBOSE = True

# TODO: Move these to environment variables for better security and flexibility
CLIENT_ID = "1562b563518b40848fa76d89a37609a3"
CLIENT_SECRET = "ba1248d2aac04918aab2a7af32978113"
REDIRECT_URI = "http://127.0.0.1:8888/"

UTC_OFFSET = "+02:00"  # Adjust this if you want to store times in a specific timezone instead of UTC

DBNAME = "spotify.db"
DBSCHEMA_LISTENINGEVENT = {
    "listening_events": {
        "track_id": "TEXT NOT NULL",
        "played_at": "INTEGER NOT NULL",
        "context_source": "TEXT",
        "context_type": "TEXT",
        "context_id": "TEXT",
        "UNIQUE(track_id, played_at)": ""
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
        "start_year": "INTEGER"
    }
}
DBSCHEMA_ALBUM = {
    "albums": {
        "id": "TEXT PRIMARY KEY NOT NULL",
        "name": "TEXT NOT NULL",
        "artists": "TEXT NOT NULL",  # JSON serialized list of artist IDs
        "tracks": "TEXT NOT NULL",  # JSON serialized list of track IDs
        "total_tracks": "INTEGER NOT NULL",
        "disc_number": "INTEGER",
        "images": "TEXT",  # JSON serialized list of image URLs
        "popularity": "INTEGER",
        "release_date": "TEXT",
        "release_date_precision": "TEXT",
        "release_decade": "INTEGER",
        "release_year": "INTEGER"
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
        "valence": "REAL"
    }
}

# Combine schemas
DBSCHEMA = {**DBSCHEMA_LISTENINGEVENT, **DBSCHEMA_ARTIST, **DBSCHEMA_ALBUM, **DBSCHEMA_TRACK}
