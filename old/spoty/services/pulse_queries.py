import math
from typing import Any

from spoty.domain.listening import ValidationError
from spoty.storage.snapshots import Snapshot


class PulseQueryService:
    """Read the existing Pulse row contract without loading original metadata."""

    def __init__(self, snapshot: Snapshot):
        self.snapshot = snapshot

    def recent_audio_history(
        self, current_id: str, now: float, hours: int | None = 2,
        current_started_at: float | None = None,
    ) -> list[dict[str, Any]]:
        if (
            not isinstance(current_id, str) or type(now) not in (int, float) or not math.isfinite(now)
            or (hours is not None and (type(hours) is not int or hours < 0))
            or (current_started_at is not None and (
                type(current_started_at) not in (int, float) or not math.isfinite(current_started_at)
            ))
        ):
            raise ValidationError("Invalid Pulse history query.")
        audio = {row["spotify_track_id"]: row for row in self.snapshot.rows("track_audio")}
        tracks = {row["id"]: row for row in self.snapshot.rows("tracks")}

        def details(track_id):
            metadata = audio.get(track_id, {})
            return {
                "name": tracks.get(track_id, {}).get("name") or metadata.get("name"),
                "audio": metadata.get("audio", {}),
            }

        started = current_started_at if current_started_at is not None else now
        rows = []
        for event in self.snapshot.rows("listening_events"):
            played_at = event["played_at"]
            if played_at > now or (hours is not None and played_at < now - hours * 3600):
                continue
            if event["track_id"] == current_id and played_at >= started:
                continue
            rows.append({
                "kind": "history", "track_id": event["track_id"], "played_at": played_at,
                **details(event["track_id"]),
            })
        rows.sort(key=lambda row: (row["played_at"], row["track_id"]))
        rows.append({
            "kind": "current", "track_id": current_id, "played_at": None, **details(current_id),
        })
        return rows
