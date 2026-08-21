from datetime import datetime
import time

from config import DBNAME, UTC_OFFSET, VERBOSE
from register.album import Album
from register.artist import Artist
from register.db import SimpleDB
from register.track import Track
from register.listeningevent import ListeningEvent
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


def save_liked_track_ids(limit=50, offset=0):
    """
    Fetch liked songs page by page and save their track IDs into the database.
    The provided limit is treated as the page size for each request. If a full
    round adds no new IDs, the function restarts from the beginning.
    """
    spotify = SpotifyClient()
    db = SimpleDB(DBNAME)

    page_size = max(1, limit)
    current_offset = offset
    saved_count = 0

    while True:
        liked_response = spotify.get_liked_songs(limit=page_size, offset=current_offset)
        if not liked_response or "items" not in liked_response:
            current_offset = 0
            continue

        items = liked_response["items"]
        if not items:
            current_offset = 0
            continue

        added_this_round = 0
        for item in items:
            track = item.get("track")
            track_id = track.get("id") if track else None
            if track_id:
                inserted = db.insert(
                    "liked_tracks", {"id": track_id}, print_only_insert=True
                )
                if inserted:
                    saved_count += 1
                    added_this_round += 1

        if added_this_round == 0:
            current_offset = 0
            continue

        if len(items) < page_size:
            current_offset = 0
            continue

        current_offset += len(items)

        print(f"Saved {saved_count} liked track IDs so far.") if VERBOSE else None


def register_listeningevents():
    spotify = SpotifyClient()
    db = SimpleDB(DBNAME)
    print("Starting Spotify tracker (single run)...")
    (
        print(
            f"---------------------------------\nFetching recently played tracks at {datetime.now()}"
        )
        if VERBOSE
        else None
    )
    start_fetch = time.perf_counter()
    print("Calling Spotify API for recently played...") if VERBOSE else None
    recently_played = spotify.get_recently_played(limit=50)
    elapsed = time.perf_counter() - start_fetch
    print(f"Spotify API response received in {elapsed:.2f}s") if VERBOSE else None
    if recently_played and "items" in recently_played:
        for item in recently_played["items"]:
            # track
            track = item["track"]
            track_id = track["id"]
            track_name = track["name"]
            track_duration_ms = track["duration_ms"]
            track_album_id = track["album"]["id"]
            track_album_track = track["track_number"]
            track_artists_ids = [artist["id"] for artist in track["artists"]]
            track_explicit = track["explicit"]
            _track = Track(
                id=track_id,
                name=track_name,
                duration_ms=track_duration_ms,
                album_id=track_album_id,
                album_track=track_album_track,
                artists_ids=track_artists_ids,
                explicit=track_explicit,
            )
            # print(f'Save TRACK: {track_name} ({track_id})') if VERBOSE else None
            _track.save(db, print_only_insert=True)
            # album
            album = track["album"]
            album_id = album["id"]
            album_name = album["name"]
            album_artists_ids = [artist["id"] for artist in album["artists"]]
            album_total_tracks = album["total_tracks"]
            album_images = (
                [img["url"] for img in album["images"]]
                if "images" in album
                else []
            )
            album_release_date = album["release_date"]
            album_release_date_precision = album["release_date_precision"]
            _album = Album(
                id=album_id,
                name=album_name,
                artists=album_artists_ids,
                tracks=[track_id],  # only save the current track, update later
                total_tracks=album_total_tracks,
                images=album_images,
                release_date=album_release_date,
                release_date_precision=album_release_date_precision,
            )
            # print(f'Save ALBUM: {album_name} ({album_id}) of year {_album.release_year}') if VERBOSE else None
            _album.save(db, print_only_insert=True)
            # artists
            artists = []
            for artist in track["artists"] + album["artists"]:
                artist_id = artist["id"]
                artist_name = artist["name"]
                _artist = Artist(id=artist_id, name=artist_name)
                artists.append(_artist)
                # print(f'Save ARTIST: {artist_name} ({artist_id})') if VERBOSE else None
                _artist.save(db, print_only_insert=True)
            # event
            date = datetime.fromisoformat(
                item["played_at"].replace("Z", UTC_OFFSET)
            )
            context_uri = item["context"]["uri"] if item["context"] else None
            event = ListeningEvent(
                track_id=track_id, played_at=date, context_uri=context_uri
            )
            # print(f'Save LISTENING EVENT: {track_name} at {date}') if VERBOSE else None
            event.save(db, print_only_insert=True)
    else:
        print("No recently played items received.") if VERBOSE else None
    # get missing metadata for tracks in listening_events that are not in tracks table
    # get_missing_track_metadata(db, spotify, limit=1)
    print("Single run completed.")


if __name__ == "__main__":
    register_listeningevents()
    # save_liked_track_ids(limit=50)
    # db = SimpleDB(DBNAME)
    # spotify = SpotifyClient()
    # get_missing_track_metadata(db, spotify, limit=1)
