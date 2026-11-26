import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from spoty.storage.contracts import StorageError, validate_commit


SOURCE_REPOSITORY = "https://github.com/luiscaballeroramos/spoty-data.git"
SOURCE_BRANCH = "main"


@dataclass(frozen=True)
class BackupResult:
    backup_path: Path
    checkpoint_ref: str
    checkpoint_commit: str
    remote_commit: str
    advanced: bool


def _git(arguments: list[str], *, capture: bool = True) -> str:
    environment = os.environ.copy()
    environment["GIT_TERMINAL_PROMPT"] = "0"
    try:
        result = subprocess.run(
            ["git", *arguments], capture_output=capture, text=True, env=environment,
        )
    except OSError:
        raise StorageError("Could not run Git for the offline backup.") from None
    if result.returncode:
        raise StorageError("Git backup operation failed; the prior checkpoint was preserved.")
    return result.stdout.strip() if capture else ""


def _git_dir(path: Path, arguments: list[str], *, capture: bool = True) -> str:
    return _git(["--git-dir", str(path), *arguments], capture=capture)


def _remote_head(path: Path) -> str:
    result = _git_dir(path, ["ls-remote", "--exit-code", "origin", f"refs/heads/{SOURCE_BRANCH}"])
    lines = result.splitlines()
    if len(lines) != 1:
        raise StorageError("The source repository main branch could not be verified.")
    columns = lines[0].split()
    if len(columns) != 2 or columns[1] != f"refs/heads/{SOURCE_BRANCH}":
        raise StorageError("The source repository main branch response is invalid.")
    validate_commit(columns[0])
    return columns[0]


def _local_head(path: Path) -> str:
    value = _git_dir(path, ["rev-parse", "--verify", f"refs/heads/{SOURCE_BRANCH}"])
    validate_commit(value)
    return value


def _validate_destination(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_absolute() or resolved == Path(resolved.anchor):
        raise StorageError("Choose a specific backup directory, not a filesystem root.")
    repository_root = Path(__file__).resolve().parents[2]
    if resolved == repository_root:
        raise StorageError("The data backup must be outside the application repository.")
    try:
        resolved.relative_to(repository_root)
        raise StorageError("The data backup must be outside the application repository.")
    except ValueError:
        pass
    try:
        repository_root.relative_to(resolved)
        raise StorageError("The data backup directory cannot contain the application repository.")
    except ValueError:
        pass
    return resolved


def _ensure_mirror(path: Path, source: str) -> None:
    if path.exists():
        if not path.is_dir():
            raise StorageError("The backup destination exists and is not a directory.")
        is_bare = _git_dir(path, ["rev-parse", "--is-bare-repository"])
        if is_bare != "true":
            raise StorageError("An existing backup destination must be a bare Git repository.")
        configured = _git_dir(path, ["remote", "get-url", "origin"])
        if configured != source:
            raise StorageError("The existing backup repository has a different source.")
    else:
        if not path.parent.is_dir():
            raise StorageError("Create the explicit backup parent directory first.")
        _git(["clone", "--mirror", "--", source, str(path)], capture=False)
        if _git_dir(path, ["rev-parse", "--is-bare-repository"]) != "true":
            raise StorageError("Git did not create a bare backup repository.")
        configured = _git_dir(path, ["remote", "get-url", "origin"])
        if configured != source:
            raise StorageError("The backup repository source identity did not verify.")

    # Prevent an accidental push from the mirror; backup runs only fetch.
    _git_dir(path, ["config", "--local", "remote.origin.mirror", "false"])
    _git_dir(path, ["config", "--local", "push.default", "nothing"])
    _git_dir(path, ["config", "--local", "remote.origin.prune", "false"])


def backup_data_repository(
    destination: Path, *, source: str = SOURCE_REPOSITORY,
) -> BackupResult:
    """Fetch the private data repo and preserve its previous main tip locally."""
    if not isinstance(source, str) or not re.fullmatch(
        r"https://github\.com/[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_.-]+\.git", source,
    ):
        # Local bare remotes are permitted for synthetic tests only.
        local_source = Path(source)
        if not local_source.is_absolute() or not local_source.is_dir():
            raise StorageError("Backup source must be a credential-free GitHub HTTPS or local Git URL.")
        _git(["--git-dir", str(local_source), "rev-parse", "--is-bare-repository"])
    path = _validate_destination(destination)
    _ensure_mirror(path, source)
    remote_before = _remote_head(path)
    local_before = _local_head(path)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    checkpoint_ref = f"refs/backup/checkpoints/{timestamp}-{uuid4().hex}"
    _git_dir(path, ["update-ref", checkpoint_ref, local_before, "0" * 40])
    _git_dir(path, ["fetch", "--no-prune", "origin"], capture=False)
    remote_after = _remote_head(path)
    local_after = _local_head(path)
    if remote_after != local_after:
        raise StorageError(
            f"Remote branch changed during backup; checkpoint {checkpoint_ref} was retained.",
        )
    if remote_before != local_before and remote_after == local_before:
        raise StorageError(
            f"Remote branch did not advance in the backup; checkpoint {checkpoint_ref} was retained.",
        )
    return BackupResult(
        backup_path=path, checkpoint_ref=checkpoint_ref, checkpoint_commit=local_before,
        remote_commit=remote_after, advanced=remote_after != local_before,
    )
