"""CLI entrypoint for on-demand metadata enrichment."""

import argparse
import json
import os
from typing import Any, Callable

from services.enrichment import EnrichmentService


def run_enrichment(
    limit: int = 50,
    service_factory: Callable[..., Any] | None = None,
):
    """Run one bounded enrichment batch and return its structured result."""
    from config import DATABASE_URL

    factory = service_factory or EnrichmentService
    service = factory(
        DATABASE_URL,
        lastfm_api_key=os.getenv("LASTFM_API_KEY"),
        musicbrainz_user_agent=os.getenv(
            "MUSICBRAINZ_USER_AGENT", "Spoty/0.1.0 (metadata enrichment)"
        ),
    )
    return service.enrich_pending(limit=limit)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Enrich pending Spotify track metadata.")
    parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Maximum number of tracks to inspect in this run (default: 50).",
    )
    args = parser.parse_args(argv)
    if args.limit < 1:
        parser.error("--limit must be positive")

    try:
        result = run_enrichment(limit=args.limit)
        print(result.to_json())
        raise SystemExit(result.exit_code)
    except SystemExit:
        raise
    except Exception as error:
        if getattr(error, "sqlstate", None) == "42P01":
            print(json.dumps({
                "operation": "metadata_enrichment",
                "status": "deferred",
                "reason": "metadata_migration_required",
            }, sort_keys=True))
            raise SystemExit(0) from error
        print(json.dumps({
            "operation": "metadata_enrichment",
            "status": "failed",
            "error_type": type(error).__name__,
        }, sort_keys=True))
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
