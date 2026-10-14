from config import DBNAME, VERBOSE
from register.db import SimpleDB
from spotifyapi.spotifyclient import SpotifyClient
from spotifyapi.errors import SpotifyError


class ReproductionService:
    """Coordinate Spotify liked-track changes with their local database records."""
    def __init__(self, client: SpotifyClient):
        """Store the Spotify client used for remote liked-track actions."""
        self._client = client

    def _log_error(self, action: str, exc: Exception) -> None:
        """Print an action failure when verbose output is enabled."""
        if VERBOSE:
            print(f"Error trying to {action}: {exc}")

    def save_liked_track(self, track_id: str) -> bool:
        """Like a track remotely and store its ID locally, propagating Spotify errors and returning other failures as False."""
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
        except SpotifyError:
            raise
        except Exception as exc:
            self._log_error(f"like track {track_id}", exc)
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
            database = SimpleDB(DBNAME)
            database.delete_ids("liked_tracks", [track_id])
            return True
        except SpotifyError:
            raise
        except Exception as exc:
            self._log_error(f"unlike track {track_id}", exc)
            return False
