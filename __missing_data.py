from config import DBNAME, VERBOSE
from register.album import Album
from register.artist import Artist
from register.db import SimpleDB
from register.track import Track
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
            # Extract track information
            track_id = track_data["id"]
            track_name = track_data["name"]
            track_duration_ms = track_data["duration_ms"]
            track_album_id = track_data["album"]["id"]
            track_album_track = track_data["track_number"]
            track_disc_number = track_data.get("disc_number")
            track_artists_ids = [artist["id"] for artist in track_data["artists"]]
            track_explicit = track_data["explicit"]
            track_popularity = track_data.get("popularity")
            # Create and save Track
            _track = Track(
                id=track_id,
                name=track_name,
                duration_ms=track_duration_ms,
                album_id=track_album_id,
                album_track=track_album_track,
                artists_ids=track_artists_ids,
                explicit=track_explicit,
                popularity=track_popularity,
            )
            _track.save(db, print_only_insert=True)

            # Extract album information
            album = track_data["album"]
            album_id = album["id"]
            album_name = album["name"]
            album_artists_ids = [artist["id"] for artist in album["artists"]]
            album_total_tracks = album["total_tracks"]
            album_images = (
                [img["url"] for img in album["images"]] if "images" in album else []
            )
            album_release_date = album.get("release_date")
            album_release_date_precision = album.get("release_date_precision")
            album_popularity = album.get("popularity")
            #  Create and save Album
            _album = Album(
                id=album_id,
                name=album_name,
                artists=album_artists_ids,
                tracks=[track_id],
                total_tracks=album_total_tracks,
                images=album_images,
                release_date=album_release_date,
                release_date_precision=album_release_date_precision,
                popularity=album_popularity,
            )
            _album.save(db, print_only_insert=True)
            # Extract artists information (from both track and album)
            for artist in track_data["artists"] + album["artists"]:
                artist_id = artist["id"]
                artist_name = artist["name"]
                # Create and save Artist
                _artist = Artist(id=artist_id, name=artist_name)
                _artist.save(db, print_only_insert=True)

            saved_count += 1

    print(f"Saved metadata for {saved_count} tracks.") if VERBOSE else None


if __name__ == "__main__":
    db = SimpleDB(DBNAME)
    spotify = SpotifyClient()
    get_missing_track_metadata(db, spotify, limit=1)
