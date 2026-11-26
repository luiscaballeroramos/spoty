import re
from datetime import datetime, timezone


class ListeningEvent:
    def __init__(
        self, track_id: str, played_at: datetime, context_uri: str | None = None
    ) -> None:
        if not isinstance(track_id, str) or not re.fullmatch(r"[A-Za-z0-9]{22}", track_id):
            raise ValueError("Invalid listening-event track ID.")
        if played_at.tzinfo is None or played_at.utcoffset() is None:
            raise ValueError("Listening-event timestamps must be timezone-aware.")
        if played_at.timestamp() < 0:
            raise ValueError("Invalid listening-event timestamp.")
        if context_uri is not None and not isinstance(context_uri, str):
            raise ValueError("Invalid listening-event context URI.")

        self.track_id = track_id
        self.played_at = played_at.astimezone(timezone.utc)
        parts = context_uri.split(":") if context_uri else []
        context = parts if len(parts) == 3 and all(parts) else [None, None, None]
        self.context_source, self.context_type, self.context_id = context

    def to_dict(self) -> dict[str, str | int | None]:
        return {
            "track_id": self.track_id,
            "played_at": int(self.played_at.timestamp()),
            "context_source": self.context_source,
            "context_type": self.context_type,
            "context_id": self.context_id,
        }
