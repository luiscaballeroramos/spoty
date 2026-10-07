import os
from pathlib import Path
from typing import Any, Callable

import requests
import spotipy

from config import CLIENT_ID, CLIENT_SECRET, REDIRECT_URI, VERBOSE
from register.artist import Artist
from spotipy.oauth2 import SpotifyOAuth


SPOTIFY_SCOPE = (
    "user-read-playback-state "
    "user-modify-playback-state "
    "user-read-recently-played "
    "user-top-read "
    "user-library-read "
    "user-library-modify"
)
DEFAULT_CACHE_PATH = Path(__file__).resolve().parents[1] / ".cache"


def _create_oauth_manager() -> SpotifyOAuth:
    cache_path = os.getenv("SPOTIPY_CACHE_PATH", "").strip() or str(DEFAULT_CACHE_PATH)
    return SpotifyOAuth(
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        redirect_uri=REDIRECT_URI,
        scope=SPOTIFY_SCOPE,
        cache_path=cache_path,
        open_browser=False,
    )


def get_oauth_manager() -> SpotifyOAuth:
    return _create_oauth_manager()


def has_cached_oauth_token() -> bool:
    try:
        oauth_manager = _create_oauth_manager()
        token_info = oauth_manager.cache_handler.get_cached_token()
        valid_token = oauth_manager.validate_token(token_info) if token_info else None
        return bool(valid_token)
    except Exception:
        return False


class SpotifyClient:
    """
    Client class for interacting with the Spotify Web API.
    Handles authentication via OAuth or refresh token and provides methods for accessing artist and track information.
    """
    def __init__(self):
        refresh_token = os.getenv("SPOTIFY_REFRESH_TOKEN", "").strip()

        if refresh_token:
            self.sp = self._create_client_from_refresh_token(refresh_token)
        else:
            oauth_manager = _create_oauth_manager()
            token_info = oauth_manager.cache_handler.get_cached_token()
            valid_token = oauth_manager.validate_token(token_info) if token_info else None

            if not valid_token:
                raise RuntimeError(
                    "No hay token OAuth de Spotify disponible en cache. "
                    "Autoriza una vez para generar el archivo .cache."
                )

            self.sp = spotipy.Spotify(
                auth_manager=oauth_manager,
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

    def _run(
        self,
        action: str,
        operation: Callable[..., Any],
        *args: Any,
        command: bool = False,
        **kwargs: Any,
    ) -> Any:
        try:
            result = operation(*args, **kwargs)
            return True if command else result
        except Exception as exc:
            if VERBOSE:
                print(f"Error trying to {action}: {exc}")
            return False if command else None

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
        return self._run(f"get track {track_id}", self.sp.track, track_id)

    def get_currently_playing(self):
        return self._run("get currently playing", self.sp.currently_playing)

    def get_recently_played(self, limit=20):
        return self._run(
            "get recently played", self.sp.current_user_recently_played, limit=limit
        )

    def get_top_tracks(self, limit=20, offset=0, time_range="medium_term"):
        return self._run(
            "get top tracks",
            self.sp.current_user_top_tracks,
            limit=limit,
            offset=offset,
            time_range=time_range,
        )

    def get_top_artists(self, limit=20, offset=0, time_range="medium_term"):
        return self._run(
            "get top artists",
            self.sp.current_user_top_artists,
            limit=limit,
            offset=offset,
            time_range=time_range,
        )

    def get_liked_songs(self, limit=20, offset=0):
        return self._run(
            "get liked songs",
            self.sp.current_user_saved_tracks,
            limit=limit,
            offset=offset,
        )

    def pause_playback(self, device_id: str | None = None) -> bool:
        return self._run(
            "pause playback", self.sp.pause_playback, device_id=device_id, command=True
        )

    def start_playback(
        self,
        device_id: str | None = None,
        uri: str | None = None,
        position_ms: int | None = None,
        uris: list[str] | None = None,
        context_uri: str | None = None,
    ) -> bool:
        return self._run(
            "resume playback",
            self.sp.start_playback,
            device_id=device_id,
            uris=None if context_uri else uris if uris is not None else [uri] if uri else None,
            position_ms=position_ms if uri or uris else None,
            **(
                {"context_uri": context_uri, "offset": {"uri": uri}}
                if context_uri and uri
                else {}
            ),
            command=True,
        )

    def next_track(self, device_id: str | None = None) -> bool:
        return self._run(
            "skip to next track", self.sp.next_track, device_id=device_id, command=True
        )

    def previous_track(self) -> bool:
        return self._run(
            "go to previous track", self.sp.previous_track, command=True
        )

    def shuffle(self, state: bool) -> bool:
        return self._run("set shuffle", self.sp.shuffle, state, command=True)

    def repeat(self, mode: str) -> bool:
        normalized_mode = (mode or "").strip().lower()
        if normalized_mode not in {"off", "track", "context"}:
            if VERBOSE:
                print("Invalid repeat mode. Use: off, track, or context")
            return False
        return self._run(
            f"set repeat mode to {normalized_mode}",
            self.sp.repeat,
            normalized_mode,
            command=True,
        )

    def get_current_playback(self):
        return self._run("get current playback", self.sp.current_playback)

    def get_devices(self):
        return self._run("get devices", self.sp.devices)

    def transfer_playback(self, device_id: str) -> bool:
        return self._run(
            "activate playback device",
            self.sp.transfer_playback,
            device_id,
            force_play=False,
            command=True,
        )

    def add_to_queue(self, uri: str, device_id: str | None = None) -> bool:
        if not uri or not uri.strip():
            if VERBOSE:
                print("uri is required")
            return False
        return self._run(
            f"add uri to queue: {uri}",
            self.sp.add_to_queue,
            uri=uri,
            device_id=device_id,
            command=True,
        )

    def get_queue(self):
        return self._run("get queue", self.sp.queue)

    def like_track(self, track_id: str) -> bool:
        return self._run(
            f"like track {track_id}",
            self.sp.current_user_saved_tracks_add,
            [track_id],
            command=True,
        )

    def unlike_track(self, track_id: str) -> bool:
        return self._run(
            f"unlike track {track_id}",
            self.sp.current_user_saved_tracks_delete,
            [track_id],
            command=True,
        )

    def is_track_liked(self, track_id: str) -> bool:
        response = self._run(
            f"check liked status for track {track_id}",
            self.sp.current_user_saved_tracks_contains,
            [track_id],
        )
        return bool(response and response[0])
