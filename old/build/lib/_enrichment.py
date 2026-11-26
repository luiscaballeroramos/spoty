"""CLI entrypoint for on-demand metadata enrichment."""

import argparse
import json
import os
from typing import Any, Callable

from services.enrichment_contract import METADATA_PROVIDERS
from services.metadata_enrichment import MetadataEnrichmentService


def run_enrichment(
    limit: int = 50,
    service_factory: Callable[..., Any] | None = None,
    *,
    provider: str = "all",
    all_due: bool = False,
    batch_size: int = 50,
    refresh_hours: int = 20,
    max_minutes: int = 300,
    session_file: str | None = None,
    dry_run: bool = False,
    progress=None,
):
    """Run one bounded enrichment batch and return its structured result."""
    from config import DATABASE_URL

    factory = service_factory or MetadataEnrichmentService
    service = factory(
        DATABASE_URL,
        lastfm_api_key=os.getenv("LASTFM_API_KEY"),
        musicbrainz_user_agent=os.getenv(
            "MUSICBRAINZ_USER_AGENT", "Spoty/0.1.0 (metadata enrichment)"
        ),
        session_cookie=os.getenv("SMARTERPLAYLISTS_SESSION_COOKIE"),
        session_file=session_file or os.getenv("SMARTERPLAYLISTS_SESSION_FILE"),
    )
    return service.run(provider, limit=limit, all_due=all_due, batch_size=batch_size,
                       refresh_hours=refresh_hours, max_minutes=max_minutes,
                       dry_run=dry_run, progress=progress)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Enrich metadata through a shared provider procedure.")
    parser.add_argument("--provider", choices=("all", *METADATA_PROVIDERS), default="all",
                        help="Run one source independently (default: all enabled sources).")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Maximum number of tracks to inspect in this run (default: 50).",
    )
    mode.add_argument("--all", dest="all_due", action="store_true", help="Process every eligible entity in batches")
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--refresh-hours", type=int, default=20)
    parser.add_argument("--max-minutes", type=int, default=300)
    parser.add_argument("--session-file", help="Private Smarter Playlists session JSON file")
    parser.add_argument("--dry-run", action="store_true", help="Read pending counts without HTTP or writes")
    args = parser.parse_args(argv)
    if (not 1 <= args.limit <= 100 or not 1 <= args.batch_size <= 100
            or not 1 <= args.refresh_hours <= 168 or not 1 <= args.max_minutes <= 300):
        parser.error("limit/batch-size must be 1..100, refresh-hours 1..168 and max-minutes 1..300")

    try:
        options = {"limit": args.limit}
        if args.provider != "all":
            options["provider"] = args.provider
        if args.all_due or args.dry_run or args.session_file:
            options.update(all_due=args.all_due, dry_run=args.dry_run, session_file=args.session_file,
                           batch_size=args.batch_size, refresh_hours=args.refresh_hours,
                           max_minutes=args.max_minutes)
        else:
            if args.refresh_hours != 20:
                options["refresh_hours"] = args.refresh_hours
            if args.batch_size != 50:
                options["batch_size"] = args.batch_size
            if args.max_minutes != 300:
                options["max_minutes"] = args.max_minutes
        if args.all_due:
            options["progress"] = lambda event: print(json.dumps(event, sort_keys=True), flush=True)
        result = run_enrichment(**options)
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
