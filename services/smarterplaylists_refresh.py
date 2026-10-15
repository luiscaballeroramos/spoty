"""Bounded refresh orchestration; no database transaction remains open during HTTP."""

from metadata_sources.smarterplaylists import SmarterPlaylistsError
from register.smarterplaylists import SmarterPlaylistsRepository
from services.smarterplaylists import normalize_capture


class SmarterPlaylistsRefreshService:
    def __init__(self, database_url, adapter_factory, repository=None):
        self._database_url = database_url
        self._adapter_factory = adapter_factory
        self._repository = repository or SmarterPlaylistsRepository()

    def refresh(self, limit=10, refresh_hours=20, dry_run=False):
        candidates = self._repository.get_refresh_candidates(self._database_url, limit, refresh_hours)
        result = {"operation": "smarterplaylists_refresh", "status": "completed",
                  "candidates": {entity: len(rows) for entity, rows in candidates.items()},
                  "records_saved": 0, "errors": {}, "dry_run": dry_run}
        if dry_run or not any(candidates.values()):
            return result

        def outcome(entity, spotify_id, status, code=None):
            self._repository.record_refresh_outcome(self._database_url, entity, spotify_id,
                status, refresh_hours=refresh_hours, error_code=code)
            if code:
                result["errors"][code] = result["errors"].get(code, 0) + 1

        adapter = None
        try:
            try:
                adapter = self._adapter_factory()
                adapter.authenticate()
            except SmarterPlaylistsError as error:
                for entity, rows in candidates.items():
                    for row in rows:
                        outcome(entity, row["id"], "deferred", error.code)
                return {**result, "status": "deferred"}
            for entity, rows in candidates.items():
                if not rows:
                    continue
                try:
                    reports, _missing = adapter.capture(entity, rows)
                except SmarterPlaylistsError as error:
                    for row in rows:
                        outcome(entity, row["id"], "error", error.code)
                    continue
                remaining = {row["id"] for row in rows}
                for report in reports:
                    try:
                        batch = normalize_capture(report)
                        if len(batch.records) != 1:
                            raise ValueError("Unexpected multi-entity capture.")
                        record = batch.records[0]
                        if record.entity != entity or record.spotify_id not in remaining:
                            raise ValueError("Unexpected refresh entity.")
                    except ValueError:
                        continue
                    try:
                        saved = self._repository.import_batch(self._database_url, batch)
                    except ValueError:
                        outcome(entity, record.spotify_id, "error", "capture_conflict")
                    else:
                        result["records_saved"] += saved["records_saved"]
                        outcome(entity, record.spotify_id, record.status)
                    remaining.remove(record.spotify_id)
                for spotify_id in remaining:
                    outcome(entity, spotify_id, "error", "missing_or_invalid_response")
        finally:
            if adapter is not None:
                adapter.close()
        if result["errors"]:
            result["status"] = "partial"
        return result
