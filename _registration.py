from datetime import datetime
import time

from config import DBNAME, VERBOSE
from register.db import SimpleDB
from register.listeningevent import ListeningEvent
from register.spotify_data import models_from_spotify_track, parse_spotify_timestamp
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

            date = parse_spotify_timestamp(played_at)
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

            track = item["track"]
            _track, _album, artists = models_from_spotify_track(track)
            if _track.save(db, print_only_insert=True):
                inserted_tracks += 1
            if _album.save(db, print_only_insert=True):
                inserted_albums += 1
            for artist in artists:
                if artist.save(db, print_only_insert=True):
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
