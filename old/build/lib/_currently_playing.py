import json
import os
from datetime import datetime, timezone
from pathlib import Path

from register.track import Track
from spotifyapi.spotifyclient import SpotifyClient


def main() -> None:
    client = SpotifyClient()
    payload = client.get_currently_playing() or {}
    polled_at_utc = datetime.now(timezone.utc).isoformat()

    if not payload.get("item"):
        snapshot = {
            "status": "no_item",
            "polled_at_utc": polled_at_utc,
        }
    elif payload.get("currently_playing_type") and payload.get(
        "currently_playing_type"
    ) != "track":
        snapshot = {
            "status": "not_track",
            "currently_playing_type": payload.get("currently_playing_type"),
            "polled_at_utc": polled_at_utc,
        }
    else:
        item = payload["item"]
        track_id = item.get("id")
        track_source = item
        album_data = track_source.get("album") or {}
        artists_data = track_source.get("artists") or []
        artists_ids = [
            artist.get("id") for artist in artists_data if artist and artist.get("id")
        ]
        artists_names = [
            artist.get("name")
            for artist in artists_data
            if artist and artist.get("name")
        ]
        images = [
            image.get("url")
            for image in album_data.get("images", [])
            if image and image.get("url")
        ]

        genres = track_source.get("genres") or []
        if not isinstance(genres, list):
            genres = []

        audio_features = track_source.get("audio_features") or {}
        if not isinstance(audio_features, dict):
            audio_features = {}

        track_name = track_source.get("name")
        duration_ms = track_source.get("duration_ms")
        album_id = album_data.get("id")

        # check minimium required fields for Track model
        if track_id and track_name and duration_ms is not None and album_id:
            track_model = Track(
                id=track_id,
                name=track_name,
                duration_ms=duration_ms,
                album_id=album_id,
                album_track=track_source.get("track_number"),
                artists_ids=artists_ids,
                acousticness=audio_features.get(
                    "acousticness", track_source.get("acousticness")
                ),
                danceability=audio_features.get(
                    "danceability", track_source.get("danceability")
                ),
                energy=audio_features.get("energy", track_source.get("energy")),
                explicit=track_source.get("explicit"),
                genres=genres,
                images=images,
                instrumentalness=audio_features.get(
                    "instrumentalness", track_source.get("instrumentalness")
                ),
                key=audio_features.get("key", track_source.get("key")),
                liveness=audio_features.get("liveness", track_source.get("liveness")),
                loudness=audio_features.get("loudness", track_source.get("loudness")),
                mode=audio_features.get("mode", track_source.get("mode")),
                popularity=track_source.get("popularity"),
                speechiness=audio_features.get(
                    "speechiness", track_source.get("speechiness")
                ),
                tempo=audio_features.get("tempo", track_source.get("tempo")),
                time_signature=audio_features.get(
                    "time_signature", track_source.get("time_signature")
                ),
                valence=audio_features.get("valence", track_source.get("valence")),
            )
            track_data = {
                "id": track_model.id,
                "name": track_model.name,
                "duration_ms": track_model.duration_ms,
                "album_id": track_model.album_id,
                "album_track": track_model.album_track,
                "artists": artists_names,
                "artists_ids": track_model.artists_ids,
                "acousticness": track_model.acousticness,
                "danceability": track_model.danceability,
                "energy": track_model.energy,
                "explicit": track_model.explicit,
                "genres": track_model.genres,
                "images": track_model.images,
                "instrumentalness": track_model.instrumentalness,
                "key": track_model.key,
                "liveness": track_model.liveness,
                "loudness": track_model.loudness,
                "mode": track_model.mode,
                "popularity": track_model.popularity,
                "speechiness": track_model.speechiness,
                "tempo": track_model.tempo,
                "time_signature": track_model.time_signature,
                "valence": track_model.valence,
                "album": album_data.get("name"),
                "external_url": track_source.get("external_urls", {}).get("spotify"),
                "preview_url": track_source.get("preview_url"),
                "progress_ms": payload.get("progress_ms"),
            }
        else:
            snapshot = {
                "status": "missing_required_fields",
                "polled_at_utc": polled_at_utc,
                "missing_fields": {
                    "id": track_id is None,
                    "name": track_name is None,
                    "duration_ms": duration_ms is None,
                    "album_id": album_id is None,
                },
            }

        device_data = payload.get("device") or {}
        snapshot = {
            "status": "playing" if payload.get("is_playing") else "paused",
            "polled_at_utc": polled_at_utc,
            "track": track_data,
            "device": {
                "name": device_data.get("name"),
                "type": device_data.get("type"),
                "is_active": device_data.get("is_active"),
            },
        }

    Path("now_playing.json").write_text(
        json.dumps(snapshot, indent=2, ensure_ascii=True),
        encoding="utf-8",
    )

    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if summary_path:
        lines = [
            "## Spotify currently playing",
            "",
            f"- Poll time (UTC): {snapshot.get('polled_at_utc')}",
            f"- Status: {snapshot.get('status')}",
        ]

        track_data = snapshot.get("track")
        if track_data:
            artists = ", ".join(track_data.get("artists", [])) or "Unknown"
            lines.extend(
                [
                    f"- Track: {track_data.get('name')}",
                    f"- Artists: {artists}",
                    f"- Album: {track_data.get('album')}",
                    f"- URL: {track_data.get('external_url')}",
                ]
            )

        with open(summary_path, "a", encoding="utf-8") as file:
            file.write("\n".join(lines) + "\n")

    print(json.dumps(snapshot, indent=2, ensure_ascii=True))


if __name__ == "__main__":
    main()
