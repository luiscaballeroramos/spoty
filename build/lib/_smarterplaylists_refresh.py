"""Daily refresh CLI using a private session cookie, never browser automation in CI."""

import argparse
import json
import os

from metadata_sources.smarterplaylists import SmarterPlaylistsAdapter, load_session_cookie
from services.smarterplaylists_refresh import SmarterPlaylistsRefreshService


def main(argv=None):
    parser = argparse.ArgumentParser(description="Refresh pending Smarter Playlists metadata.")
    parser.add_argument("--limit", type=int, default=10, help="Maximum entities of each type")
    parser.add_argument("--refresh-hours", type=int, default=20, help="Minimum capture age before refresh")
    parser.add_argument("--session-file", help="Private JSON session file (never commit)")
    parser.add_argument("--dry-run", action="store_true", help="Only inspect due entities; no HTTP or writes")
    args = parser.parse_args(argv)
    if not 1 <= args.limit <= 100 or not 1 <= args.refresh_hours <= 168:
        parser.error("limit must be 1..100 and refresh-hours 1..168")
    try:
        from config import require_database_url

        def adapter_factory():
            cookie = load_session_cookie(value=os.getenv("SMARTERPLAYLISTS_SESSION_COOKIE"),
                path=args.session_file or os.getenv("SMARTERPLAYLISTS_SESSION_FILE"))
            return SmarterPlaylistsAdapter(cookie)

        result = SmarterPlaylistsRefreshService(require_database_url(), adapter_factory).refresh(
            args.limit, args.refresh_hours, args.dry_run)
    except Exception as error:
        print(json.dumps({"operation": "smarterplaylists_refresh", "status": "failed",
                          "error_type": type(error).__name__}, sort_keys=True))
        raise SystemExit(1) from None
    print(json.dumps(result, sort_keys=True))
    if result["status"] != "completed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()