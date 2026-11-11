"""Validate captures offline; database import requires an explicit --apply."""

import argparse
import json

from services.smarterplaylists import import_capture


def main(argv=None):
    parser = argparse.ArgumentParser(description="Validate or import a Smarter Playlists capture.")
    parser.add_argument("capture", help="Path to report.json, including original responses")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Validate without a database (default)")
    mode.add_argument("--apply", action="store_true", help="Import existing entities into the migrated database")
    args = parser.parse_args(argv)
    try:
        database_url = None
        if args.apply:
            from config import require_database_url

            database_url = require_database_url()
        result = import_capture(args.capture, dry_run=not args.apply, database_url=database_url)
    except Exception as error:
        reason = "metadata_migration_required" if getattr(error, "sqlstate", None) == "42P01" else "import_failed"
        print(json.dumps({"operation": "smarterplaylists_import", "status": "failed",
                          "reason": reason, "error_type": type(error).__name__}, sort_keys=True))
        raise SystemExit(1) from None
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
