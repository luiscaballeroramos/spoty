from services.synchronization import SyncResult, SynchronizationService


def save_liked_track_ids(limit: int = 50, offset: int = 0) -> SyncResult:
    """Compatibility entrypoint that delegates favorite reconciliation to the shared service."""
    return SynchronizationService().sync_liked_tracks(limit=limit, offset=offset)


def main() -> None:
    result = save_liked_track_ids()
    print(result.to_json())
    raise SystemExit(result.exit_code)


if __name__ == "__main__":
    main()
