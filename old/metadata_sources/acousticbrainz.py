"""AcousticBrainz analysis adapter."""

from collections.abc import Callable
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import quote
import time

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
        sleep: Callable[[float], None] | None = None,
        max_retries: int = 3,
    ):
        if not 0 <= max_retries <= 5:
            raise ValueError("AcousticBrainz retries must be between zero and five.")
        self._request_get = request_get or requests.get
        self._timeout = timeout
        self._sleep = sleep or time.sleep
        self._max_retries = max_retries

    def get_analysis(self, mbid: str, level: str) -> dict[str, Any] | None:
        """Return one analysis, or None if AcousticBrainz has no matching data."""
        if not mbid.strip():
            raise ValueError("A MusicBrainz recording ID is required.")
        if level not in self._LEVELS:
            raise ValueError(f"Unsupported AcousticBrainz level: {level}.")

        url = f"{self._API_URL}/{quote(mbid, safe='')}/{level}"
        for attempt in range(self._max_retries + 1):
            try:
                response = self._request_get(url, timeout=self._timeout)
                if response.status_code == 404:
                    return None
                response.raise_for_status()
            except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as error:
                failed_response = getattr(error, "response", None)
                status = getattr(failed_response, "status_code", None)
                transient = (
                    isinstance(error, (requests.Timeout, requests.ConnectionError))
                    or status in {429, 500, 502, 503, 504}
                )
                if not transient or attempt == self._max_retries:
                    raise
                delay = float(2 ** (attempt + 1))
                retry_after = (
                    failed_response.headers.get("Retry-After")
                    if failed_response is not None
                    else None
                )
                if isinstance(retry_after, str):
                    try:
                        requested = float(retry_after)
                    except ValueError:
                        try:
                            requested = (
                                parsedate_to_datetime(retry_after)
                                - datetime.now(timezone.utc)
                            ).total_seconds()
                        except (ValueError, TypeError, OverflowError):
                            requested = 0
                    delay = max(delay, requested)
                if delay > 60:
                    raise
                self._sleep(delay)
            else:
                break
        try:
            payload = response.json()
        except ValueError as error:
            raise ValueError("AcousticBrainz returned invalid JSON.") from error

        response_key = self._RESPONSE_KEYS[level]
        analysis = payload.get(response_key) if isinstance(payload, dict) else None
        if not isinstance(analysis, dict):
            raise ValueError("AcousticBrainz returned an invalid analysis response.")
        return payload
