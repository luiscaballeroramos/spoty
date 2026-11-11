"""Explicit private-repository preflight and opt-in synthetic publication."""

import argparse
import json
from pathlib import Path

from spoty.cli.credentials import local_github_token
from spoty.domain.listening import ListeningBatch, ValidationError, validate_account
from spoty.domain.metadata import MetadataBatch
from spoty.services.consolidation import consolidate
from spoty.services.favorites import FavoriteQueryService, FavoritesCoordinator
from spoty.services.acquisition import CsvAcquisitionService
from spoty.services.summary_queries import SummaryQueryService
from spoty.storage.contracts import PublicationConflict, StorageError
from spoty.storage.github import GitHubGitStore, GitHubHTTPError, GitHubTransport, RequestsGitHubTransport
from spoty.storage.snapshots import Snapshot


PROTECTION_POLICIES = ("strict", "allow-unprotected-private")


def preflight(
    store: GitHubGitStore, transport: GitHubTransport, *, policy: str = "strict",
) -> dict:
    if policy not in PROTECTION_POLICIES:
        raise ValidationError("Unknown branch protection policy.")
    head = store.head()
    base = f"https://api.github.com/repos/{store.owner}/{store.repository}"
    response = transport("GET", f"{base}/rules/branches/{store.branch}?per_page=100", None)
    if (response.status == 403 and response.error_code == "private_rules_require_plan_upgrade"
            and policy == "allow-unprotected-private"):
        return {
            "repository_private": True, "branch": store.branch, "head": head,
            "protection_policy": policy, "branch_rules_verified": False,
            "branch_protections": {"deletion": None, "non_fast_forward": None},
            "rules_verification": "unavailable_on_current_plan",
            "rules_http_status": 403,
            "residual_risks": ["branch_deletion", "force_push"],
        }
    if response.status != 200:
        raise GitHubHTTPError(response.status)
    if not isinstance(response.body, list) or len(response.body) >= 100:
        raise StorageError("Cannot verify complete active branch rules.")
    protections = {"deletion": False, "non_fast_forward": False}
    details = {}
    for rule in response.body:
        if not isinstance(rule, dict) or not isinstance(rule.get("type"), str):
            raise StorageError("Invalid active branch rule response.")
        kind = rule.get("type")
        if kind not in protections:
            continue
        ruleset_id = rule.get("ruleset_id")
        if type(ruleset_id) is not int or ruleset_id < 1 or rule.get("ruleset_source_type") != "Repository":
            raise StorageError("This demo requires verifiable repository-level branch protections.")
        if ruleset_id not in details:
            detail = transport("GET", f"{base}/rulesets/{ruleset_id}", None)
            if detail.status != 200:
                raise GitHubHTTPError(detail.status)
            details[ruleset_id] = detail.body
        detail = details[ruleset_id]
        if (
            not isinstance(detail, dict) or detail.get("enforcement") not in ("active", "disabled", "evaluate")
            or not isinstance(detail.get("bypass_actors"), list)
        ):
            raise StorageError("Cannot verify branch protection enforcement and bypass actors.")
        protections[kind] |= detail["enforcement"] == "active" and detail["bypass_actors"] == []
    verified = all(protections.values())
    if not verified and policy == "strict":
        raise StorageError("Branch lacks enforced deletion and force-push protections; no data published.")
    return {
        "repository_private": True, "branch": store.branch, "head": head,
        "protection_policy": policy, "branch_rules_verified": verified,
        "rules_verification": "available",
        "branch_protections": protections,
        "residual_risks": [
            risk for kind, risk in (
                ("deletion", "branch_deletion"), ("non_fast_forward", "force_push"),
            ) if not protections[kind]
        ],
    }


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


def verify_synthetic_recovery(store: GitHubGitStore, account: str) -> dict:
    """Continue only this demo's consolidated synthetic fixture; never call Spotify."""
    validate_account(account)
    if not account.startswith("synthetic-"):
        raise ValidationError("Recovery verification requires a synthetic- namespace.")
    before = Snapshot.load(store, account)
    if (before.manifest is None or before.rows("tracks") != [{
        "id": "T" * 22, "name": "Synthetic track", "duration_ms": 180000,
        "album_id": "A" * 22, "album_track": None, "disc_number": None,
        "artists_ids": ["R" * 22], "explicit": None, "popularity": None,
    }] or before.pending_batches() != 0):
        raise StorageError("Recovery verification requires the completed synthetic demo fixture.")
    previous_counts = SummaryQueryService(before).library_counts()

    class SyntheticObservation:
        def get_recently_played(self, limit=50):
            raise StorageError("Synthetic recovery does not acquire listening.")

        def get_liked_songs(self, limit=50, offset=0):
            raise StorageError("Synthetic recovery does not acquire favorite pages.")

        def are_tracks_liked(self, track_ids):
            return {track_id: False for track_id in track_ids}

    acquisition = CsvAcquisitionService(store, account, "github-recovery", spotify=SyntheticObservation())
    acquisition.favorites.begin_change("2" * 32, "T" * 22, False, "2026-11-10T09:00:00Z")
    pending = acquisition.favorites.pending_change()
    if pending is None:
        raise StorageError("Synthetic recovery requires a newly reserved pending change.")
    publication = acquisition.recover_pending_favorite(
        lambda: "2026-11-10T09:01:00Z", remote_settled=True,
    )
    if publication is None:
        raise StorageError("Synthetic favorite recovery did not publish its observation.")
    favorite_result = consolidate(store, account)
    acquisition.publish_enrichment([{
        "provider": "lastfm", "spotify_track_id": "T" * 22, "match_status": "matched",
        "normalized_data": {"name": "Synthetic Last.fm", "zero": 0}, "raw_response": {"synthetic": True},
    }], "2026-11-10T09:02:00Z")
    metadata_result = consolidate(store, account)
    after = Snapshot.load(store, account)
    overview = SummaryQueryService(after).metadata_overview("tracks")
    if (favorite_result.removed_favorites != 1 or metadata_result.saved_metadata != 1
            or FavoriteQueryService(after).liked_ids() != set()
            or SummaryQueryService(after).library_counts() != {**previous_counts, "total_liked_tracks": 0}
            or FavoriteQueryService(before).liked_ids() != {"T" * 22}
            or overview["sources"][2]["available"] != 1
            or after.pending_batches() != 0 or acquisition.favorites.pending_change() is not None):
        raise StorageError("Synthetic recovery verification failed; preserve namespace for inspection.")
    return {
        "account": account, "recovery_commit": after.commit_id,
        "favorite_recovery_observation_only": True, "removed_favorites": favorite_result.removed_favorites,
        "incremental_metadata_saved": metadata_result.saved_metadata,
        "historical_favorite_preserved": True, "pending_batches": 0,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--env-file", type=Path)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--publish-synthetic", action="store_true")
    actions.add_argument("--verify-synthetic-recovery", action="store_true",
                         help="Continue a completed synthetic demo; observes a fake favorite state, not Spotify.")
    parser.add_argument("--account")
    parser.add_argument(
        "--protection-policy", choices=PROTECTION_POLICIES, default="strict",
        help="Explicit acceptance of private-repository deletion/force-push risk; strict is the default.",
    )
    arguments = parser.parse_args(argv)
    synthetic_action = arguments.publish_synthetic or arguments.verify_synthetic_recovery
    if synthetic_action and not arguments.account:
        parser.error("Synthetic operations require an explicit --account")
    try:
        if synthetic_action:
            validate_account(arguments.account)
            if not arguments.account.startswith("synthetic-"):
                raise ValidationError("Synthetic publication requires a synthetic- namespace.")
        transport = RequestsGitHubTransport(lambda: local_github_token(arguments.env_file))
        store = GitHubGitStore(arguments.owner, arguments.repository, transport)
        report = preflight(store, transport, policy=arguments.protection_policy)
        if arguments.publish_synthetic:
            report.update(publish_synthetic(store, arguments.account))
        elif arguments.verify_synthetic_recovery:
            report.update(verify_synthetic_recovery(store, arguments.account))
        print(json.dumps(report, indent=2))
        return 0
    except (StorageError, ValidationError, OSError) as error:
        report = {"status": "failed", "error": "GitHub preflight or synthetic verification failed.",
                  "error_type": type(error).__name__}
        if isinstance(error, GitHubHTTPError):
            report["http_status"] = error.status
        print(json.dumps(report))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
