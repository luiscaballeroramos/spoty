"""One public enrichment procedure with source-owned adapters and persistence."""

import time
from dataclasses import replace

import requests

from services.enrichment import EnrichmentService, _error_category
from services.enrichment_contract import EnrichmentReport, METADATA_PROVIDERS, combine_reports


class MetadataEnrichmentService:
    def __init__(self, database_url, *, lastfm_api_key=None,
                 musicbrainz_user_agent="Spoty/0.1.0 (metadata enrichment)",
                 session_cookie=None, session_file=None, legacy_service=None,
                 smarter_service=None, clock=time.monotonic):
        self._database_url = database_url
        self._lastfm_api_key = lastfm_api_key
        self._session_cookie = session_cookie
        self._session_file = session_file
        self._legacy = legacy_service or EnrichmentService(database_url,
            lastfm_api_key=lastfm_api_key, musicbrainz_user_agent=musicbrainz_user_agent)
        self._smarter_service = smarter_service
        self._clock = clock

    def _smarter(self):
        if self._smarter_service is None:
            from metadata_sources.smarterplaylists import SmarterPlaylistsAdapter, load_session_cookie
            from services.smarterplaylists_refresh import SmarterPlaylistsRefreshService

            def adapter_factory():
                cookie = load_session_cookie(value=self._session_cookie, path=self._session_file)
                return SmarterPlaylistsAdapter(cookie)

            self._smarter_service = SmarterPlaylistsRefreshService(self._database_url, adapter_factory)
        return self._smarter_service

    def run(self, provider="all", *, limit=50, all_due=False, batch_size=50,
            refresh_hours=20, dry_run=False, max_minutes=300, progress=None):
        if provider not in ("all", *METADATA_PROVIDERS):
            raise ValueError("Unknown metadata provider.")
        if (not 1 <= limit <= 100 or not 1 <= batch_size <= 100
                or not 1 <= refresh_hours <= 168 or not 1 <= max_minutes <= 300):
            raise ValueError("Invalid enrichment batch or execution budget.")
        configured = {"lastfm": bool(self._lastfm_api_key), "musicbrainz": True,
                      "acousticbrainz": True,
                      "smarterplaylists": bool(self._session_cookie or self._session_file or self._smarter_service)}
        selected = [name for name in METADATA_PROVIDERS if configured[name]] if provider == "all" else [provider]
        skipped = [name for name in METADATA_PROVIDERS if name not in selected] if provider == "all" else []
        reports = []
        deadline = self._clock() + max_minutes * 60
        for name in selected:
            if all_due and self._clock() >= deadline:
                reports.append(EnrichmentReport(name, "incomplete", {}, 0,
                    details={"stop_reason": "time_budget", "not_started": True}))
                continue
            try:
                report = self._run_provider(name, limit, all_due, batch_size, refresh_hours,
                                            dry_run, deadline, progress)
            except Exception as error:
                if getattr(error, "sqlstate", None) == "42P01":
                    report = EnrichmentReport(name, "deferred", {}, 0,
                        {name: {"metadata_migration_required": 1}})
                else:
                    category = _error_category(error) if isinstance(error, requests.RequestException) else type(error).__name__
                    report = EnrichmentReport(name, "failed", {}, 0, {name: {category: 1}})
            reports.append(report)
        result = combine_reports(reports, skipped) if provider == "all" else reports[0]
        return replace(result, details={**result.details, "dry_run": dry_run,
            "scope": "all_due" if all_due else "batch",
            "batch_size": batch_size if all_due else limit})

    def _run_provider(self, provider, limit, all_due, batch_size, refresh_hours,
                      dry_run, deadline, progress):
        if provider == "smarterplaylists":
            service = self._smarter()
            if all_due:
                payload = service.refresh_all(batch_size, refresh_hours, dry_run,
                    max_seconds=max(0.001, deadline - self._clock()), clock=self._clock,
                    progress=self._progress(provider, progress))
            else:
                payload = service.refresh(limit, refresh_hours, dry_run)
            return EnrichmentReport.from_refresh(payload)
        if not all_due and not dry_run:
            return self._legacy.enrich_pending(limit=limit, provider=provider).to_report()
        candidates = self._legacy.get_candidates(None if all_due else limit, provider=provider)
        total = len(candidates)
        if dry_run:
            return EnrichmentReport(provider, "completed", {"track": total}, 0,
                details={"dry_run": True, "scope": "all_due" if all_due else "batch"})
        reports = []
        attempted = 0
        stop_reason = None
        for offset in range(0, total, batch_size):
            if self._clock() >= deadline:
                stop_reason = "time_budget"
                break
            rows = candidates[offset:offset + batch_size]
            report = self._legacy.enrich_candidates(rows, provider=provider).to_report()
            reports.append(report)
            attempted += len(rows)
            if progress:
                progress({"operation": "metadata_enrichment_progress", "provider": provider,
                          "batch": len(reports), "attempted": {"track": attempted},
                          "remaining": {"track": total - attempted}, "batch_status": report.status})
            fatal = {"http_401", "http_403", "http_429", "api_10", "api_26", "api_29"} & report.errors.get(provider, {}).keys()
            if fatal:
                stop_reason = sorted(fatal)[0]
                break
        aggregate = combine_reports(reports)
        details = {"scope": "all_due", "batches": len(reports), "dry_run": False,
                   "attempted": {"track": attempted}, "remaining": {"track": total - attempted},
                   "matched": {}, "not_found": {}, "ambiguous_musicbrainz": 0}
        for report in reports:
            for key in ("matched", "not_found"):
                for name, count in report.details.get(key, {}).items():
                    details[key][name] = details[key].get(name, 0) + count
            details["ambiguous_musicbrainz"] += report.details.get("ambiguous_musicbrainz", 0)
        if stop_reason:
            details["stop_reason"] = stop_reason
        status = "incomplete" if attempted < total else aggregate.status
        return replace(aggregate, provider=provider, status=status, candidates={"track": total}, details=details)

    @staticmethod
    def _progress(provider, callback):
        if callback is None:
            return None
        def emit(event):
            callback({**event, "operation": "metadata_enrichment_progress", "provider": provider})
        return emit
