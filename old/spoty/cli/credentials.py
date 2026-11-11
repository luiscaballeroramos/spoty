import os
from pathlib import Path

from spoty.storage.contracts import StorageError


def local_github_token(env_file: Path | None = None) -> str:
    """Read only GitHub token settings; never populate the process environment."""
    names = ("GH_TOKEN", "GITHUB_TOKEN")
    for name in names:
        if name in os.environ:
            return _validate(os.environ[name])
    if env_file is None:
        raise StorageError("Configure GH_TOKEN or supply an explicit local credentials file.")
    values = {}
    try:
        with env_file.open(encoding="utf-8") as source:
            for raw_line in source:
                line = raw_line.strip()
                if line.startswith("export "):
                    line = line[7:].strip()
                key, separator, value = line.partition("=")
                key = key.strip()
                if not separator or key not in names:
                    continue
                if key in values:
                    raise StorageError("Duplicate GitHub credential setting in local file.")
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                    value = value[1:-1]
                values[key] = value
    except (OSError, UnicodeError):
        raise StorageError("Could not load the explicit local credentials file.") from None
    for name in names:
        if name in values:
            return _validate(values[name])
    raise StorageError("The local file has no GH_TOKEN or GITHUB_TOKEN setting.")


def _validate(value: str) -> str:
    if not value or any(char.isspace() for char in value):
        raise StorageError("GitHub credential setting is empty or invalid.")
    return value
