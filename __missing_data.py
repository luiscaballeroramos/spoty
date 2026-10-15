from config import DBNAME, VERBOSE
from register.db import SimpleDB
from register.spotify_data import models_from_spotify_track
from spotifyapi.spotifyclient import SpotifyClient


def get_missing_track_metadata(db: SimpleDB, spotify: SpotifyClient, limit=1):
    """
    Retrieve Spotify metadata for missing tracks and save them to the database.
    Missing tracks are track_ids that appear in listening_events or liked_tracks
    but are not present in the tracks table.
    """
    query = """
        SELECT DISTINCT track_id
        FROM (
            SELECT track_id FROM listening_events
            UNION
            SELECT id AS track_id FROM liked_tracks
        ) AS combined
        LEFT JOIN tracks t ON combined.track_id = t.id
        WHERE t.id IS NULL
    """
    rows = db.cursor.execute(query).fetchall()
    missing_ids = [row["track_id"] for row in rows]
    # TODO: si existe no se inserta de nuevo, pero y si lo que seq uiere es ampliar la metadata disponible¿?
    saved_count = 0
    for track_id in missing_ids[:limit]:
        track_data = spotify.get_track_byid(track_id)
        if track_data:
            _track, _album, artists = models_from_spotify_track(track_data)
            _track.save(db, print_only_insert=True)

            _album.save(db, print_only_insert=True)
            for artist in artists:
                artist.save(db, print_only_insert=True)

            saved_count += 1

    print(f"Saved metadata for {saved_count} tracks.") if VERBOSE else None


if __name__ == "__main__":
    db = SimpleDB(DBNAME)
    spotify = SpotifyClient()
    get_missing_track_metadata(db, spotify, limit=1)
