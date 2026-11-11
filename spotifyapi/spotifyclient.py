import os
from typing import Any

import requests


class SpotifyClient:
    def get_recently_played(self, limit: int = 50) -> dict[str, Any]:
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError("Spotify listening limit must be between 1 and 50.")

        credentials = {}
        for name in ("SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET", "SPOTIFY_REFRESH_TOKEN"):
            value = os.getenv(name, "").strip()
            if not value:
                raise ValueError(f"Missing {name}.")
            credentials[name] = value

        response = requests.post(
            "https://accounts.spotify.com/api/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": credentials["SPOTIFY_REFRESH_TOKEN"],
            },
            auth=(credentials["SPOTIFY_CLIENT_ID"], credentials["SPOTIFY_CLIENT_SECRET"]),
            timeout=20,
            allow_redirects=False,
        )
        if response.status_code != 200:
            raise RuntimeError(f"Spotify token refresh failed (HTTP {response.status_code}).")
        token_info = response.json()
        if (
            not isinstance(token_info, dict)
            or not isinstance(token_info.get("access_token"), str)
            or not token_info["access_token"]
        ):
            raise ValueError("Spotify returned an invalid access token.")
        response = requests.get(
            "https://api.spotify.com/v1/me/player/recently-played",
            headers={"Authorization": f"Bearer {token_info['access_token']}"},
            params={"limit": limit},
            timeout=20,
            allow_redirects=False,
        )
        if response.status_code != 200:
            raise RuntimeError(
                f"Spotify recently-played request failed (HTTP {response.status_code})."
            )
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
            raise ValueError("Spotify returned an invalid recently-played response.")
        return payload
