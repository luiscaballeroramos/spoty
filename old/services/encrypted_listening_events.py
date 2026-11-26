import argparse
import csv
import io
import os
import secrets
import tempfile
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


KEY_ENVIRONMENT_VARIABLE = "SPOTY_LISTENING_EVENTS_KEY"
_FILE_MAGIC = b"SPOTY-LEC\x01"
_NONCE_SIZE = 12
_KEY_SIZE = 32


def encrypt_file(source: Path, destination: Path, key: bytes) -> bool:
    if len(key) != _KEY_SIZE:
        raise ValueError("The listening-events encryption key must be 32 bytes.")
    if not source.exists():
        return False

    plaintext = source.read_bytes()
    if destination.exists() and decrypt_bytes(destination.read_bytes(), key) == plaintext:
        return False

    nonce = secrets.token_bytes(_NONCE_SIZE)
    encrypted = _FILE_MAGIC + nonce + AESGCM(key).encrypt(
        nonce, plaintext, _FILE_MAGIC
    )
    _write_atomically(destination, encrypted)
    return True


def decrypt_file(source: Path, destination: Path, key: bytes) -> bool:
    if len(key) != _KEY_SIZE:
        raise ValueError("The listening-events encryption key must be 32 bytes.")
    if not source.exists():
        return False

    plaintext = decrypt_bytes(source.read_bytes(), key)
    _write_atomically(destination, plaintext)
    return True


def merge_file(source: Path, destination: Path, key: bytes) -> int:
    """Append events from an encrypted CSV missing in the destination CSV."""
    if len(key) != _KEY_SIZE:
        raise ValueError("The listening-events encryption key must be 32 bytes.")
    if not source.exists():
        return 0

    text = decrypt_bytes(source.read_bytes(), key).decode("utf-8")
    remote_reader = csv.DictReader(io.StringIO(text, newline=""))
    fields = tuple(remote_reader.fieldnames or ())
    remote_rows = list(remote_reader)

    local_keys: set[tuple[str, str]] = set()
    has_local_content = destination.exists() and destination.stat().st_size > 0
    if has_local_content:
        with destination.open("r", encoding="utf-8", newline="") as csv_file:
            local_reader = csv.DictReader(csv_file)
            if tuple(local_reader.fieldnames or ()) != fields:
                raise ValueError(f"The CSV header does not match: {destination}")
            local_keys = {
                (row["track_id"], row["played_at"]) for row in local_reader
            }

    new_rows = [
        row for row in remote_rows
        if (row["track_id"], row["played_at"]) not in local_keys
    ]
    if not new_rows:
        return 0

    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("a", encoding="utf-8", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fields)
        if not has_local_content:
            writer.writeheader()
        writer.writerows(new_rows)
    return len(new_rows)


def decrypt_bytes(encrypted: bytes, key: bytes) -> bytes:
    minimum_size = len(_FILE_MAGIC) + _NONCE_SIZE + 16
    if not encrypted.startswith(_FILE_MAGIC) or len(encrypted) < minimum_size:
        raise ValueError("The encrypted listening-events file is invalid.")

    nonce_start = len(_FILE_MAGIC)
    nonce = encrypted[nonce_start:nonce_start + _NONCE_SIZE]
    ciphertext = encrypted[nonce_start + _NONCE_SIZE:]
    try:
        return AESGCM(key).decrypt(nonce, ciphertext, _FILE_MAGIC)
    except InvalidTag as exc:
        raise ValueError(
            "Could not authenticate the encrypted listening-events file; "
            "check the configured key or file integrity."
        ) from exc


def _write_atomically(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            temporary_file.write(content)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def _key_from_environment() -> bytes:
    value = os.getenv(KEY_ENVIRONMENT_VARIABLE, "").strip()
    if not value:
        raise ValueError(
            f"Set the {KEY_ENVIRONMENT_VARIABLE} environment variable."
        )
    try:
        key = bytes.fromhex(value)
    except ValueError as exc:
        raise ValueError(
            f"{KEY_ENVIRONMENT_VARIABLE} must be 64 hexadecimal characters."
        ) from exc
    if len(key) != _KEY_SIZE:
        raise ValueError(
            f"{KEY_ENVIRONMENT_VARIABLE} must be 64 hexadecimal characters."
        )
    return key


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("encrypt", "decrypt", "merge"))
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()

    key = _key_from_environment()
    if args.operation == "merge":
        print(f"Added {merge_file(args.source, args.destination, key)} new events.")
        return
    if args.operation == "encrypt":
        changed = encrypt_file(args.source, args.destination, key)
    else:
        changed = decrypt_file(args.source, args.destination, key)
    print("Listening-events file updated." if changed else "No file change needed.")


if __name__ == "__main__":
    main()
