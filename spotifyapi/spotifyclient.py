import os

import requests
import spotipy

from config import CLIENT_ID, CLIENT_SECRET, REDIRECT_URI, VERBOSE
from register.artist import Artist
from spotipy.oauth2 import SpotifyOAuth


class SpotifyClient:
    def __init__(self):
        refresh_token = os.getenv("SPOTIFY_REFRESH_TOKEN")

        if refresh_token:
            self.sp = self._create_client_from_refresh_token(refresh_token)
        else:
            self.sp = spotipy.Spotify(
                auth_manager=SpotifyOAuth(
                    client_id=CLIENT_ID,
                    client_secret=CLIENT_SECRET,
                    redirect_uri=REDIRECT_URI,
                    scope="user-read-playback-state user-read-recently-played user-library-read",
                ),
                requests_timeout=20,
                retries=2,
                status_retries=2,
                backoff_factor=0.3,
            )

    def _create_client_from_refresh_token(self, refresh_token: str):
        response = requests.post(
            "https://accounts.spotify.com/api/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
            auth=(CLIENT_ID, CLIENT_SECRET),
            timeout=20,
        )

        response.raise_for_status()

        access_token = response.json()["access_token"]

        return spotipy.Spotify(
            auth=access_token,
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
            if VERBOSE:
                print(f"Error in SpotifyClient.get_track_byid for {track_id}")
            return None

    def get_currently_playing(self):
        try:
            return self.sp.currently_playing()
        except Exception:
            if VERBOSE:
                print("Error in SpotifyClient.get_currently_playing")
            return None

    def get_recently_played(self, limit=20):
        try:
            return self.sp.current_user_recently_played(limit=limit)
        except Exception as exc:
            if VERBOSE:
                print(f"Error in SpotifyClient.get_recently_played: {exc}")
            return None

    def get_liked_songs(self, limit=20, offset=0):
        try:
            return self.sp.current_user_saved_tracks(limit=limit, offset=offset)
        except Exception:
            if VERBOSE:
                print("Error in SpotifyClient.get_liked_songs")
            return None
