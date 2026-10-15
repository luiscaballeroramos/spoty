"""Daily refresh CLI using a private session cookie, never browser automation in CI."""

import argparse
import json
import os

from metadata_sources.smarterplaylists import SmarterPlaylistsAdapter, load_session_cookie
from services.smarterplaylists_refresh import SmarterPlaylistsRefreshService


def main(argv=None):
    parser = argparse.ArgumentParser(description="Refresh pending Smarter Playlists metadata.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--limit", type=int, help="Maximum entities of each type (default: 10)")
    mode.add_argument("--all", dest="all_due", action="store_true", help="Process all due entities in batches")
    parser.add_argument("--batch-size", type=int, default=50, help="Entities of each type per bulk batch, not a total limit")
    parser.add_argument("--max-minutes", type=int, default=300, help="Bulk time budget, 1..300 minutes")
    parser.add_argument("--refresh-hours", type=int, default=20, help="Minimum capture age before refresh")
    parser.add_argument("--session-file", help="Private JSON session file (never commit)")
    parser.add_argument("--dry-run", action="store_true", help="Only inspect due entities; no HTTP or writes")
    args = parser.parse_args(argv)
    limit = 10 if args.limit is None else args.limit
    if (not 1 <= limit <= 100 or not 1 <= args.batch_size <= 100
            or not 1 <= args.max_minutes <= 300 or not 1 <= args.refresh_hours <= 168):
        parser.error("limit/batch-size must be 1..100, max-minutes 1..300 and refresh-hours 1..168")
    try:
        from config import require_database_url

        def adapter_factory():
            cookie = load_session_cookie(value=os.getenv("SMARTERPLAYLISTS_SESSION_COOKIE"),
                path=args.session_file or os.getenv("SMARTERPLAYLISTS_SESSION_FILE"))
            return SmarterPlaylistsAdapter(cookie)

        service = SmarterPlaylistsRefreshService(require_database_url(), adapter_factory)
        if args.all_due:
            result = service.refresh_all(args.batch_size, args.refresh_hours, args.dry_run,
                max_seconds=args.max_minutes * 60,
                progress=lambda event: print(json.dumps(event, sort_keys=True), flush=True))
        else:
            result = service.refresh(limit, args.refresh_hours, args.dry_run)
    except Exception as error:
        print(json.dumps({"operation": "smarterplaylists_refresh", "status": "failed",
                          "error_type": type(error).__name__}, sort_keys=True))
        raise SystemExit(1) from None
    print(json.dumps(result, sort_keys=True))
    if result["status"] != "completed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
