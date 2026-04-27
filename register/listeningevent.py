from datetime import datetime

from register.db import SimpleDB


class ListeningEvent:
    def __init__(self, track_id: str, played_at: datetime, context_uri: str):
        self.track_id = track_id
        self.played_at = played_at
        self.context_source = None
        self.context_type = None
        self.context_id = None

        if context_uri:
            parts = context_uri.split(":")
            if len(parts) == 3:
                self.context_source, self.context_type, self.context_id = parts

    def save(self, db: "SimpleDB", print_only_insert=False) -> bool:
        timestamp = int(self.played_at.timestamp())

        return db.insert("listening_events", {
            "track_id": self.track_id,
            "played_at": timestamp,
            "context_source": self.context_source,
            "context_type": self.context_type,
            "context_id": self.context_id
        }, print_only_insert=print_only_insert)
