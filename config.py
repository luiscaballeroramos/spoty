VERBOSE = True

# TODO: Move these to environment variables for better security and flexibility
CLIENT_ID = "1562b563518b40848fa76d89a37609a3"
CLIENT_SECRET = "ba1248d2aac04918aab2a7af32978113"
REDIRECT_URI = "http://127.0.0.1:8888/"

UTC_OFFSET = "+02:00"  # Adjust this if you want to store times in a specific timezone instead of UTC

DBNAME = "spotify.db"
DBSCHEMA = {
    "listening_events": {
        "track_id": "TEXT NOT NULL",
        "played_at": "INTEGER NOT NULL",
        "context_source": "TEXT",
        "context_type": "TEXT",
        "context_id": "TEXT",
        "UNIQUE(track_id, played_at)": ""
    }
}
