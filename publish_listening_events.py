import os
from typing import Any

from config import load_env_file
from publish_data import publish_data
from register.listeningevent import ListeningEvent
from register.spotify_data import catalog_from_items
from spotifyapi.spotifyclient import SpotifyClient
from spotifyapi.timestamps import parse_spotify_timestamp

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


def main() -> None:
    load_env_file()
    token = os.getenv("SPOTY_DATA_TOKEN", "").strip()
    if not token:
        raise ValueError("Missing SPOTY_DATA_TOKEN.")
    response = SpotifyClient().get_recently_played(limit=RECENT_LISTENING_LIMIT)
    events = build_event_rows(response["items"])
    catalog = catalog_from_items(response["items"])
    publish_data({"events": events, **catalog}, token, source="spotify-listening")
    print(f"Dispatched {len(events)} listening events and their catalog.")


if __name__ == "__main__":
    main()
