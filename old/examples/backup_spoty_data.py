"""Incrementally back up spoty-data to an explicit local bare Git repository."""

import argparse
import json
import sys
from pathlib import Path

from spoty.storage.contracts import StorageError
from spoty.storage.git_backup import backup_data_repository


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--destination", type=Path, required=True,
        help="Bare backup directory outside the application repository, preferably on an encrypted disk.",
    )
    arguments = parser.parse_args(argv)
    try:
        result = backup_data_repository(arguments.destination)
    except StorageError as error:
        print(json.dumps({"status": "failed", "error": str(error)}), file=sys.stderr)
        return 1
    print(json.dumps({
        "status": "backed_up", "backup_path": str(result.backup_path),
        "checkpoint_ref": result.checkpoint_ref,
        "checkpoint_commit": result.checkpoint_commit,
        "remote_commit": result.remote_commit, "advanced": result.advanced,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
