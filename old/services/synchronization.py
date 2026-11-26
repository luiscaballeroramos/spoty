import random
import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from spotifyapi.errors import SpotifyError, SpotifyRateLimitError
from spotifyapi.timestamps import parse_spotify_timestamp


MAX_LIKED_TRACK_RATE_LIMIT_RETRIES = 3
MAX_LIKED_TRACK_SNAPSHOT_RETRIES = 1


class SyncStatus(Enum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    DEFERRED = "deferred"
    FAILED = "failed"


@dataclass(frozen=True)
class SyncResult:
    operation: str
    status: SyncStatus
    counters: dict[str, int] = field(default_factory=dict)
    message: str | None = None
    retry_after: float | None = None

    @property
    def exit_code(self) -> int:
        return 0 if self.status is SyncStatus.COMPLETED else 1

    def to_json(self) -> str:
        return json.dumps({
            "operation": self.operation,
            "status": self.status.value,
            "counters": self.counters,
            "message": self.message,
            "retry_after": self.retry_after,
        }, sort_keys=True)


class SynchronizationService:
    """Run Spotify synchronization use cases and return explicit outcomes."""

    def __init__(
        self,
        database_url: str | None = None,
        spotify_factory: Callable[[], Any] | None = None,
        database_factory: Callable[[str], Any] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        delay: Callable[[float, float], float] = random.uniform,
    ) -> None:
        self._database_url = database_url
        self._spotify_factory = spotify_factory
        self._database_factory = database_factory
        self._sleep = sleep
        self._delay = delay

    def _get_database_url(self) -> str:
        if self._database_url is not None:
            return self._database_url
        from config import DBNAME

        return DBNAME

    def _get_spotify_client(self):
        if self._spotify_factory is None:
            from spotifyapi.spotifyclient import SpotifyClient

            return SpotifyClient()
        return self._spotify_factory()

    def _get_database_factory(self):
        if self._database_factory is not None:
            return self._database_factory
        from register.db import SimpleDB

        return SimpleDB

    @staticmethod
    def _result(
        operation: str,
        status: SyncStatus,
        counters: dict[str, int],
        message: str | None = None,
        retry_after: float | None = None,
    ) -> SyncResult:
        return SyncResult(operation, status, counters, message, retry_after)

    def sync_listening_events(self, limit: int = 50) -> SyncResult:
        counters = {
            "fetched_items": 0,
            "skipped_invalid_events": 0,
            "saved_local_events": 0,
            "skipped_existing_listening_events": 0,
        }
        try:
            spotify = self._get_spotify_client()
            response = spotify.get_recently_played(limit=limit)
            items = response["items"]
            counters["fetched_items"] = len(items)

            candidates = []
            for item in items:
                track = item.get("track")
                track_id = track.get("id") if isinstance(track, dict) else None
                played_at = item.get("played_at")
                if not track_id or not played_at:
                    counters["skipped_invalid_events"] += 1
                    continue
                try:
                    played_at_utc = parse_spotify_timestamp(played_at)
                except (TypeError, ValueError):
                    counters["skipped_invalid_events"] += 1
                    continue
                candidates.append(
                    (item, track_id, played_at_utc, int(played_at_utc.timestamp()))
                )

            if not candidates:
                status = (
                    SyncStatus.PARTIAL
                    if counters["skipped_invalid_events"]
                    else SyncStatus.COMPLETED
                )
                return self._result("listening_events", status, counters)

            from register.listeningevent import ListeningEvent
            from register.spotify_data import models_from_spotify_track
            from services.listening_event_csv import ListeningEventCsvStore

            valid_events = []
            for item, track_id, played_at_utc, timestamp in candidates:
                models_from_spotify_track(item["track"])
                ListeningEvent(
                    track_id,
                    played_at_utc,
                    (item.get("context") or {}).get("uri"),
                )
                valid_events.append((item, track_id, timestamp))

            saved, skipped = ListeningEventCsvStore().append_events(valid_events)
            counters["saved_local_events"] = saved
            counters["skipped_existing_listening_events"] = skipped

            status = (
                SyncStatus.PARTIAL
                if counters["skipped_invalid_events"]
                else SyncStatus.COMPLETED
            )
            return self._result("listening_events", status, counters)
        except SpotifyRateLimitError as exc:
            return self._result(
                "listening_events",
                SyncStatus.DEFERRED,
                counters,
                str(exc),
                exc.retry_after,
            )
        except SpotifyError as exc:
            return self._result("listening_events", SyncStatus.FAILED, counters, str(exc))
        except Exception as exc:
            return self._result(
                "listening_events",
                SyncStatus.FAILED,
                counters,
                f"Synchronization failed ({type(exc).__name__}).",
            )

    def sync_liked_tracks(self, limit: int = 50, offset: int = 0) -> SyncResult:
        counters = {
            "downloaded_items": 0,
            "saved_count": 0,
            "removed_count": 0,
            "pages": 0,
            "snapshot_retries": 0,
        }
        if offset != 0:
            return self._result(
                "liked_tracks", SyncStatus.FAILED, counters,
                "Reconciliation must start at offset 0.",
            )

        try:
            database_url = self._get_database_url()
            spotify = self._get_spotify_client()
            with self._get_database_factory()(database_url) as database:
                generation, local_revision = database.reserve_liked_track_sync()

            page_size = max(1, limit)
            while True:
                current_offset = 0
                expected_total = None
                current_ids = []
                snapshot_changed = False
                while True:
                    rate_limit_retries = 0
                    while True:
                        try:
                            response = spotify.get_liked_songs(
                                limit=page_size, offset=current_offset
                            )
                            break
                        except SpotifyRateLimitError as exc:
                            if rate_limit_retries >= MAX_LIKED_TRACK_RATE_LIMIT_RETRIES:
                                raise
                            self._sleep(max(1.0, exc.retry_after))
                            rate_limit_retries += 1
                    items = response["items"]
                    response_offset = response.get("offset")
                    response_limit = response.get("limit")
                    response_total = response.get("total")
                    next_page = response.get("next")
                    if (
                        type(response_offset) is not int
                        or response_offset != current_offset
                        or type(response_limit) is not int
                        or response_limit <= 0
                        or type(response_total) is not int
                        or response_total < 0
                        or len(items) > response_limit
                    ):
                        return self._result(
                            "liked_tracks", SyncStatus.FAILED, counters,
                            "Spotify returned invalid pagination metadata.",
                        )
                    if expected_total is None:
                        expected_total = response_total
                    elif expected_total != response_total:
                        snapshot_changed = True
                        break

                    if response_offset + len(items) > response_total:
                        return self._result(
                            "liked_tracks", SyncStatus.FAILED, counters,
                            "Spotify returned invalid pagination metadata.",
                        )
                    next_offset = current_offset + len(items)
                    has_more = next_offset < response_total
                    if (has_more and (not isinstance(next_page, str) or not next_page)) or (
                        not has_more and next_page is not None
                    ) or (has_more and not items):
                        return self._result(
                            "liked_tracks", SyncStatus.FAILED, counters,
                            "Spotify returned invalid pagination metadata.",
                        )

                    for item in items:
                        track = item.get("track")
                        track_id = track.get("id") if isinstance(track, dict) else None
                        if not isinstance(track_id, str) or not track_id:
                            return self._result(
                                "liked_tracks", SyncStatus.PARTIAL, counters,
                                "Spotify returned an item without a track ID.",
                            )
                        current_ids.append(track_id)
                    counters["downloaded_items"] += len(items)
                    counters["pages"] += 1
                    if not has_more:
                        break
                    current_offset = next_offset
                    self._sleep(self._delay(10, 15))

                if not snapshot_changed:
                    break
                if counters["snapshot_retries"] >= MAX_LIKED_TRACK_SNAPSHOT_RETRIES:
                    return self._result(
                        "liked_tracks", SyncStatus.PARTIAL, counters,
                        "Spotify library changed during pagination.",
                    )
                counters["snapshot_retries"] += 1
                counters["downloaded_items"] = 0
                counters["pages"] = 0
                self._sleep(self._delay(10, 15))

            with self._get_database_factory()(database_url) as database:
                applied, saved_count, stale_ids = database.apply_liked_track_snapshot(
                    generation, local_revision, current_ids
                )
            if not applied:
                return self._result(
                    "liked_tracks", SyncStatus.DEFERRED, counters,
                    "A newer synchronization or local favorite change took precedence.",
                )
            counters["saved_count"] = saved_count
            counters["removed_count"] = len(stale_ids)
            return self._result("liked_tracks", SyncStatus.COMPLETED, counters)
        except SpotifyRateLimitError as exc:
            return self._result(
                "liked_tracks", SyncStatus.DEFERRED, counters,
                str(exc), exc.retry_after,
            )
        except SpotifyError as exc:
            return self._result("liked_tracks", SyncStatus.FAILED, counters, str(exc))
        except KeyError as exc:
            missing_key = exc.args[0] if len(exc.args) == 1 else None
            if isinstance(missing_key, str) and missing_key.isidentifier():
                message = f"Synchronization failed (missing field '{missing_key}')."
            else:
                message = "Synchronization failed (KeyError)."
            return self._result("liked_tracks", SyncStatus.FAILED, counters, message)
        except Exception as exc:
            return self._result(
                "liked_tracks",
                SyncStatus.FAILED,
                counters,
                f"Synchronization failed ({type(exc).__name__}).",
            )
