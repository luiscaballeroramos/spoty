import random
import time

from config import DBNAME, VERBOSE
from register.db import SimpleDB
from spotifyapi.errors import SpotifyInvalidResponseError
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
    if offset != 0:
        raise ValueError("Liked-track reconciliation must start at offset 0.")

    spotify = SpotifyClient()
    with SimpleDB(DBNAME) as db:
        generation, local_revision = db.reserve_liked_track_sync()

    page_size = max(1, limit)
    current_offset = offset
    saved_count = 0
    total_count = 0
    current_liked_ids = []
    expected_total = None

    while True:
        liked_response = spotify.get_liked_songs(limit=page_size, offset=current_offset)
        items = liked_response["items"]
        response_offset = liked_response.get("offset")
        response_limit = liked_response.get("limit")
        response_total = liked_response.get("total")
        next_page = liked_response.get("next")

        if (
            type(response_offset) is not int
            or response_offset != current_offset
            or type(response_limit) is not int
            or response_limit <= 0
            or type(response_total) is not int
            or response_total < 0
            or len(items) > response_limit
            or response_offset + len(items) > response_total
        ):
            raise SpotifyInvalidResponseError("validate liked tracks pagination")

        if expected_total is None:
            expected_total = response_total
        elif response_total != expected_total:
            raise SpotifyInvalidResponseError("validate liked tracks pagination")

        next_offset = current_offset + len(items)
        has_more = next_offset < response_total
        if has_more:
            if not isinstance(next_page, str) or not next_page:
                raise SpotifyInvalidResponseError("validate liked tracks pagination")
        elif next_page is not None:
            raise SpotifyInvalidResponseError("validate liked tracks pagination")
        if has_more and not items:
            raise SpotifyInvalidResponseError("validate liked tracks pagination")

        total_count += len(items)
        for item in items:
            track = item.get("track")
            track_id = track.get("id") if isinstance(track, dict) else None
            if not isinstance(track_id, str) or not track_id:
                raise SpotifyInvalidResponseError("validate liked track item")
            current_liked_ids.append(track_id)
        if not has_more:
            break

        current_offset = next_offset

        (
            print(f"Downloaded {total_count} liked track IDs so far.")
            if VERBOSE
            else None
        )

        delay = random.uniform(10, 15)
        time.sleep(delay)

    with SimpleDB(DBNAME) as db:
        applied, saved_count, stale_ids = db.apply_liked_track_snapshot(
            generation, local_revision, current_liked_ids
        )
    if not applied:
        print(
            "Skipped stale liked-track snapshot; a newer sync or local change won."
        ) if VERBOSE else None
        return {"saved_count": 0, "stale_ids": [], "applied": False}

    if stale_ids:
        print(
            f"Removed {len(stale_ids)} stale liked track IDs that are no longer in Spotify."
        ) if VERBOSE else None
    else:
        print("No stale liked track IDs found.") if VERBOSE else None

    return {"saved_count": saved_count, "stale_ids": stale_ids, "applied": True}


if __name__ == "__main__":
    save_liked_track_ids(limit=50)
