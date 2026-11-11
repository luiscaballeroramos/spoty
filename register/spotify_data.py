from datetime import datetime
from typing import Any


def catalog_from_items(items: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    tracks: dict[str, dict[str, Any]] = {}
    artists: dict[str, dict[str, Any]] = {}
    albums: dict[str, dict[str, Any]] = {}
    for item in items:
        track = item["track"]
        album = track.get("album")
        if not isinstance(album, dict):
            raise ValueError("Missing Spotify album data.")
        for entry in track["artists"] + album["artists"]:
            artists.setdefault(entry["id"], {"id": entry["id"], "name": entry["name"]})
        explicit = track.get("explicit")
        if explicit is not None and type(explicit) is not bool:
            raise ValueError("Invalid Spotify explicit flag.")
        tracks.setdefault(track["id"], {
            "id": track["id"], "name": track["name"],
            "duration_ms": track["duration_ms"], "album_id": album["id"],
            "album_track": track.get("track_number"),
            "artists_ids": [artist["id"] for artist in track["artists"]],
            "explicit": int(explicit) if explicit is not None else None,
            "popularity": track.get("popularity"), "disc_number": track.get("disc_number"),
        })
        release_year = None
        if album.get("release_date") is not None:
            formats = {"year": "%Y", "month": "%Y-%m", "day": "%Y-%m-%d"}
            precision = album.get("release_date_precision")
            if precision not in formats:
                raise ValueError("Invalid Spotify release-date precision.")
            release_year = datetime.strptime(album["release_date"], formats[precision]).year
        record = albums.setdefault(album["id"], {
            "id": album["id"], "name": album["name"],
            "artists": [artist["id"] for artist in album["artists"]],
            "tracks": [], "total_tracks": album["total_tracks"],
            "images": [image["url"] for image in album.get("images", [])],
            "release_year": release_year,
        })
        if track["id"] not in record["tracks"]:
            record["tracks"].append(track["id"])
    return {"tracks": list(tracks.values()), "artists": list(artists.values()), "albums": list(albums.values())}
