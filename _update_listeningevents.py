import sqlite3

from register.db import SimpleDB
from config import DBNAME
from register.artist import  get_artists_bytrackid
from spotifyapi.spotifyclient import SpotifyClient

# Initialize the database connection
db = SimpleDB(DBNAME)

# Set the row factory for the SQLite connection to return rows as dictionaries
db.conn.row_factory = sqlite3.Row

# Initialize Spotify client
spotify = SpotifyClient()

# Fetch all listening events
tracks_ids = db.cursor.execute("SELECT track_id FROM listening_events").fetchall()

# Insert artists into the artist table
k = 0
for row in tracks_ids:
    track_id = row["track_id"]  # Access column by name
    artists = get_artists_bytrackid(track_id, spotify)
    for artist in artists:
        print(f'Artist: {artist.name}, ID: {artist.id}, images: {len(artist.images)}')
        # artist.save(db, print_only_insert=True)
        k += 1

print(f"{k} artists have been added to the artist table.")
