from datetime import datetime, timezone
from typing import Any

from register.album import Album
from register.artist import Artist
from register.track import Track


def parse_spotify_timestamp(value: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError("Spotify played_at must be a nonempty ISO-8601 timestamp.")

    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Spotify played_at must include a timezone.")
    return parsed.astimezone(timezone.utc)


def models_from_spotify_track(
    track_data: dict[str, Any],
) -> tuple[Track, Album, list[Artist]]:
    album_data = track_data["album"]
    track_artists = track_data["artists"]
    album_artists = album_data["artists"]
    track_id = track_data["id"]

    track = Track(
        id=track_id,
        name=track_data["name"],
        duration_ms=track_data["duration_ms"],
        album_id=album_data["id"],
        album_track=track_data.get("track_number"),
        artists_ids=[artist["id"] for artist in track_artists],
        explicit=track_data.get("explicit"),
        popularity=track_data.get("popularity"),
    )
    album = Album(
        id=album_data["id"],
        name=album_data["name"],
        artists=[artist["id"] for artist in album_artists],
        tracks=[track_id],
        total_tracks=album_data["total_tracks"],
        images=[image["url"] for image in album_data.get("images", [])],
        release_date=album_data.get("release_date"),
        release_date_precision=album_data.get("release_date_precision"),
        popularity=album_data.get("popularity"),
    )
    artists = [
        Artist(id=artist["id"], name=artist["name"])
        for artist in track_artists + album_artists
    ]
    return track, album, artists
