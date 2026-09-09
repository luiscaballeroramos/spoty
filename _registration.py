from datetime import datetime
import time

from config import DBNAME, UTC_OFFSET, VERBOSE
from register.album import Album
from register.artist import Artist
from register.db import SimpleDB
from register.track import Track
from register.listeningevent import ListeningEvent
from spotifyapi.spotifyclient import SpotifyClient


def register_listeningevents():
    spotify = SpotifyClient()
    db = SimpleDB(DBNAME)
    inserted_tracks = 0
    inserted_albums = 0
    inserted_artists = 0
    inserted_listening_events = 0
    skipped_existing_listening_events = 0
    fetched_items = 0

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
        fetched_items = len(recently_played["items"])
        candidate_events = []

        for item in recently_played["items"]:
            track_id = (item.get("track") or {}).get("id")
            played_at = item.get("played_at")

            if not track_id or not played_at:
                continue

            date = datetime.fromisoformat(played_at.replace("Z", UTC_OFFSET))
            played_at_ts = int(date.timestamp())
            candidate_events.append((item, track_id, date, played_at_ts))

        existing_event_keys = set()
        if candidate_events:
            track_ids = list({track_id for _, track_id, _, _ in candidate_events})
            played_at_values = [played_at_ts for _, _, _, played_at_ts in candidate_events]
            existing_event_keys = db.get_listening_event_keys(
                track_ids=track_ids,
                min_played_at=min(played_at_values),
                max_played_at=max(played_at_values),
            )

        for item, track_id, date, played_at_ts in candidate_events:
            event_key = (track_id, played_at_ts)

            if event_key in existing_event_keys:
                skipped_existing_listening_events += 1
                continue

            # track
            track = item["track"]
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
            if _track.save(db, print_only_insert=True):
                inserted_tracks += 1
            # album
            album = track["album"]
            album_id = album["id"]
            album_name = album["name"]
            album_artists_ids = [artist["id"] for artist in album["artists"]]
            album_total_tracks = album["total_tracks"]
            album_images = (
                [img["url"] for img in album["images"]] if "images" in album else []
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
            if _album.save(db, print_only_insert=True):
                inserted_albums += 1
            # artists
            for artist in track["artists"] + album["artists"]:
                artist_id = artist["id"]
                artist_name = artist["name"]
                _artist = Artist(id=artist_id, name=artist_name)
                # print(f'Save ARTIST: {artist_name} ({artist_id})') if VERBOSE else None
                if _artist.save(db, print_only_insert=True):
                    inserted_artists += 1
            # event
            context = item.get("context") or {}
            context_uri = context.get("uri")
            event = ListeningEvent(
                track_id=track_id, played_at=date, context_uri=context_uri
            )
            # print(f'Save LISTENING EVENT: {track_name} at {date}') if VERBOSE else None
            if event.save(db, print_only_insert=True):
                inserted_listening_events += 1
            existing_event_keys.add(event_key)

        (
            print(
                f"Skipped {skipped_existing_listening_events} listening events already stored"
            )
            if VERBOSE
            else None
        )
    else:
        print("No recently played items received.") if VERBOSE else None
    # get missing metadata for tracks in listening_events that are not in tracks table
    # get_missing_track_metadata(db, spotify, limit=1)
    print("Single run completed.")

    return {
        "fetched_items": fetched_items,
        "inserted_tracks": inserted_tracks,
        "inserted_albums": inserted_albums,
        "inserted_artists": inserted_artists,
        "inserted_listening_events": inserted_listening_events,
        "skipped_existing_listening_events": skipped_existing_listening_events,
    }


if __name__ == "__main__":
    register_listeningevents()
