from datetime import datetime, timezone


def parse_spotify_timestamp(value: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError("Spotify played_at must be a nonempty ISO-8601 timestamp.")

    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Spotify played_at must include a timezone.")
    return parsed.astimezone(timezone.utc)
