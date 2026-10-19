"""Bounded refresh orchestration; no database transaction remains open during HTTP."""

import re
import time

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
        return self._refresh_candidates(candidates, refresh_hours, dry_run)

    def lookup_track(self, spotify_id):
        if not isinstance(spotify_id, str) or not re.fullmatch(r"[A-Za-z0-9]{22}", spotify_id):
            raise ValueError("Invalid Spotify track ID.")
        adapter = self._adapter_factory()
        try:
            adapter.authenticate()
            reports, _missing_ids = adapter.capture("track", [{"id": spotify_id}])
        finally:
            adapter.close()

        if not reports:
            return {"status": "not_found", "audio": {}}
        batch = normalize_capture(reports[0])
        if len(batch.records) != 1:
            raise ValueError("Unexpected single-track lookup response.")
        record = batch.records[0]
        if record.entity != "track" or record.spotify_id != spotify_id:
            raise ValueError("Unexpected track in single-track lookup response.")
        return {
            "status": "completed",
            "audio": record.normalized_data,
            "missing_fields": record.missing_requested,
        }

    def refresh_all(self, batch_size=50, refresh_hours=20, dry_run=False, *,
                    only_unattempted=False, max_seconds=18000, clock=time.monotonic,
                    progress=None):
        if not 1 <= batch_size <= 100 or max_seconds <= 0:
            raise ValueError("Invalid bulk batch size or time budget.")
        started = clock()
        candidates = self._repository.get_refresh_candidates(
            self._database_url, None, refresh_hours, only_unattempted=only_unattempted)
        scope = "never_attempted" if only_unattempted else "all_due"
        result = {"operation": "smarterplaylists_refresh", "scope": scope,
                  "status": "completed", "dry_run": dry_run,
                  "candidates": {entity: len(rows) for entity, rows in candidates.items()},
                  "attempted": {entity: 0 for entity in candidates},
                  "remaining": {entity: len(rows) for entity, rows in candidates.items()},
                  "records_saved": 0, "errors": {}, "batches": 0}
        if dry_run:
            return result
        offsets = {entity: 0 for entity in candidates}
        while any(result["remaining"].values()):
            if clock() - started >= max_seconds:
                result["status"] = "incomplete"
                result["stop_reason"] = "time_budget"
                break
            selected = {entity: rows[offsets[entity]:offsets[entity] + batch_size]
                        for entity, rows in candidates.items()}
            batch = self._refresh_candidates(selected, refresh_hours, False)
            result["batches"] += 1
            result["records_saved"] += batch["records_saved"]
            for entity, rows in selected.items():
                offsets[entity] += len(rows)
                result["attempted"][entity] += len(rows)
                result["remaining"][entity] -= len(rows)
            for code, count in batch["errors"].items():
                result["errors"][code] = result["errors"].get(code, 0) + count
            if progress:
                progress({"operation": "smarterplaylists_refresh_progress",
                          "batch": result["batches"], "records_saved": result["records_saved"],
                          "remaining": dict(result["remaining"]), "batch_status": batch["status"]})
            fatal = {"rate_limited", "session_expired", "session_expired_or_denied",
                     "session_missing_or_invalid", "unowned_program", "program_name_collision",
                     "inventory_changed", "unexpected_playlist_output"} & batch["errors"].keys()
            if batch["status"] == "deferred" or fatal:
                result["status"] = "deferred" if batch["status"] == "deferred" else "partial"
                result["stop_reason"] = sorted(fatal)[0] if fatal else "authentication"
                break
        if result["errors"] and result["status"] == "completed":
            result["status"] = "partial"
        return result

    def _refresh_candidates(self, candidates, refresh_hours, dry_run):
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
