"""Authenticated internal-API adapter; only manages marked metadata programs."""

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import requests


ORIGIN = "https://smarterplaylists.playlistmachinery.com"
OWNER = "spoty-smarterplaylists-daily-v1"
SUPPORTED = {
    "artist": ["artist_area", "artist_country", "artist_end_year", "artist_gender",
               "artist_start_year", "artist_type", "followers", "name", "popularity", "uri"],
    "album": ["album_type", "artist", "label", "name", "popularity", "release_date", "total_tracks", "uri"],
}


class SmarterPlaylistsError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def load_session_cookie(*, value=None, path=None):
    if path:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        expires = payload.get("expires_at")
        if expires is not None and expires > 0 and expires <= time.time():
            raise SmarterPlaylistsError("session_expired")
        value = payload.get("session_cookie")
    if not isinstance(value, str) or not value or any(char in value for char in "\r\n;"):
        raise SmarterPlaylistsError("session_missing_or_invalid")
    return value


class SmarterPlaylistsAdapter:
    def __init__(self, session_cookie, *, session=None, sleep=time.sleep, clock=time.monotonic):
        self._session = session or requests.Session()
        cookie = load_session_cookie(value=session_cookie)
        self._session.cookies.set("session", cookie, domain="smarterplaylists.playlistmachinery.com",
                                  path="/", secure=True)
        self._sleep = sleep
        self._clock = clock
        self._inventory = None

    def close(self):
        self._session.close()

    def _request(self, path, *, method="GET", body=None):
        try:
            response = self._session.request(method, ORIGIN + path, json=body,
                                             timeout=(10, 60), allow_redirects=False)
        except requests.RequestException:
            raise SmarterPlaylistsError("transport_error") from None
        if response.status_code in (301, 302, 303, 307, 308, 401, 403):
            raise SmarterPlaylistsError("session_expired_or_denied")
        if response.status_code == 429:
            raise SmarterPlaylistsError("rate_limited")
        if not 200 <= response.status_code < 300:
            raise SmarterPlaylistsError(f"http_{response.status_code}")
        try:
            return response.json()
        except ValueError:
            raise SmarterPlaylistsError("invalid_json") from None

    def authenticate(self):
        self._request("/api/auth/me")

    def _program(self, entity, candidates):
        source_type = {"track": "TrackSource", "artist": "ArtistURIs", "album": "AlbumURIs"}[entity]
        if self._inventory is None:
            self._inventory = self._request("/api/inventory")
        try:
            sortable = next(item for item in self._inventory if item["name"] == "Sorter")["params"]["properties"]["attr"]["enum"]
        except (KeyError, TypeError, StopIteration):
            raise SmarterPlaylistsError("inventory_changed") from None
        attrs = [attr for attr in sortable if attr not in ("random", "source", "source_count")] if entity == "track" else SUPPORTED[entity]
        if not set(attrs) <= set(sortable):
            raise SmarterPlaylistsError("inventory_changed")
        components = {"source": {"type": source_type, "params": {
            "uris": " ".join(f"spotify:{entity}:{candidate['id']}" for candidate in candidates),
            "name": "spoty-daily", "alias": "spoty-daily", "cache_mode": "no-cache",
        }, "sources": {}}}
        branches = ["source"]
        for attr in attrs:
            node = "sort_" + attr
            components[node] = {"type": "Sorter", "params": {"attr": attr, "reverse": False},
                                "sources": {"green": ["source"]}}
            branches.append(node)
        if entity == "track":
            components["genres"] = {"type": "GenreFilter", "params": {"genre": "", "invert": False,
                "match_type": "substring", "primary_genre_only": False}, "sources": {"green": ["source"]}}
            branches.append("genres")
        components["results"] = {"type": "Concatenate", "params": {}, "sources": {"green": branches}}
        name = f"Spoty Daily Metadata {entity}"
        return {"name": name, "main": "results", "components": components, "_spoty_owner": OWNER}, attrs

    def capture(self, entity, candidates):
        if entity not in ("track", "artist", "album") or not candidates:
            raise ValueError("Expected a nonempty entity batch.")
        if any(not re.fullmatch(r"[A-Za-z0-9]{22}", candidate["id"]) for candidate in candidates):
            raise ValueError("Invalid Spotify candidate ID.")
        program, attrs = self._program(entity, candidates)
        programs = self._request("/api/programs/")
        matches = [item for item in programs if item["name"] == program["name"]]
        if len(matches) > 1:
            raise SmarterPlaylistsError("program_name_collision")
        body = {"name": program["name"], "program_json": program}
        if matches:
            pid = matches[0]["pid"]
            existing = self._request(f"/api/programs/{pid}")
            if existing.get("program_json", {}).get("_spoty_owner") != OWNER:
                raise SmarterPlaylistsError("unowned_program")
            self._request(f"/api/programs/{pid}", method="PUT", body=body)
        else:
            pid = self._request("/api/programs/", method="POST", body=body)["pid"]
        run_id = self._request(f"/api/programs/{pid}/run?save=false", method="POST")["run_id"]
        deadline = self._clock() + 180
        while True:
            raw = self._request(f"/api/runs/{run_id}/status")
            if raw.get("status") == "complete":
                break
            if raw.get("status") not in ("queued", "running"):
                raise SmarterPlaylistsError("run_failed")
            if self._clock() >= deadline:
                raise SmarterPlaylistsError("run_timeout")
            self._sleep(1.5)
        if raw.get("playlist_uri"):
            raise SmarterPlaylistsError("unexpected_playlist_output")
        grouped = {candidate["id"]: [] for candidate in candidates}
        for row in raw.get("tracks", []):
            spotify_id = row.get("id")
            if spotify_id not in grouped or row.get("uri") != f"spotify:{entity}:{spotify_id}":
                raise SmarterPlaylistsError("unexpected_entity")
            grouped[spotify_id].append(row)
        reports = []
        missing_ids = []
        captured_at = datetime.now(timezone.utc).isoformat()
        for spotify_id, rows in grouped.items():
            if not rows:
                missing_ids.append(spotify_id)
                continue
            values = {}
            for row in rows:
                for field, value in row.items():
                    if value is None:
                        continue
                    if field in values and json.dumps(values[field], sort_keys=True) != json.dumps(value, sort_keys=True):
                        raise SmarterPlaylistsError("conflicting_values")
                    values[field] = value
            reports.append({"captured_at": captured_at, "entities": {entity: {
                "values": values, "conflicts": {},
                "missing_requested": [field for field in attrs if field not in values],
                "raw": {**raw, "tracks": rows},
            }}})
        return reports, missing_ids
