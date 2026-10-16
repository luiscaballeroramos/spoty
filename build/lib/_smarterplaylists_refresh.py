"""Compatibility entrypoint delegating to the unified metadata procedure."""

import sys


def main(argv=None):
    from _enrichment import main as unified_main

    arguments = list(sys.argv[1:] if argv is None else argv)
    if any(value == "--provider" or value.startswith("--provider=") for value in arguments):
        raise SystemExit("This compatibility command always selects smarterplaylists.")
    if "--all" not in arguments and not any(value == "--limit" or value.startswith("--limit=") for value in arguments):
        arguments.extend(["--limit", "10"])
    return unified_main(["--provider", "smarterplaylists", *arguments])


if __name__ == "__main__":
    main()
