"""MusicBrainz metadata adapter."""

from collections.abc import Callable
from typing import Any
from urllib.parse import quote
import time

import requests


class MusicBrainzAdapter:
    """Look up MusicBrainz entities while respecting its request limit."""

    _API_URL = "https://musicbrainz.org/ws/2"
    _ENTITIES = {"artist", "recording", "release", "release-group"}

    def __init__(
        self,
        user_agent: str,
        request_get: Callable[..., Any] | None = None,
        monotonic: Callable[[], float] | None = None,
        sleep: Callable[[float], None] | None = None,
        min_request_interval: float = 1.1,
        timeout: float = 10,
    ):
        if not user_agent.strip():
            raise ValueError("A meaningful MusicBrainz User-Agent is required.")
        if min_request_interval < 1:
            raise ValueError("MusicBrainz requests must be at least one second apart.")
        self._user_agent = user_agent
        self._request_get = request_get or requests.get
        self._monotonic = monotonic or time.monotonic
        self._sleep = sleep or time.sleep
        self._min_request_interval = min_request_interval
        self._timeout = timeout
        self._last_request_at: float | None = None

    def find_recordings_by_isrc(self, isrc: str) -> dict[str, Any]:
        """Find recordings for an ISRC, returning an empty list when unmatched."""
        if not isrc.strip():
            raise ValueError("An ISRC is required.")
        return self._get_json(
            f"/isrc/{quote(isrc, safe='')}",
            params={"fmt": "json"},
            expected_list="recordings",
            not_found_empty=True,
        )

    def search_recordings(
        self,
        title: str,
        artist: str,
        limit: int = 10,
    ) -> dict[str, Any]:
        """Search recordings using an escaped title and artist query."""
        if not title.strip() or not artist.strip():
            raise ValueError("A recording title and artist are required.")
        if not 1 <= limit <= 100:
            raise ValueError("MusicBrainz search limit must be between 1 and 100.")

        def quoted(value: str) -> str:
            escaped = value.replace("\\", "\\\\").replace('"', '\\"')
            return f'"{escaped}"'

        return self._get_json(
            "/recording",
            params={
                "fmt": "json",
                "limit": str(limit),
                "query": (
                    f"recording:{quoted(title)} AND artist:{quoted(artist)}"
                ),
            },
            expected_list="recordings",
        )

    def get_entity(
        self,
        entity: str,
        mbid: str,
        includes: str | None = None,
    ) -> dict[str, Any]:
        """Fetch a MusicBrainz entity by MBID, optionally including linked data."""
        if entity not in self._ENTITIES:
            raise ValueError(f"Unsupported MusicBrainz entity: {entity}.")
        if not mbid.strip():
            raise ValueError("A MusicBrainz ID is required.")
        params = {"fmt": "json"}
        if includes:
            params["inc"] = includes
        payload = self._get_json(
            f"/{entity}/{quote(mbid, safe='')}",
            params=params,
        )
        if payload.get("id") != mbid:
            raise ValueError("MusicBrainz returned an entity with an unexpected ID.")
        return payload

    def _get_json(
        self,
        path: str,
        *,
        params: dict[str, str],
        expected_list: str | None = None,
        not_found_empty: bool = False,
    ) -> dict[str, Any]:
        now = self._monotonic()
        if self._last_request_at is not None:
            delay = self._min_request_interval - (now - self._last_request_at)
            if delay > 0:
                self._sleep(delay)
        self._last_request_at = self._monotonic()

        response = self._request_get(
            f"{self._API_URL}{path}",
            params=params,
            headers={"User-Agent": self._user_agent},
            timeout=self._timeout,
        )
        if not_found_empty and response.status_code == 404:
            return {expected_list: []} if expected_list else {}
        response.raise_for_status()
        try:
            payload = response.json()
        except ValueError as error:
            raise ValueError("MusicBrainz returned invalid JSON.") from error
        if not isinstance(payload, dict):
            raise ValueError("MusicBrainz returned an invalid response.")
        if expected_list:
            entities = payload.get(expected_list)
            if not isinstance(entities, list) or any(
                not isinstance(item, dict)
                or not isinstance(item.get("id"), str)
                or not item["id"]
                for item in entities
            ):
                raise ValueError("MusicBrainz returned an invalid entity list.")
        return payload
