"""Shared execution result and failure policy for every metadata provider."""

import json
from dataclasses import dataclass, field
from typing import Any


METADATA_PROVIDERS = ("lastfm", "musicbrainz", "acousticbrainz", "smarterplaylists")
RETRYABLE_ERRORS = frozenset({
    "timeout", "connection_error", "transport_error", "rate_limited", "run_timeout",
    "http_429", "http_500", "http_502", "http_503", "http_504",
})


@dataclass(frozen=True)
class EnrichmentReport:
    provider: str
    status: str
    candidates: dict[str, int]
    records_saved: int
    errors: dict[str, dict[str, int]] = field(default_factory=dict)
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def exit_code(self) -> int:
        if self.status not in ("completed", "partial"):
            return 1
        return int(any(count and category not in RETRYABLE_ERRORS
                       for errors in self.errors.values() for category, count in errors.items()))

    @property
    def candidate_tracks(self) -> int:
        return self.candidates.get("track", 0)

    @property
    def provider_errors(self) -> dict[str, int]:
        return {provider: sum(errors.values()) for provider, errors in self.errors.items()}

    def to_dict(self) -> dict[str, Any]:
        return {**self.details, "operation": "metadata_enrichment", "provider": self.provider,
                "status": self.status, "candidates": self.candidates,
                "candidate_tracks": self.candidate_tracks, "records_saved": self.records_saved,
                "errors": self.errors, "provider_error_details": self.errors,
                "provider_errors": self.provider_errors}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)

    @classmethod
    def from_refresh(cls, payload):
        details = {key: value for key, value in payload.items()
                   if key not in {"operation", "status", "candidates", "records_saved", "errors"}}
        status = payload["status"]
        if (payload.get("scope") == "all_due" and not payload.get("dry_run")
                and any(payload.get("remaining", {}).values()) and status != "deferred"):
            status = "incomplete"
        return cls("smarterplaylists", status, payload["candidates"],
                   payload["records_saved"], {"smarterplaylists": payload.get("errors", {})}, details)


def combine_reports(reports, skipped=()):
    candidates = {}
    errors = {}
    for report in reports:
        for entity, count in report.candidates.items():
            candidates[entity] = candidates.get(entity, 0) + count
        for provider, categories in report.errors.items():
            target = errors.setdefault(provider, {})
            for category, count in categories.items():
                target[category] = target.get(category, 0) + count
    status = "completed" if all(report.status == "completed" for report in reports) else "partial"
    if any(report.status in ("failed", "incomplete", "deferred") for report in reports):
        status = "incomplete"
    return EnrichmentReport("all", status, candidates, sum(report.records_saved for report in reports),
        errors, {"provider_results": [report.to_dict() for report in reports],
                 "skipped_providers": list(skipped), "candidate_count_semantics": "provider_entity_pairs"})