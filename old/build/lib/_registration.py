from services.synchronization import SyncResult, SynchronizationService


def register_listeningevents() -> SyncResult:
    """Compatibility entrypoint that delegates registration to the shared service."""
    return SynchronizationService().sync_listening_events()


def main() -> None:
    result = register_listeningevents()
    print(result.to_json())
    raise SystemExit(result.exit_code)


if __name__ == "__main__":
    main()
