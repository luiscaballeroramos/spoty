"""AcousticBrainz analysis adapter."""

from collections.abc import Callable
from typing import Any
from urllib.parse import quote

import requests


class AcousticBrainzAdapter:
    """Fetch low-level and high-level analyses by MusicBrainz recording MBID."""

    _API_URL = "https://acousticbrainz.org/api/v1"
    _LEVELS = {"low-level", "high-level"}
    _RESPONSE_KEYS = {"low-level": "lowlevel", "high-level": "highlevel"}

    def __init__(
        self,
        request_get: Callable[..., Any] | None = None,
        timeout: float = 10,
    ):
        self._request_get = request_get or requests.get
        self._timeout = timeout

    def get_analysis(self, mbid: str, level: str) -> dict[str, Any] | None:
        """Return one analysis, or None if AcousticBrainz has no matching data."""
        if not mbid.strip():
            raise ValueError("A MusicBrainz recording ID is required.")
        if level not in self._LEVELS:
            raise ValueError(f"Unsupported AcousticBrainz level: {level}.")

        response = self._request_get(
            f"{self._API_URL}/{quote(mbid, safe='')}/{level}",
            timeout=self._timeout,
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        try:
            payload = response.json()
        except ValueError as error:
            raise ValueError("AcousticBrainz returned invalid JSON.") from error

        response_key = self._RESPONSE_KEYS[level]
        analysis = payload.get(response_key) if isinstance(payload, dict) else None
        if not isinstance(analysis, dict):
            raise ValueError("AcousticBrainz returned an invalid analysis response.")
        return payload