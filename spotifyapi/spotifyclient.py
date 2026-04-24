import spotipy
from spotipy.oauth2 import SpotifyOAuth

from config import CLIENT_ID, CLIENT_SECRET, REDIRECT_URI, VERBOSE


class SpotifyClient:
    def __init__(self):
        self.sp = spotipy.Spotify(
            auth_manager=SpotifyOAuth(
                client_id=CLIENT_ID,
                client_secret=CLIENT_SECRET,
                redirect_uri=REDIRECT_URI,
                scope="user-read-playback-state user-read-recently-played",
            )
        )

    def get_currently_playing(self):
        try:
            return self.sp.currently_playing()
        except:
            print("Error in SpotifyClient.get_currently_playing") if VERBOSE else None
            return None


    def get_recently_played(self, limit=20):
        try:
            return self.sp.current_user_recently_played(limit=limit)
        except:
            print("Error in SpotifyClient.get_recently_played") if VERBOSE else None
            return None
