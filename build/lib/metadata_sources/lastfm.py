"""Last.fm metadata adapter."""

from collections.abc import Callable
from typing import Any

import requests


class LastFmAPIError(RuntimeError):
    """Raised when Last.fm returns an API-level error response."""

    def __init__(self, code: int, message: str, payload: dict[str, Any]):
        super().__init__(f"Last.fm API error {code}: {message}")
        self.code = code
        self.payload = payload


class LastFmAdapter:
    """Fetch and validate track metadata from Last.fm."""

    _API_URL = "https://ws.audioscrobbler.com/2.0/"

    def __init__(
        self,
        api_key: str,
        request_get: Callable[..., Any] | None = None,
        timeout: float = 10,
    ):
        if not api_key.strip():
            raise ValueError("A Last.fm API key is required.")
        self._api_key = api_key
        self._request_get = request_get or requests.get
        self._timeout = timeout

    def get_track(
        self,
        *,
        artist: str | None = None,
        track: str | None = None,
        mbid: str | None = None,
    ) -> dict[str, Any]:
        """Fetch a track by MBID or by its artist and title."""
        if mbid is None and (not artist or not track):
            raise ValueError("Provide an MBID or both artist and track.")

        params: dict[str, str | int] = {
            "method": "track.getInfo",
            "api_key": self._api_key,
            "autocorrect": 1,
            "format": "json",
        }
        if mbid:
            params["mbid"] = mbid
        else:
            params["artist"] = artist or ""
            params["track"] = track or ""

        response = self._request_get(
            self._API_URL,
            params=params,
            timeout=self._timeout,
        )
        response.raise_for_status()
        try:
            payload = response.json()
        except ValueError as error:
            raise ValueError("Last.fm returned invalid JSON.") from error

        if not isinstance(payload, dict):
            raise ValueError("Last.fm returned an invalid response.")
        if "error" in payload:
            try:
                code = int(payload["error"])
            except (TypeError, ValueError) as error:
                raise ValueError("Last.fm returned an invalid error response.") from error
            message = payload.get("message")
            if not isinstance(message, str):
                raise ValueError("Last.fm returned an invalid error response.")
            raise LastFmAPIError(code, message, payload)

        track_data = payload.get("track")
        if not isinstance(track_data, dict) or not isinstance(track_data.get("name"), str):
            raise ValueError("Last.fm returned an invalid track response.")
        return payload