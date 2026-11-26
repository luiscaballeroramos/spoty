"""Read-only inventory of accounts and stored data in a private GitHub data repo."""

import argparse
import json
from pathlib import Path
from typing import Any

from spoty.cli.credentials import local_github_token
from spoty.domain.listening import ValidationError, validate_account
from spoty.storage.contracts import StorageError
from spoty.storage.github import (
    GitHubGitStore,
    GitHubHTTPError,
    RequestsGitHubTransport,
)
from spoty.storage.snapshots import Snapshot


def inspect_repository(
    store: GitHubGitStore, account_filter: str | None = None,
) -> dict[str, Any]:
    if account_filter is not None:
        validate_account(account_filter)

    head = store.head()
    paths = store.list_paths(head)
    accounts = set()
    for path in paths:
        parts = path.split("/")
        if parts[0] == "accounts":
            if len(parts) < 3:
                raise StorageError("Invalid account data path in repository.")
            validate_account(parts[1])
            accounts.add(parts[1])

    if account_filter is not None:
        accounts.intersection_update({account_filter})

    account_reports = []
    for account in sorted(accounts):
        prefix = f"accounts/{account}/"
        account_paths = [path for path in paths if path.startswith(prefix)]
        snapshot = Snapshot.load(store, account, head)
        datasets = {}
        if snapshot.manifest is not None:
            for dataset in sorted(snapshot.manifest["datasets"]):
                descriptor = snapshot.dataset_descriptor(dataset)
                partitions = descriptor["partitions"]
                datasets[dataset] = {
                    "rows": sum(part["row_count"] for part in partitions),
                    "partitions": len(partitions),
                    "bytes": sum(part["byte_size"] for part in partitions),
                }

        inbox_prefix = prefix + "inbox/"
        pending_by_kind = {
            kind: sum(
                path.startswith(inbox_prefix + kind + "/")
                for path in account_paths
            )
            for kind in (
                "listening_capture",
                "metadata_capture",
                "favorites_capture",
            )
        }
        pending_batches = snapshot.pending_batches()
        account_reports.append({
            "account": account,
            "manifest_present": snapshot.manifest is not None,
            "snapshot_id": (
                snapshot.manifest["snapshot_id"]
                if snapshot.manifest is not None else None
            ),
            "published_at": (
                snapshot.manifest["published_at"]
                if snapshot.manifest is not None else None
            ),
            "datasets": datasets,
            "pending_batches": pending_batches,
            "pending_by_kind": pending_by_kind,
            "unclassified_pending_batches": (
                pending_batches - sum(pending_by_kind.values())
            ),
            "archived_batches": sum(
                "/archive/" in path for path in account_paths
            ),
            "quarantined_batches": sum(
                "/quarantine/" in path for path in account_paths
            ),
        })

    return {
        "status": "ok",
        "repository": f"{store.owner}/{store.repository}",
        "branch": store.branch,
        "head_commit": head,
        "accounts_count": len(account_reports),
        "file_count": len(paths),
        "top_level_entries": sorted({path.split("/", 1)[0] for path in paths}),
        "partition_contents_verified": False,
        "inventory_scope": (
            "Git paths and manifest row counts; dataset CSV contents were not downloaded."
        ),
        "accounts": account_reports,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner", default="luiscaballeroramos")
    parser.add_argument("--repository", default="spoty-data")
    parser.add_argument("--branch", default="main")
    parser.add_argument("--account", help="Limit the inventory to one account key.")
    parser.add_argument(
        "--env-file",
        type=Path,
        help="Optional explicit file containing GH_TOKEN or GITHUB_TOKEN.",
    )
    arguments = parser.parse_args(argv)
    try:
        transport = RequestsGitHubTransport(
            lambda: local_github_token(arguments.env_file)
        )
        store = GitHubGitStore(
            arguments.owner,
            arguments.repository,
            transport,
            branch=arguments.branch,
        )
        report = inspect_repository(store, arguments.account)
    except (StorageError, ValidationError, OSError) as error:
        report = {
            "status": "failed",
            "error": "Could not inspect the private data repository.",
            "error_type": type(error).__name__,
        }
        if isinstance(error, GitHubHTTPError):
            report["http_status"] = error.status
        print(json.dumps(report, indent=2))
        return 1

    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
