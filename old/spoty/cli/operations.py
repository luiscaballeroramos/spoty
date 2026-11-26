"""Opt-in operations on an existing synthetic bare repository; never creates one.

Run ``python -m spoty.cli.operations --help`` for the explicit local commands.
Status and audit are read-only. Consolidation requires --write-synthetic and
never contacts Spotify or GitHub, reads credentials, or performs a backup.
"""

import argparse
from dataclasses import asdict
import json
from pathlib import Path

from spoty.domain.favorites import FavoritesBatch
from spoty.domain.listening import ValidationError, validate_account
from spoty.services.consolidation import consolidate
from spoty.services.favorites import FavoritesCoordinator, validate_pending_revisions, verify_revision
from spoty.storage.contracts import StorageError, batch_path
from spoty.storage.csv_codec import SCHEMAS, decode_batch
from spoty.storage.local_git import LocalGitStore
from spoty.storage.snapshots import Snapshot


def _synthetic_account(account: str) -> None:
    validate_account(account)
    if not account.startswith("synthetic-"):
        raise ValidationError("Operations require a synthetic- account namespace.")


def local_operation(
    operation: str, repository: Path, account: str, *,
    write_synthetic: bool = False, batch_limit: int = 100,
) -> dict:
    if operation not in {"status", "audit", "consolidate"}:
        raise ValidationError("Unknown local operation.")
    _synthetic_account(account)
    if type(batch_limit) is not int or not 1 <= batch_limit <= 100:
        raise ValidationError("Batch limit must be between 1 and 100.")
    if operation == "consolidate" and not write_synthetic:
        raise ValidationError("Consolidation requires explicit --write-synthetic.")
    if not repository.is_dir():
        raise StorageError("An existing synthetic bare repository is required.")
    store = LocalGitStore(repository, read_only=True)
    head = store.head()
    paths = store.list_paths(head)
    for path in paths:
        parts = path.split("/")
        if len(parts) < 3 or parts[0] != "accounts":
            raise StorageError("Only a synthetic account data repository is supported.")
        _synthetic_account(parts[1])
    snapshot = Snapshot.load(store, account, head)
    prefix = f"accounts/{account}/"
    account_paths = [path for path in paths if path.startswith(prefix)]
    if not account_paths:
        raise StorageError("The synthetic account must already exist.")
    favorite_pending = FavoritesCoordinator(store, account, "operations").pending_change() is not None
    report = {
        "operation": operation, "account": account, "head": head,
        "read_only": operation != "consolidate",
        "manifest_present": snapshot.manifest is not None,
        "pending_batches": snapshot.pending_batches(),
        "archived_batches": sum(path.startswith(prefix + "archive/") for path in account_paths),
        "quarantined_batches": sum(path.startswith(prefix + "quarantine/") for path in account_paths),
        "favorite_change_pending": favorite_pending,
    }
    if operation == "audit":
        counts = {dataset: sum(1 for _ in snapshot.iter_rows(dataset)) for dataset in SCHEMAS}
        validate_pending_revisions(
            store, head, account, snapshot.rows("favorites_replica_state"), set(paths),
        )
        checked = 0
        for path in account_paths:
            if not any(path.startswith(prefix + folder + "/") for folder in ("inbox", "archive")):
                continue
            data = store.read(head, path)
            batch = decode_batch(data)
            expected = batch_path(batch)
            if path.startswith(prefix + "archive/"):
                expected = expected.replace("/inbox/", "/archive/", 1)
            if batch.account_key != account or path != expected:
                raise ValidationError("Capture identity does not match its stored path.")
            if isinstance(batch, FavoritesBatch):
                verify_revision(store, head, batch, data)
            checked += 1
        report.update({
            "dataset_rows": counts, "verified_captures": checked,
            "audit_scope": "snapshot_partitions_inbox_archive_and_favorite_revisions",
            "quarantine_contents_verified": False,
        })
    elif operation == "consolidate":
        writable = LocalGitStore(repository)
        if writable.head() != head:
            raise StorageError("Repository changed during operational checks; retry.")
        result = consolidate(writable, account, batch_limit=batch_limit)
        report.update({
            "consolidation": asdict(result), "head": result.commit_id,
            "pending_batches": Snapshot.load(writable, account, result.commit_id).pending_batches(),
        })
    if operation != "consolidate" and store.head() != head:
        raise StorageError("Repository changed during operational checks; retry.")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="operation", required=True)
    for operation in ("status", "audit", "consolidate"):
        command = commands.add_parser(operation)
        command.add_argument("--repository", required=True, type=Path)
        command.add_argument("--account", required=True)
        if operation == "consolidate":
            command.add_argument("--write-synthetic", action="store_true")
            command.add_argument("--batch-limit", type=int, default=100)
    arguments = parser.parse_args(argv)
    try:
        report = local_operation(
            arguments.operation, arguments.repository, arguments.account,
            write_synthetic=getattr(arguments, "write_synthetic", False),
            batch_limit=getattr(arguments, "batch_limit", 100),
        )
    except (StorageError, ValidationError, OSError, UnicodeError):
        print(json.dumps({"status": "failed", "error": "Local synthetic operation failed; no provider details exposed."}))
        return 1
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
