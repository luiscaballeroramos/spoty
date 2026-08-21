import os

import spotipy
from spotipy.oauth2 import SpotifyOAuth

from config import CLIENT_ID, CLIENT_SECRET, REDIRECT_URI, VERBOSE
from register.artist import Artist


class SpotifyClient:
    def __init__(self):
        refresh_token = os.getenv("SPOTIFY_REFRESH_TOKEN")

        oauth_kwargs = {
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "redirect_uri": REDIRECT_URI,
            "scope": "user-read-playback-state user-read-recently-played user-library-read",
        }

        if refresh_token:
            oauth_kwargs["refresh_token"] = refresh_token

        self.sp = spotipy.Spotify(
            auth_manager=SpotifyOAuth(**oauth_kwargs),
            requests_timeout=20,
            retries=2,
            status_retries=2,
            backoff_factor=0.3,
        )

    def get_artist_byid(self, artist_id: str) -> Artist:
        artist = self.sp.artist(artist_id)
        images = [img["url"] for img in artist["images"]] if "images" in artist else []
        return Artist(id=artist_id, name=artist["name"], images=images)

    def get_artists_bytrackid(self, track_id: str):
        track = self.sp.track(track_id)
        artists = []

        if "artists" in track:
            for artist in track["artists"]:
                artist_info = self.sp.artist(artist["id"])
                images = (
                    [img["url"] for img in artist_info["images"]]
                    if "images" in artist_info
                    else []
                )
                artists.append(
                    Artist(
                        id=artist["id"],
                        name=artist_info["name"],
                        images=images,
                    )
                )

        return artists

    def get_track_byid(self, track_id: str):
        try:
            return self.sp.track(track_id)
        except Exception:
            (
                print(f"Error in SpotifyClient.get_track_byid for {track_id}")
                if VERBOSE
                else None
            )
            return None

    def get_currently_playing(self):
        try:
            return self.sp.currently_playing()
        except Exception:
            print("Error in SpotifyClient.get_currently_playing") if VERBOSE else None
            return None

    def get_recently_played(self, limit=20):
        try:
            return self.sp.current_user_recently_played(limit=limit)
        except Exception as exc:
            (
                print(f"Error in SpotifyClient.get_recently_played: {exc}")
                if VERBOSE
                else None
            )
            return None

    def get_liked_songs(self, limit=20, offset=0):
        try:
            return self.sp.current_user_saved_tracks(limit=limit, offset=offset)
        except Exception:
            print("Error in SpotifyClient.get_liked_songs") if VERBOSE else None
            return None
