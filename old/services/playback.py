import json
import time
from pathlib import Path
from typing import Any, Callable, MutableMapping

from spotifyapi.errors import SpotifyRequestError


class PlaybackSnapshotStore:
    """Persist a restart-safe playback snapshot without depending on Streamlit."""

    def __init__(
        self,
        path: Path,
        monotonic: Callable[[], float] = time.monotonic,
        wall_time: Callable[[], float] = time.time,
    ) -> None:
        self._path = path
        self._monotonic = monotonic
        self._wall_time = wall_time

    def load(self) -> dict[str, Any] | None:
        try:
            saved_state = json.loads(self._path.read_text(encoding="utf-8"))
            playback = saved_state.get("playback")
            if not isinstance(playback, dict) or not playback.get("item"):
                return None
            paused_at_epoch = saved_state.get("paused_at_epoch")
            if isinstance(paused_at_epoch, (int, float)):
                paused_elapsed = max(0, self._wall_time() - paused_at_epoch)
                playback["paused_at"] = self._monotonic() - paused_elapsed
            return playback
        except (OSError, json.JSONDecodeError, AttributeError):
            return None

    def save(self, playback: dict[str, Any]) -> None:
        if not playback.get("item"):
            return
        snapshot = dict(playback)
        paused_at = snapshot.pop("paused_at", None)
        saved_state = {
            "playback": snapshot,
            "paused_at_epoch": (
                self._wall_time() - (self._monotonic() - paused_at)
                if isinstance(paused_at, (int, float))
                else None
            ),
        }
        temporary_path = self._path.with_suffix(".tmp")
        try:
            temporary_path.write_text(
                json.dumps(saved_state, ensure_ascii=True), encoding="utf-8"
            )
            temporary_path.replace(self._path)
        except OSError:
            temporary_path.unlink(missing_ok=True)

    def clear(self) -> None:
        try:
            self._path.unlink(missing_ok=True)
        except OSError:
            return


class PlaybackController:
    """Coordinate playback polling, restoration, transport actions, and snapshots."""

    def __init__(
        self,
        client: Any,
        state: MutableMapping[str, Any],
        snapshot_store: PlaybackSnapshotStore,
        monotonic: Callable[[], float] = time.monotonic,
        playing_poll_seconds: float = 5,
        paused_poll_seconds: float = 5,
        restore_after_seconds: float = 60,
        playback_cache_key: str = "reproduction_playback_cache",
        paused_playback_key: str = "reproduction_paused_playback",
        persisted_loaded_key: str = "reproduction_persisted_playback_loaded",
        previous_request_key: str = "reproduction_previous_request",
        adjacent_cache_key: str = "reproduction_adjacent_tracks_cache",
        adjacent_history_key: str = "reproduction_adjacent_track_history",
        adjacent_poll_seconds: float = 30,
    ) -> None:
        self._client = client
        self._state = state
        self._snapshot_store = snapshot_store
        self._monotonic = monotonic
        self._playing_poll_seconds = playing_poll_seconds
        self._paused_poll_seconds = paused_poll_seconds
        self._restore_after_seconds = restore_after_seconds
        self._cache_key = playback_cache_key
        self._paused_key = paused_playback_key
        self._loaded_key = persisted_loaded_key
        self._previous_request_key = previous_request_key
        self._adjacent_cache_key = adjacent_cache_key
        self._adjacent_history_key = adjacent_history_key
        self._adjacent_poll_seconds = adjacent_poll_seconds

    @staticmethod
    def estimate_progress(
        playback: dict[str, Any], fetched_at: float, now: float
    ) -> dict[str, Any]:
        estimated = dict(playback)
        if not playback.get("is_playing"):
            return estimated
        item = playback.get("item") or {}
        progress_ms = playback.get("progress_ms")
        duration_ms = item.get("duration_ms")
        if not isinstance(progress_ms, (int, float)) or not isinstance(
            duration_ms, (int, float)
        ):
            return estimated
        elapsed_ms = (now - fetched_at) * 1000
        estimated["progress_ms"] = min(int(progress_ms + elapsed_ms), int(duration_ms))
        return estimated

    def _load_snapshot_once(self) -> None:
        if self._state.get(self._loaded_key):
            return
        self._state[self._loaded_key] = True
        saved_playback = self._snapshot_store.load()
        if saved_playback:
            self._state[self._paused_key] = saved_playback

    def get_playback(self) -> dict[str, Any]:
        self._load_snapshot_once()
        now = self._monotonic()
        saved_playback = self._state.get(self._paused_key)
        cached = self._state.get(self._cache_key)
        if cached:
            playback = cached["playback"]
            item = playback.get("item") or {}
            progress_ms = playback.get("progress_ms")
            duration_ms = item.get("duration_ms")
            elapsed_ms = (now - cached["fetched_at"]) * 1000
            if not playback.get("is_playing"):
                if elapsed_ms < self._paused_poll_seconds * 1000:
                    if playback.get("item"):
                        return playback
                    return {**saved_playback, "is_playing": False} if saved_playback else {}
            elif (
                isinstance(progress_ms, (int, float))
                and isinstance(duration_ms, (int, float))
                and elapsed_ms < self._playing_poll_seconds * 1000
            ):
                return self.estimate_progress(playback, cached["fetched_at"], now)
            elif not isinstance(progress_ms, (int, float)) or not isinstance(
                duration_ms, (int, float)
            ):
                return playback

        playback = self._client.get_current_playback() or {}
        if saved_playback and playback.get("item"):
            if playback["item"].get("id") != (saved_playback.get("item") or {}).get(
                "id"
            ) or playback.get("is_playing"):
                self._state.pop(self._paused_key, None)
                saved_playback = None
        if playback.get("item"):
            saved_item_id = (saved_playback or {}).get("item", {}).get("id")
            if (
                not playback.get("is_playing")
                and saved_playback
                and saved_item_id == playback["item"].get("id")
            ):
                if saved_playback.get("paused_at") is not None:
                    playback["paused_at"] = saved_playback["paused_at"]
                if saved_playback.get("queue_uris"):
                    playback["queue_uris"] = saved_playback["queue_uris"]
            elif not playback.get("is_playing"):
                playback["paused_at"] = now
            self._snapshot_store.save(playback)
        self._state[self._cache_key] = {"playback": playback, "fetched_at": now}
        if not playback.get("item") and saved_playback:
            return {**saved_playback, "is_playing": False}
        return playback

    def start_playback_for_restore(self, **kwargs) -> bool:
        try:
            return self._client.start_playback(**kwargs)
        except SpotifyRequestError as exc:
            if exc.http_status not in (400, 404):
                raise
            return False

    def resume_playback(
        self, playback: dict[str, Any], live_playback: dict[str, Any]
    ) -> bool | None:
        snapshot = self._state.get(self._paused_key) or live_playback or playback
        device_id = (snapshot.get("device") or {}).get("id")
        item = snapshot.get("item") or {}
        if not device_id:
            return None

        target_is_active = (live_playback.get("device") or {}).get("id") == device_id
        if not target_is_active:
            try:
                if not self._client.transfer_playback(device_id):
                    return None
            except SpotifyRequestError as exc:
                if exc.http_status != 404:
                    raise
                return None

        paused_at = snapshot.get("paused_at")
        context_uri = (snapshot.get("context") or {}).get("uri")
        live_context_uri = (live_playback.get("context") or {}).get("uri")
        context_changed = bool(context_uri and live_context_uri != context_uri)
        live_track_id = (live_playback.get("item") or {}).get("id")
        missing_playback = (
            not live_playback.get("item")
            or not target_is_active
            or live_track_id != item.get("id")
            or paused_at is None
            or self._monotonic() - paused_at >= self._restore_after_seconds
            or context_changed
        )
        queue_uris = snapshot.get("queue_uris") or []
        restore_context = context_uri if missing_playback else None
        succeeded = self.start_playback_for_restore(
            device_id=device_id,
            uri=item.get("uri") if missing_playback else None,
            position_ms=snapshot.get("progress_ms") if missing_playback else None,
            **(
                {"context_uri": restore_context}
                if restore_context and item.get("uri")
                else (
                    {"uris": [item["uri"], *queue_uris]}
                    if missing_playback and item.get("uri") and queue_uris
                    else {}
                )
            ),
        )
        if not succeeded and restore_context and item.get("uri"):
            succeeded = self.start_playback_for_restore(
                device_id=device_id,
                uri=item["uri"],
                uris=[item["uri"], *queue_uris] if queue_uris else None,
                position_ms=snapshot.get("progress_ms"),
            )
        if not succeeded and not missing_playback and item.get("uri"):
            succeeded = self.start_playback_for_restore(
                device_id=device_id,
                uri=item["uri"],
                position_ms=snapshot.get("progress_ms"),
            )
        return succeeded

    def toggle_playback(self, playback: dict[str, Any]) -> bool | None:
        live_playback = self._client.get_current_playback() or {}
        if live_playback.get("is_playing"):
            device_id = (live_playback.get("device") or {}).get("id")
            succeeded = self._client.pause_playback(device_id=device_id)
            if succeeded and live_playback.get("item"):
                self.save_paused_playback(live_playback)
        else:
            succeeded = self.resume_playback(playback, live_playback)
            if succeeded is None:
                return None
        if succeeded:
            self.invalidate_playback_cache()
        return succeeded

    def save_paused_playback(self, playback: dict[str, Any]) -> None:
        saved_playback = {**playback, "paused_at": self._monotonic()}
        queue = self._client.get_queue() or {}
        if (queue.get("currently_playing") or {}).get("id") == playback["item"].get("id"):
            saved_playback["queue_uris"] = [
                track["uri"]
                for track in queue.get("queue") or []
                if track.get("uri", "").startswith("spotify:track:")
            ][:99]
        self._state[self._paused_key] = saved_playback
        self._snapshot_store.save(saved_playback)

    def invalidate_playback_cache(self) -> None:
        self._state.pop(self._cache_key, None)

    def clear_paused_playback(self) -> None:
        self._state.pop(self._paused_key, None)
        self._snapshot_store.clear()

    def adjacent_tracks(
        self, current_track: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        current_track_id = current_track.get("id")
        if not current_track_id:
            return {}, {}

        cached = self._state.get(self._adjacent_cache_key)
        if cached and cached.get("current_track_id") == current_track_id:
            if self._monotonic() - cached.get("fetched_at", 0) >= self._adjacent_poll_seconds:
                queue = self._client.get_queue()
                if queue is not None:
                    cached["next"] = next(
                        (track for track in queue.get("queue") or [] if track.get("id")),
                        {},
                    )
                    cached["fetched_at"] = self._monotonic()
            return cached.get("previous") or {}, cached.get("next") or {}

        history = self._state.setdefault(self._adjacent_history_key, {})
        previous_request = self._state.pop(self._previous_request_key, None)
        if cached and current_track_id == previous_request:
            previous_track = history.get(current_track_id) or {}
        elif cached and (cached.get("current") or {}).get("id"):
            previous_track = cached["current"]
        else:
            recently_played = self._client.get_recently_played() or {}
            recent_tracks = [
                entry.get("track") or {}
                for entry in recently_played.get("items") or []
            ]
            for index, track in enumerate(recent_tracks):
                if track.get("id") and track["id"] not in history:
                    history[track["id"]] = next(
                        (
                            older
                            for older in recent_tracks[index + 1 :]
                            if older.get("id") and older["id"] != track["id"]
                        ),
                        {},
                    )
            previous_track = next(
                (
                    track
                    for track in recent_tracks
                    if track.get("id") and track["id"] != current_track_id
                ),
                {},
            )

        queue = self._client.get_queue() or {}
        next_track = next(
            (track for track in queue.get("queue") or [] if track.get("id")), {}
        )
        history[current_track_id] = previous_track
        self._state[self._adjacent_cache_key] = {
            "current_track_id": current_track_id,
            "current": current_track,
            "previous": previous_track,
            "next": next_track,
            "fetched_at": self._monotonic(),
        }
        return previous_track, next_track

    def previous_track(self, previous_track: dict[str, Any]) -> bool:
        if not self._client.previous_track():
            return False
        self.invalidate_playback_cache()
        self.clear_paused_playback()
        self._state[self._previous_request_key] = previous_track.get("id")
        return True

    def next_track(self, playback: dict[str, Any]) -> bool:
        live_playback = self._client.get_current_playback() or {}
        device_id = (live_playback.get("device") or playback.get("device") or {}).get("id")
        device = None
        if device_id:
            devices = self._client.get_devices()
            device = next(
                (available for available in (devices or {}).get("devices") or []
                 if available.get("id") == device_id),
                None,
            )
            if device is not None and not device.get("is_active"):
                if not self._client.transfer_playback(device_id):
                    return False

        saved_playback = self._state.get(self._paused_key) or {}
        paused_at = saved_playback.get("paused_at")
        needs_restore = (
            bool(saved_playback)
            and not live_playback.get("is_playing")
            and (
                not live_playback.get("item")
                or (device is not None and not device.get("is_active"))
                or (
                    paused_at is not None
                    and self._monotonic() - paused_at >= self._restore_after_seconds
                )
            )
        )
        if needs_restore:
            queue = self._client.get_queue()
            if queue is not None:
                current_id = (live_playback.get("item") or playback.get("item") or {}).get("id")
                queued_current_id = (queue.get("currently_playing") or {}).get("id")
                if queued_current_id and queued_current_id != current_id:
                    return False
                queue_uris = [
                    track["uri"]
                    for track in queue.get("queue") or []
                    if track.get("uri", "").startswith("spotify:track:")
                ][:100]
            else:
                queue_uris = saved_playback.get("queue_uris") or []
            current_uri = (live_playback.get("item") or playback.get("item") or {}).get("uri")
            if not queue_uris or queue_uris[0] == current_uri:
                return False

            context_uri = (saved_playback.get("context") or {}).get("uri")
            succeeded = (
                self.start_playback_for_restore(
                    device_id=device_id, uri=queue_uris[0], context_uri=context_uri
                )
                if context_uri
                else False
            )
            if not succeeded:
                succeeded = self.start_playback_for_restore(
                    device_id=device_id, uris=queue_uris
                )
        else:
            succeeded = self._client.next_track(device_id=device_id)

        if succeeded:
            self.invalidate_playback_cache()
            self.clear_paused_playback()
            self._state.pop(self._previous_request_key, None)
        return succeeded
