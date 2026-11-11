import os
from typing import Any

import requests

from config import load_env_file
from register.listeningevent import ListeningEvent
from spotifyapi.spotifyclient import SpotifyClient
from spotifyapi.timestamps import parse_spotify_timestamp

DATA_REPOSITORY = "luiscaballeroramos/spoty-data"
EVENT_TYPE = "record-listening-events"
RECENT_LISTENING_LIMIT = 50


def build_event_rows(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        raise ValueError("Expected a list of Spotify listening events.")
    rows = []
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("track"), dict):
            raise ValueError("Invalid Spotify listening-event track.")
        context = item.get("context")
        if context is not None and not isinstance(context, dict):
            raise ValueError("Invalid Spotify listening-event context.")
        uri = context.get("uri") if context is not None else None
        event = ListeningEvent(
            item["track"].get("id"), parse_spotify_timestamp(item.get("played_at")), uri
        )
        rows.append(event.to_dict())
    return rows


def publish_events(
    events: list[dict[str, Any]],
    token: str,
) -> None:
    if not events:
        return
    if not token:
        raise ValueError("Missing SPOTY_DATA_TOKEN.")

    response = requests.post(
        f"https://api.github.com/repos/{DATA_REPOSITORY}/dispatches",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        json={"event_type": EVENT_TYPE, "client_payload": {"events": events}},
        timeout=20,
        allow_redirects=False,
    )
    if response.status_code != 204:
        raise RuntimeError(
            f"GitHub repository dispatch failed (HTTP {response.status_code})."
        )


def main() -> None:
    load_env_file()
    token = os.getenv("SPOTY_DATA_TOKEN", "").strip()
    if not token:
        raise ValueError("Missing SPOTY_DATA_TOKEN.")
    response = SpotifyClient().get_recently_played(limit=RECENT_LISTENING_LIMIT)
    events = build_event_rows(response["items"])
    publish_events(events, token)
    print(f"Dispatched {len(events)} listening events.")


if __name__ == "__main__":
    main()
