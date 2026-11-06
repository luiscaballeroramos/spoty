"""Explicit private-repository preflight and opt-in synthetic publication."""

import argparse
import json
from pathlib import Path

from spoty.cli.credentials import local_github_token
from spoty.domain.listening import ListeningBatch, ValidationError, validate_account
from spoty.domain.metadata import MetadataBatch
from spoty.services.consolidation import consolidate
from spoty.services.favorites import FavoriteQueryService, FavoritesCoordinator
from spoty.storage.contracts import PublicationConflict, StorageError
from spoty.storage.github import GitHubGitStore, GitHubHTTPError, RequestsGitHubTransport
from spoty.storage.snapshots import Snapshot


def preflight(store: GitHubGitStore, transport: RequestsGitHubTransport) -> dict:
    head = store.head()
    base = f"https://api.github.com/repos/{store.owner}/{store.repository}"
    response = transport("GET", f"{base}/rules/branches/{store.branch}?per_page=100", None)
    if response.status != 200:
        raise GitHubHTTPError(response.status)
    if not isinstance(response.body, list) or len(response.body) >= 100:
        raise StorageError("Cannot verify complete active branch rules.")
    protected = set()
    for rule in response.body:
        if not isinstance(rule, dict):
            raise StorageError("Invalid active branch rule response.")
        kind = rule.get("type")
        if kind not in ("deletion", "non_fast_forward"):
            continue
        ruleset_id = rule.get("ruleset_id")
        if type(ruleset_id) is not int or rule.get("ruleset_source_type") != "Repository":
            raise StorageError("This demo requires verifiable repository-level branch protections.")
        detail = transport("GET", f"{base}/rulesets/{ruleset_id}", None)
        if detail.status != 200:
            raise GitHubHTTPError(detail.status)
        if (
            not isinstance(detail.body, dict) or detail.body.get("enforcement") != "active"
            or detail.body.get("bypass_actors") != []
        ):
            raise StorageError("Active branch protection with an empty bypass list is required.")
        protected.add(kind)
    if protected != {"deletion", "non_fast_forward"}:
        raise StorageError("Main lacks enforced deletion and force-push protections; no data published.")
    return {"repository_private": True, "branch": store.branch, "head": head, "branch_rules_verified": True}


def publish_synthetic(store: GitHubGitStore, account: str) -> dict:
    validate_account(account)
    if not account.startswith("synthetic-"):
        raise ValidationError("The remote demo requires a synthetic- account namespace.")
    before = Snapshot.load(store, account)
    if store.list_paths(before.commit_id, f"accounts/{account}"):
        raise StorageError("Use a new synthetic namespace; existing account data will not be changed.")
    artist = {"id": "R" * 22, "name": "Synthetic artist"}
    batch = ListeningBatch.from_items(account, "github-demo", "2026-11-06T10:00:00Z", [{
        "played_at": "2026-11-06T10:00:00Z", "context": None,
        "track": {
            "id": "T" * 22, "name": "Synthetic track", "duration_ms": 180000, "artists": [artist],
            "album": {"id": "A" * 22, "name": "Synthetic album", "artists": [artist], "total_tracks": 1},
        },
    }])
    publication = store.publish_batch(batch)
    retry = store.publish_batch(batch)
    values = {"id": "T" * 22, "uri": "spotify:track:" + "T" * 22, "name": "Synthetic track", "energy": 0}
    metadata = MetadataBatch.from_smarterplaylists(account, "github-demo", "2026-11-06T10:00:00Z", [{
        "captured_at": "2026-11-06T10:00:00Z", "entities": {"track": {
            "values": values, "conflicts": {}, "missing_requested": [],
            "raw": {"status": "complete", "playlist_uri": None, "tracks": [values]},
        }},
    }], live_track=True)
    store.publish_batch(metadata)
    favorites = FavoritesCoordinator(store, account, "github-demo")
    favorites.begin_change("1" * 32, "T" * 22, True, "2026-11-06T10:00:00Z")
    favorites.resolve_change(
        "1" * 32, True, "2026-11-06T10:00:00Z",
        resolution="confirmed", remote_settled=True,
    )
    # This is an observed synthetic fixture; no Spotify action is executed.
    result = consolidate(store, account)
    after = Snapshot.load(store, account)
    archived_retry = store.publish_batch(batch)
    checks = (
        result.saved_events == result.saved_metadata == result.saved_favorites == 1,
        retry.status == archived_retry.status == "already_saved",
        after.pending_batches() == 0,
        after.rows("track_audio")[0]["audio"] == {"energy": 0},
        FavoriteQueryService(after).liked_ids() == {"T" * 22},
        before.rows("listening_events") == [],
    )
    if not all(checks):
        raise StorageError("Synthetic remote verification failed; preserve namespace for inspection.")
    try:
        store.publish(publication.commit_id, {}, "Synthetic stale-head check")
    except PublicationConflict:
        stale_rejected = True
    else:
        raise StorageError("A stale remote head was unexpectedly accepted.")
    return {
        "account": account, "snapshot_commit": after.commit_id,
        "saved_events": result.saved_events, "saved_metadata": result.saved_metadata,
        "saved_favorites": result.saved_favorites, "pending_batches": after.pending_batches(),
        "idempotent_retries": True, "pinned_reads": True, "stale_head_rejected": stale_rejected,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--publish-synthetic", action="store_true")
    parser.add_argument("--account")
    arguments = parser.parse_args()
    if arguments.publish_synthetic and not arguments.account:
        parser.error("--publish-synthetic requires an explicit new --account")
    try:
        transport = RequestsGitHubTransport(lambda: local_github_token(arguments.env_file))
        store = GitHubGitStore(arguments.owner, arguments.repository, transport)
        report = preflight(store, transport)
        if arguments.publish_synthetic:
            report.update(publish_synthetic(store, arguments.account))
        print(json.dumps(report, indent=2))
        return 0
    except (StorageError, ValidationError) as error:
        print(json.dumps({"status": "failed", "error": str(error)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
