import json
import logging
import os
import time
from contextlib import contextmanager
from uuid import uuid4


LOGGER = logging.getLogger("spoty.operations")
SAFE_OPERATIONS = {
    "initialize Spotify client", "refresh Spotify token", "get artist",
    "get track artists", "get track", "get currently playing",
    "get recently played", "get top tracks", "get top artists", "get liked songs",
    "pause playback", "resume playback", "skip to next track",
    "go to previous track", "set shuffle", "set repeat mode",
    "get current playback", "get devices", "activate playback device",
    "add to queue", "get queue", "like track", "unlike track",
    "check liked status for tracks", "save liked track", "remove liked track",
}


class OperationFormatter(logging.Formatter):
    """Serialize only approved operation metadata without messages, arguments, or exception text."""
    def format(self, record: logging.LogRecord) -> str:
        """Return a JSON event containing safe diagnostic fields and its severity."""
        fields = {
            "event": record.msg,
            "level": record.levelname,
            "operation": record.operation,
            "provider": record.provider,
            "run_id": record.run_id,
            "duration_ms": record.duration_ms,
        }
        for name in ("error_type", "http_status", "retry_after", "count"):
            value = getattr(record, name, None)
            if value is not None:
                fields[name] = value
        return json.dumps(fields, ensure_ascii=True)


def configure_logging() -> None:
    """Configure one console handler with an optional SPOTY_LOG_LEVEL and no root logger changes."""
    if LOGGER.handlers:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(OperationFormatter())
    LOGGER.addHandler(handler)
    level = os.getenv("SPOTY_LOG_LEVEL", "INFO").upper()
    LOGGER.setLevel(level if level in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL") else "INFO")
    LOGGER.propagate = False


class OperationLog:
    """Group repeated failures per operation until recovery and avoid duplicate exception reports."""
    def __init__(self):
        """Initialize bounded incident state and configure console logging once."""
        self._incidents = {}
        configure_logging()

    @contextmanager
    def observe(self, action: str, provider: str = "spotify"):
        """Measure an operation, record its outcome once, and propagate exceptions unchanged."""
        operation = action if action in SAFE_OPERATIONS else "operation"
        provider = provider if provider in ("spotify", "postgresql") else "application"
        started_at = time.perf_counter()
        run_id = uuid4().hex
        key = (provider, operation)
        try:
            yield
        except Exception as exc:
            if not getattr(exc, "_spoty_reported", False):
                status = getattr(exc, "http_status", None)
                status = status if isinstance(status, int) else None
                signature = (type(exc).__name__, status)
                previous = self._incidents.get(key)
                if previous and previous["signature"] == signature:
                    previous["count"] += 1
                    run_id = previous["run_id"]
                else:
                    self._incidents[key] = {"signature": signature, "count": 1, "run_id": run_id}
                    details = {
                        "operation": operation,
                        "provider": provider,
                        "run_id": run_id,
                        "duration_ms": round((time.perf_counter() - started_at) * 1000, 3),
                        "error_type": type(exc).__name__,
                        "http_status": status,
                        "count": 1,
                    }
                    retry_after = getattr(exc, "retry_after", None)
                    if isinstance(retry_after, (int, float)):
                        details["retry_after"] = retry_after
                    LOGGER.log(logging.WARNING if provider == "spotify" else logging.ERROR, "operation_failed", extra=details)
                exc.run_id = run_id
                exc._spoty_reported = True
            raise
        else:
            previous = self._incidents.pop(key, None)
            details = {
                "operation": operation,
                "provider": provider,
                "run_id": previous["run_id"] if previous else run_id,
                "duration_ms": round((time.perf_counter() - started_at) * 1000, 3),
                "count": previous["count"] if previous else 1,
            }
            LOGGER.log(logging.INFO if previous else logging.DEBUG, "operation_recovered" if previous else "operation_succeeded", extra=details)


OPERATIONS = OperationLog()
