import random
import time

from config import DBNAME, VERBOSE
from register.db import SimpleDB
from spotifyapi.spotifyclient import SpotifyClient


def find_stale_liked_track_ids(current_ids: list[str], db_ids: list[str]) -> list[str]:
    current_id_set = set(current_ids)
    return sorted(set(db_ids) - current_id_set)


def save_liked_track_ids(limit=50, offset=0):
    """
    Fetch liked songs page by page and save their track IDs into the database.
    After syncing, remove any database IDs that are no longer present in the
    current Spotify liked tracks list.
    """
    spotify = SpotifyClient()
    db = SimpleDB(DBNAME)

    page_size = max(1, limit)
    current_offset = offset
    saved_count = 0
    total_count = 0
    current_liked_ids = []

    while True:
        liked_response = spotify.get_liked_songs(limit=page_size, offset=current_offset)
        if not liked_response or "items" not in liked_response:
            break

        items = liked_response["items"]
        if not items:
            break

        total_count += len(items)
        for item in items:
            track = item.get("track")
            track_id = track.get("id") if track else None
            if track_id:
                current_liked_ids.append(track_id)
                inserted = db.insert(
                    "liked_tracks", {"id": track_id}, print_only_insert=True
                )
                if inserted:
                    saved_count += 1

        if len(items) < page_size:
            break

        current_offset += len(items)

        (
            print(f"Saved {saved_count}/{total_count} liked track IDs so far.")
            if VERBOSE
            else None
        )

        delay = random.uniform(10, 15)
        time.sleep(delay)

    stale_ids = find_stale_liked_track_ids(
        current_liked_ids, db.get_all_ids("liked_tracks")
    )
    if stale_ids:
        deleted_count = db.delete_ids("liked_tracks", stale_ids)
        (
            print(
                f"Removed {deleted_count} stale liked track IDs that are no longer in Spotify."
            )
            if VERBOSE
            else None
        )
    else:
        print("No stale liked track IDs found.") if VERBOSE else None

    return {"saved_count": saved_count, "stale_ids": stale_ids}


if __name__ == "__main__":
    save_liked_track_ids(limit=50)
