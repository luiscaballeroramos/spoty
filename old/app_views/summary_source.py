import os
from pathlib import Path

from spoty.domain.listening import ValidationError, validate_account
from spoty.storage.contracts import validate_commit
from spoty.storage.local_git import (
    LocalGitStore, SYNTHETIC_WRITE_OPT_IN, SyntheticLocalGitStore,
)
from spoty.storage.snapshots import Snapshot


def summary_backend() -> str:
    backend = os.environ.get("SPOTY_SUMMARY_BACKEND", "sql").strip()
    if backend not in ("sql", "csv_local"):
        raise ValidationError("SPOTY_SUMMARY_BACKEND debe ser sql o csv_local.")
    return backend


def load_local_summary_snapshot() -> Snapshot:
    repository = os.environ.get("SPOTY_CSV_REPOSITORY", "").strip()
    account = os.environ.get("SPOTY_CSV_ACCOUNT", "").strip()
    commit = os.environ.get("SPOTY_CSV_COMMIT", "").strip()
    if not repository or not Path(repository).is_absolute():
        raise ValidationError("SPOTY_CSV_REPOSITORY debe indicar una ruta absoluta a un Git bare existente.")
    validate_account(account)
    if commit:
        validate_commit(commit)
    store = LocalGitStore(Path(repository), read_only=True)
    snapshot = Snapshot.load(store, account, commit=commit or None)
    if snapshot.manifest is None:
        raise ValidationError("La cuenta seleccionada no tiene un snapshot consolidado.")
    return snapshot


def load_synthetic_writable_store() -> LocalGitStore:
    """Open an existing, local-only synthetic repository for explicitly opted-in writes."""
    if os.environ.get(SYNTHETIC_WRITE_OPT_IN, "").strip() != "1":
        raise ValidationError(
            f"Las escrituras CSV requieren {SYNTHETIC_WRITE_OPT_IN}=1."
        )

    repository = os.environ.get("SPOTY_CSV_REPOSITORY", "").strip()
    account = os.environ.get("SPOTY_CSV_ACCOUNT", "").strip()
    if not repository or not Path(repository).is_absolute():
        raise ValidationError("SPOTY_CSV_REPOSITORY debe indicar una ruta absoluta a un Git bare existente.")
    validate_account(account)
    return SyntheticLocalGitStore(Path(repository), account)
