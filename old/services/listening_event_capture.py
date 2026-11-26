import time
from typing import Any, Callable, MutableMapping

from services.synchronization import SyncResult, SynchronizationService


LISTENING_EVENT_SYNC_INTERVAL_SECONDS = 5 * 60
LAST_LISTENING_EVENT_SYNC_KEY = "last_listening_event_sync_at"


def capture_listening_events_if_due(
    state: MutableMapping[str, Any],
    *,
    now: float | None = None,
    sync: Callable[[], SyncResult] | None = None,
    interval_seconds: int = LISTENING_EVENT_SYNC_INTERVAL_SECONDS,
) -> SyncResult | None:
    current_time = time.monotonic() if now is None else now
    last_sync_at = state.get(LAST_LISTENING_EVENT_SYNC_KEY)
    if (
        isinstance(last_sync_at, (int, float))
        and current_time - last_sync_at < interval_seconds
    ):
        return None

    state[LAST_LISTENING_EVENT_SYNC_KEY] = current_time
    sync_operation = sync or SynchronizationService().sync_listening_events
    return sync_operation()
