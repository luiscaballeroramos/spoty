import math
import os
import time
from pathlib import Path
from typing import Any, Callable

import requests
import spotipy

from config import CLIENT_ID, CLIENT_SECRET, REDIRECT_URI, VERBOSE
from register.artist import Artist
from spotipy.oauth2 import SpotifyOAuth
from spotifyapi.errors import (
    SpotifyAuthorizationError,
    SpotifyInvalidResponseError,
    SpotifyRateLimitError,
    SpotifyRequestError,
    SpotifyTimeoutError,
    SpotifyUnavailableError,
)


SPOTIFY_SCOPE = (
    "user-read-playback-state "
    "user-modify-playback-state "
    "user-read-recently-played "
    "user-top-read "
    "user-library-read "
    "user-library-modify"
)
DEFAULT_CACHE_PATH = Path(__file__).resolve().parents[1] / ".cache"
_SPOTIFY_RATE_LIMITED_UNTIL = 0.0


def _create_oauth_manager() -> SpotifyOAuth:
    """Create an OAuth manager with configured scopes and token cache without opening a browser."""
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
    """Return a new OAuth manager for authorization and token management."""
    return _create_oauth_manager()


def has_cached_oauth_token() -> bool:
    """Check whether a cached OAuth token validates, returning False if validation fails or raises."""
    try:
        oauth_manager = _create_oauth_manager()
        token_info = oauth_manager.cache_handler.get_cached_token()
        valid_token = oauth_manager.validate_token(token_info) if token_info else None
        return bool(valid_token)
    except Exception:
        return False


class SpotifyClient:
    """Access Spotify using OAuth or a refresh token, validated reads, and explicit API errors."""
    def __init__(self):
        """Initialize a non-retrying Spotify transport from a refresh token or valid cached OAuth token."""
        refresh_token = os.getenv("SPOTIFY_REFRESH_TOKEN", "").strip()

        if refresh_token:
            self.sp = self._create_client_from_refresh_token(refresh_token)
        else:
            oauth_manager = _create_oauth_manager()
            token_info = oauth_manager.cache_handler.get_cached_token()
            valid_token = oauth_manager.validate_token(token_info) if token_info else None

            if not valid_token:
                raise SpotifyAuthorizationError("initialize Spotify client")

            self.sp = spotipy.Spotify(
                auth_manager=oauth_manager,
                requests_timeout=20,
                retries=0,
                status_retries=0,
                backoff_factor=0.3,
            )

    def _create_client_from_refresh_token(self, refresh_token: str):
        """Exchange a refresh token for a validated access token and create a non-retrying transport."""
        def refresh_access_token():
            """Request a new access token and return its JSON payload after checking the HTTP response."""
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
            return response.json()

        token_info = self._run("refresh Spotify token", refresh_access_token)
        if not isinstance(token_info, dict) or not isinstance(token_info.get("access_token"), str) or not token_info["access_token"]:
            raise SpotifyInvalidResponseError("refresh Spotify token")
        access_token = token_info["access_token"]

        return spotipy.Spotify(
            auth=access_token,
            requests_timeout=20,
            retries=0,
            status_retries=0,
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
        """Run an operation once, enforce shared cooldown, and raise categorized errors with their causes."""
        global _SPOTIFY_RATE_LIMITED_UNTIL
        remaining_cooldown = _SPOTIFY_RATE_LIMITED_UNTIL - time.monotonic()
        if remaining_cooldown > 0:
            raise SpotifyRateLimitError(action, remaining_cooldown)

        try:
            result = operation(*args, **kwargs)
            return True if command else result
        except Exception as exc:
            response = getattr(exc, "response", None)
            http_status = getattr(exc, "http_status", None)
            if http_status is None and response is not None:
                http_status = response.status_code
            if http_status == 429:
                headers = getattr(exc, "headers", None)
                if headers is None and response is not None:
                    headers = response.headers
                headers = headers or {}
                retry_after = headers.get("Retry-After", headers.get("retry-after", 1))
                try:
                    retry_after_seconds = float(retry_after)
                    if not math.isfinite(retry_after_seconds) or retry_after_seconds < 0:
                        retry_after_seconds = 1.0
                except (TypeError, ValueError):
                    retry_after_seconds = 1.0
                _SPOTIFY_RATE_LIMITED_UNTIL = time.monotonic() + retry_after_seconds
                print(
                    "Spotify API rate limit hit (429). Pausing requests for "
                    f"{retry_after_seconds:g} seconds."
                )
                raise SpotifyRateLimitError(action, retry_after_seconds) from exc
            if http_status in (401, 403):
                raise SpotifyAuthorizationError(action, http_status) from exc
            if isinstance(exc, requests.Timeout):
                raise SpotifyTimeoutError(action) from exc
            if isinstance(exc, requests.ConnectionError) or (
                http_status is not None and http_status >= 500
            ):
                raise SpotifyUnavailableError(action, http_status) from exc
            if isinstance(exc, (ValueError, requests.exceptions.JSONDecodeError)):
                raise SpotifyInvalidResponseError(action) from exc
            if http_status is not None:
                raise SpotifyRequestError(action, http_status) from exc
            raise

    def _read_object(
        self,
        action: str,
        operation: Callable[..., Any],
        *args: Any,
        allow_none: bool = False,
        list_key: str | None = None,
        **kwargs: Any,
    ) -> dict | None:
        """Validate an object response and optional list field, allowing None only when explicitly requested."""
        response = self._run(action, operation, *args, **kwargs)
        if response is None and allow_none:
            return None
        if not isinstance(response, dict):
            raise SpotifyInvalidResponseError(action)
        if list_key is not None and (
            not isinstance(response.get(list_key), list)
            or any(not isinstance(item, dict) for item in response[list_key])
        ):
            raise SpotifyInvalidResponseError(action)
        return response

    def get_artist_byid(self, artist_id: str) -> Artist:
        """Fetch an artist by Spotify ID and convert its name and image URLs into an Artist model."""
        artist = self._read_object("get artist", self.sp.artist, artist_id)
        images = [img["url"] for img in artist["images"]] if "images" in artist else []
        return Artist(id=artist_id, name=artist["name"], images=images)

    def get_artists_bytrackid(self, track_id: str):
        """Fetch a track's artists individually and return Artist models with their names and image URLs."""
        track = self._read_object("get track artists", self.sp.track, track_id)
        artists = []

        if "artists" in track:
            for artist in track["artists"]:
                artist_info = self._read_object("get artist", self.sp.artist, artist["id"])
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
        """Return a validated track object for the given Spotify ID."""
        return self._read_object(f"get track {track_id}", self.sp.track, track_id)

    def get_currently_playing(self):
        """Return the currently playing item payload, allowing None when Spotify reports no item."""
        return self._read_object("get currently playing", self.sp.currently_playing, allow_none=True)

    def get_recently_played(self, limit=20):
        """Fetch recent listening events and require a valid items list, which may be empty."""
        return self._read_object(
            "get recently played", self.sp.current_user_recently_played, limit=limit, list_key="items"
        )

    def get_top_tracks(self, limit=20, offset=0, time_range="medium_term"):
        """Fetch a validated page of top tracks for the requested limit, offset, and time range."""
        return self._read_object(
            "get top tracks",
            self.sp.current_user_top_tracks,
            limit=limit,
            offset=offset,
            time_range=time_range,
            list_key="items",
        )

    def get_top_artists(self, limit=20, offset=0, time_range="medium_term"):
        """Fetch a validated page of top artists for the requested limit, offset, and time range."""
        return self._read_object(
            "get top artists",
            self.sp.current_user_top_artists,
            limit=limit,
            offset=offset,
            time_range=time_range,
            list_key="items",
        )

    def get_liked_songs(self, limit=20, offset=0):
        """Fetch a liked-track page, distinguishing a valid empty items list from a malformed response."""
        return self._read_object(
            "get liked songs",
            self.sp.current_user_saved_tracks,
            limit=limit,
            offset=offset,
            list_key="items",
        )

    def pause_playback(self, device_id: str | None = None) -> bool:
        """Pause playback on the requested or active device, returning True on success and raising on failure."""
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
        """Start or resume playback with an optional device, track URI, queue, context, and saved position."""
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
        """Skip to the next track on the requested or active device without automatically retrying."""
        return self._run(
            "skip to next track", self.sp.next_track, device_id=device_id, command=True
        )

    def previous_track(self) -> bool:
        """Request the previous track on the active device without automatically retrying."""
        return self._run(
            "go to previous track", self.sp.previous_track, command=True
        )

    def shuffle(self, state: bool) -> bool:
        """Enable or disable shuffle on the active device and raise if Spotify rejects the change."""
        return self._run("set shuffle", self.sp.shuffle, state, command=True)

    def repeat(self, mode: str) -> bool:
        """Set repeat to off, track, or context, returning False without an API call for invalid modes."""
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
        """Return the current playback state, allowing None for absence but raising on failed reads."""
        return self._read_object("get current playback", self.sp.current_playback, allow_none=True)

    def get_devices(self):
        """Return a validated devices payload with a devices list that may be empty."""
        return self._read_object("get devices", self.sp.devices, list_key="devices")

    def transfer_playback(self, device_id: str) -> bool:
        """Transfer playback to the specified device without forcing playback to start."""
        return self._run(
            "activate playback device",
            self.sp.transfer_playback,
            device_id,
            force_play=False,
            command=True,
        )

    def add_to_queue(self, uri: str, device_id: str | None = None) -> bool:
        """Queue a URI on the requested or active device, returning False if the URI is blank."""
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
        """Return a validated queue payload, allowing None for absence and raising on malformed data."""
        return self._read_object("get queue", self.sp.queue, allow_none=True, list_key="queue")

    def like_track(self, track_id: str) -> bool:
        """Save a track to the Spotify library, returning True on success and raising on failure."""
        return self._run(
            f"like track {track_id}",
            self.sp.current_user_saved_tracks_add,
            [track_id],
            command=True,
        )

    def unlike_track(self, track_id: str) -> bool:
        """Remove a track from the Spotify library, returning True on success and raising on failure."""
        return self._run(
            f"unlike track {track_id}",
            self.sp.current_user_saved_tracks_delete,
            [track_id],
            command=True,
        )

    def are_tracks_liked(self, track_ids: list[str]) -> dict[str, bool]:
        """Batch unique nonempty IDs and require one boolean per track instead of inventing false negatives."""
        unique_track_ids = list(
            dict.fromkeys(track_id for track_id in track_ids if track_id)
        )
        if not unique_track_ids:
            return {}

        response = self._run(
            "check liked status for tracks",
            self.sp.current_user_saved_tracks_contains,
            unique_track_ids,
        )
        if (
            not isinstance(response, list)
            or len(response) != len(unique_track_ids)
            or any(not isinstance(liked, bool) for liked in response)
        ):
            raise SpotifyInvalidResponseError("check liked status for tracks")
        return {
            track_id: response[index]
            for index, track_id in enumerate(unique_track_ids)
        }

    def is_track_liked(self, track_id: str) -> bool:
        """Return a track's validated liked status, using False for an empty ID and raising on read failures."""
        return self.are_tracks_liked([track_id]).get(track_id, False)
