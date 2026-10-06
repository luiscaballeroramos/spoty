from config import DBNAME, VERBOSE
from register.db import SimpleDB
from spotifyapi.spotifyclient import SpotifyClient


class ReproductionService:
    """
    Service class for handling Spotify track reproduction actions.
    Only actions that involve DB interactions are handled here.
    If DB is not involved, the action will be in SpotifyClient.
    """
    def __init__(self, client: SpotifyClient):
        self._client = client

    def _log_error(self, action: str, exc: Exception) -> None:
        if VERBOSE:
            print(f"Error trying to {action}: {exc}")

    def save_liked_track(self, track_id: str) -> bool:
        if not track_id or not track_id.strip():
            if VERBOSE:
                print("track_id is required")
            return False

        try:
            if not self._client.like_track(track_id):
                return False
            database = SimpleDB(DBNAME)
            database.insert("liked_tracks", {"id": track_id}, print_only_insert=True)
            return True
        except Exception as exc:
            self._log_error(f"like track {track_id}", exc)
            return False

    def remove_liked_track(self, track_id: str) -> bool:
        if not track_id or not track_id.strip():
            if VERBOSE:
                print("track_id is required")
            return False

        try:
            if not self._client.unlike_track(track_id):
                return False
            database = SimpleDB(DBNAME)
            database.delete_ids("liked_tracks", [track_id])
            return True
        except Exception as exc:
            self._log_error(f"unlike track {track_id}", exc)
            return False
