import csv
import json
import os
import threading
from pathlib import Path
from typing import Any


CSV_SCHEMA_VERSION = "1"
CSV_FIELDS = (
    "schema_version",
    "track_id",
    "played_at",
    "context_source",
    "context_type",
    "context_id",
    "spotify_item_json",
)

_CSV_LOCK = threading.Lock()


def default_listening_events_csv_path() -> Path:
    configured_path = os.getenv("SPOTY_LISTENING_EVENTS_CSV", "").strip()
    if configured_path:
        path = Path(configured_path).expanduser()
        if not path.is_absolute():
            path = Path(__file__).resolve().parents[1] / path
        return path
    return (
        Path(__file__).resolve().parents[1]
        / "external data"
        / "listening_events.csv"
    )


class ListeningEventCsvStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_listening_events_csv_path()

    def append_events(
        self, events: list[tuple[dict[str, Any], str, int]]
    ) -> tuple[int, int]:
        if not events:
            return 0, 0

        self.path.parent.mkdir(parents=True, exist_ok=True)
        with _CSV_LOCK:
            existing_keys = self._read_event_keys()
            new_rows = []
            skipped = 0
            for item, track_id, played_at in events:
                key = (track_id, played_at)
                if key in existing_keys:
                    skipped += 1
                    continue
                existing_keys.add(key)
                context = item.get("context") or {}
                context_uri = context.get("uri") if isinstance(context, dict) else None
                context_source, context_type, context_id = parse_context_uri(
                    context_uri
                )
                new_rows.append({
                    "schema_version": CSV_SCHEMA_VERSION,
                    "track_id": track_id,
                    "played_at": played_at,
                    "context_source": context_source or "",
                    "context_type": context_type or "",
                    "context_id": context_id or "",
                    "spotify_item_json": json.dumps(
                        item, ensure_ascii=True, separators=(",", ":")
                    ),
                })

            if not new_rows:
                return 0, skipped

            file_exists = self.path.exists() and self.path.stat().st_size > 0
            with self.path.open("a", encoding="utf-8", newline="") as csv_file:
                writer = csv.DictWriter(csv_file, fieldnames=CSV_FIELDS)
                if not file_exists:
                    writer.writeheader()
                writer.writerows(new_rows)
                csv_file.flush()
                os.fsync(csv_file.fileno())

        return len(new_rows), skipped

    def _read_event_keys(self) -> set[tuple[str, int]]:
        if not self.path.exists() or self.path.stat().st_size == 0:
            return set()

        with self.path.open("r", encoding="utf-8", newline="") as csv_file:
            reader = csv.DictReader(csv_file)
            if tuple(reader.fieldnames or ()) != CSV_FIELDS:
                raise ValueError(
                    f"Listening-events CSV has an unsupported header: {self.path}"
                )

            keys = set()
            for line_number, row in enumerate(reader, start=2):
                if row.get("schema_version") != CSV_SCHEMA_VERSION:
                    raise ValueError(
                        f"Unsupported listening-events CSV schema version "
                        f"on line {line_number}."
                    )
                try:
                    key = (row["track_id"], int(row["played_at"]))
                except (KeyError, TypeError, ValueError) as exc:
                    raise ValueError(
                        f"Invalid listening-event key on CSV line {line_number}."
                    ) from exc
                if not key[0]:
                    raise ValueError(
                        f"Missing track ID on listening-events CSV line {line_number}."
                    )
                keys.add(key)
            return keys


def parse_context_uri(
    context_uri: Any,
) -> tuple[str | None, str | None, str | None]:
    if not isinstance(context_uri, str) or not context_uri:
        return None, None, None
    parts = context_uri.split(":")
    if len(parts) != 3:
        return None, None, None
    return parts[0], parts[1], parts[2]
