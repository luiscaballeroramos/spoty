import argparse
import csv
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from register.album import Album
from register.artist import Artist
from register.listeningevent import ListeningEvent
from register.spotify_data import models_from_spotify_track
from register.track import Track
from services.listening_event_csv import (
    CSV_FIELDS,
    CSV_SCHEMA_VERSION,
    default_listening_events_csv_path,
    parse_context_uri,
)
from spotifyapi.timestamps import parse_spotify_timestamp


@dataclass(frozen=True)
class CsvListeningEvent:
    track_model: Track
    album_model: Album
    artist_models: list[Artist]
    event_model: ListeningEvent


def read_listening_events_csv(path: Path) -> list[CsvListeningEvent]:
    if not path.exists():
        raise FileNotFoundError(f"Listening-events CSV not found: {path}")

    events = []
    seen_keys = set()
    with path.open("r", encoding="utf-8", newline="") as csv_file:
        reader = csv.DictReader(csv_file)
        if tuple(reader.fieldnames or ()) != CSV_FIELDS:
            raise ValueError(f"Listening-events CSV has an unsupported header: {path}")

        for line_number, row in enumerate(reader, start=2):
            if row.get("schema_version") != CSV_SCHEMA_VERSION:
                raise ValueError(
                    f"Unsupported listening-events CSV schema version "
                    f"on line {line_number}."
                )
            try:
                item = json.loads(row["spotify_item_json"])
                track_id = row["track_id"]
                played_at = int(row["played_at"])
                track_data = item["track"]
                item_track_id = track_data["id"]
                played_at_utc = parse_spotify_timestamp(item["played_at"])
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise ValueError(
                    f"Invalid listening-event data on CSV line {line_number}."
                ) from exc

            if not track_id or item_track_id != track_id:
                raise ValueError(
                    f"Track ID mismatch on listening-events CSV line {line_number}."
                )
            if int(played_at_utc.timestamp()) != played_at:
                raise ValueError(
                    f"Timestamp mismatch on listening-events CSV line {line_number}."
                )

            context = item.get("context") or {}
            context_uri = context.get("uri") if isinstance(context, dict) else None
            expected_context = parse_context_uri(context_uri)
            row_context = tuple(
                row.get(field, "") or ""
                for field in ("context_source", "context_type", "context_id")
            )
            if row_context != tuple(value or "" for value in expected_context):
                raise ValueError(
                    f"Context mismatch on listening-events CSV line {line_number}."
                )

            event_key = (track_id, played_at)
            if event_key in seen_keys:
                continue
            seen_keys.add(event_key)

            try:
                track_model, album_model, artist_models = models_from_spotify_track(
                    track_data
                )
                event_model = ListeningEvent(track_id, played_at_utc, context_uri)
            except (KeyError, TypeError, ValueError, AttributeError) as exc:
                raise ValueError(
                    f"Invalid Spotify track data on listening-events CSV "
                    f"line {line_number}."
                ) from exc
            events.append(CsvListeningEvent(
                track_model,
                album_model,
                artist_models,
                event_model,
            ))
    return events


class ListeningEventCsvImporter:
    def __init__(
        self,
        path: Path,
        database_factory: Callable[[str], Any] | None = None,
    ) -> None:
        self.path = path
        self.database_factory = database_factory

    def run(self, apply: bool = False) -> dict[str, Any]:
        events = read_listening_events_csv(self.path)
        result = {
            "status": "validated" if not apply else "completed",
            "mode": "apply" if apply else "dry-run",
            "csv_path": str(self.path),
            "validated_events": len(events),
            "inserted_events": 0,
            "skipped_existing_events": 0,
            "inserted_tracks": 0,
            "inserted_albums": 0,
            "inserted_artists": 0,
        }
        if not apply:
            return result

        from config import require_database_url

        if self.database_factory is None:
            from register.db import SimpleDB

            database_factory = SimpleDB
        else:
            database_factory = self.database_factory

        with database_factory(require_database_url()) as database:
            for event in events:
                with database.transaction():
                    for artist in event.artist_models:
                        result["inserted_artists"] += bool(
                            artist.save(database, print_only_insert=True)
                        )
                    result["inserted_albums"] += bool(
                        event.album_model.save(database, print_only_insert=True)
                    )
                    result["inserted_tracks"] += bool(
                        event.track_model.save(database, print_only_insert=True)
                    )
                    inserted = event.event_model.save(
                        database, print_only_insert=True
                    )
                    if inserted:
                        result["inserted_events"] += 1
                    else:
                        result["skipped_existing_events"] += 1
        return result


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Validate or import locally captured listening events into Neon."
    )
    parser.add_argument(
        "--csv",
        type=Path,
        default=default_listening_events_csv_path(),
        help="Listening-events CSV path.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--apply",
        action="store_true",
        help="Write validated events to Neon. Without this flag, only validate.",
    )
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate the CSV without connecting to Neon (the default).",
    )
    args = parser.parse_args(argv)
    try:
        result = ListeningEventCsvImporter(args.csv).run(apply=args.apply)
    except Exception as exc:
        print(
            f"Listening-event import failed ({type(exc).__name__}): {exc}",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
