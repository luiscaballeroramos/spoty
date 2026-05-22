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
                scope="user-read-playback-state user-read-recently-played user-library-modify user-library-read",
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

    def get_saved_tracks(self):
        all_tracks = []
        # get first page
        results = self.sp.current_user_saved_tracks(limit=50)
        all_tracks.extend(results['items'])
        # fetching while there is a 'next' page
        while results['next']:
            results = self.sp.next(results)
            all_tracks.extend(results['items'])
            if VERBOSE:
                # \r allows us to update the same line in the terminal
                print(f"Progress: {len(all_tracks)} tracks collected...", end="\r")
        print(f"\nFinished! Total: {len(all_tracks)} tracks.")
        return all_tracks
