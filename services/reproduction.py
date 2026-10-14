from config import DBNAME, VERBOSE
from register.db import SimpleDB
from spotifyapi.spotifyclient import SpotifyClient
from spotifyapi.errors import SpotifyError
from spotifyapi.diagnostics import OPERATIONS


class ReproductionService:
    """Coordinate Spotify liked-track changes with their local database records."""
    def __init__(self, client: SpotifyClient):
        """Store the Spotify client used for remote liked-track actions."""
        self._client = client

    def save_liked_track(self, track_id: str) -> bool:
        """Like a track remotely and store its ID locally, propagating Spotify errors and returning other failures as False."""
        if not track_id or not track_id.strip():
            if VERBOSE:
                print("track_id is required")
            return False

        try:
            if not self._client.like_track(track_id):
                return False
            with OPERATIONS.observe("save liked track", "postgresql"):
                database = SimpleDB(DBNAME)
                database.insert("liked_tracks", {"id": track_id}, print_only_insert=True)
            return True
        except SpotifyError:
            raise
        except Exception:
            return False

    def remove_liked_track(self, track_id: str) -> bool:
        """Unlike a track remotely and delete its local ID, propagating Spotify errors and returning other failures as False."""
        if not track_id or not track_id.strip():
            if VERBOSE:
                print("track_id is required")
            return False

        try:
            if not self._client.unlike_track(track_id):
                return False
            with OPERATIONS.observe("remove liked track", "postgresql"):
                database = SimpleDB(DBNAME)
                database.delete_ids("liked_tracks", [track_id])
            return True
        except SpotifyError:
            raise
        except Exception:
            return False
