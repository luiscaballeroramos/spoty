import hashlib
import sqlite3
from collections.abc import Iterator, MutableMapping
from pathlib import Path
from typing import Any

from spoty.domain.listening import ValidationError, canonical_json
from spoty.storage.contracts import validate_path
from spoty.storage.csv_codec import MAX_CSV_BYTES, SCHEMAS, decode_csv, encode_csv


class FileChanges(MutableMapping[str, bytes | None]):
    """Temporary publication content; stores read one value at a time."""

    def __init__(self, directory: Path):
        self.directory = directory
        self.directory.mkdir()
        self.files: dict[str, Path | None] = {}
        self.counter = 0

    def __getitem__(self, key: str) -> bytes | None:
        path = self.files[key]
        return path.read_bytes() if path is not None else None

    def __setitem__(self, key: str, value: bytes | None) -> None:
        validate_path(key)
        if value is not None and not isinstance(value, bytes):
            raise ValidationError("Published content must be bytes.")
        previous = self.files.get(key)
        if value is None:
            self.files[key] = None
        else:
            path = self.directory / str(self.counter)
            self.counter += 1
            path.write_bytes(value)
            self.files[key] = path
        if previous is not None:
            previous.unlink()

    def __delitem__(self, key: str) -> None:
        path = self.files.pop(key)
        if path is not None:
            path.unlink()

    def __iter__(self) -> Iterator[str]:
        return iter(self.files)

    def __len__(self) -> int:
        return len(self.files)


class DiskRows:
    """Validated CSV rows indexed on temporary disk, never a persistent cache."""

    def __init__(self, database: sqlite3.Connection, dataset: str):
        self.database = database
        self.dataset = dataset
        self.schema = SCHEMAS[dataset]

    def put(self, row: dict[str, Any], *, replace: bool = False) -> None:
        key = canonical_json(tuple(row[field] for field in self.schema.keys))
        bucket = hashlib.sha256(key.encode("utf-8")).digest()[0] % 16
        command = "INSERT OR REPLACE" if replace else "INSERT"
        try:
            self.database.execute(
                f"{command} INTO staged_rows(dataset, row_key, bucket, content) VALUES (?, ?, ?, ?)",
                (self.dataset, key, bucket, encode_csv(self.schema, [row])),
            )
        except sqlite3.IntegrityError as error:
            raise ValidationError("Duplicate key across snapshot partitions.") from error

    def get(self, key: tuple) -> dict[str, Any] | None:
        result = self.database.execute(
            "SELECT content FROM staged_rows WHERE dataset = ? AND row_key = ?",
            (self.dataset, canonical_json(key)),
        ).fetchone()
        return decode_csv(self.schema, result[0])[0] if result is not None else None

    def publish_parts(self, changes: FileChanges, account: str) -> dict[str, Any]:
        header = encode_csv(self.schema, [])
        descriptions = []
        cursor = self.database.execute(
            "SELECT bucket, content FROM staged_rows WHERE dataset = ? ORDER BY bucket, row_key", (self.dataset,),
        )
        bucket, part, count = 0, 0, 0
        content = bytearray(header)

        def flush():
            path = f"datasets/{self.dataset}/bucket-{bucket:02d}/part-{part:04d}.csv"
            data = bytes(content)
            changes[f"accounts/{account}/{path}"] = data
            descriptions.append({
                "path": path, "sha256": hashlib.sha256(data).hexdigest(),
                "byte_size": len(data), "row_count": count,
            })

        for next_bucket, row_content in cursor:
            row_data = row_content[len(header):]
            if len(header) + len(row_data) > MAX_CSV_BYTES:
                raise ValidationError("A CSV row exceeds the partition size.")
            if count and (next_bucket != bucket or len(content) + len(row_data) > MAX_CSV_BYTES):
                flush()
                part = part + 1 if next_bucket == bucket else 0
                content, count = bytearray(header), 0
            bucket = next_bucket
            content.extend(row_data)
            count += 1
        if count or not descriptions:
            flush()
        return {"schema_version": self.schema.version, "bucket_count": 16, "partitions": descriptions}


def staging_database(path: Path) -> sqlite3.Connection:
    database = sqlite3.connect(path)
    database.execute("PRAGMA cache_size = -2048")
    database.execute(
        "CREATE TABLE staged_rows(dataset TEXT NOT NULL, row_key TEXT NOT NULL, bucket INTEGER NOT NULL, "
        "content BLOB NOT NULL, PRIMARY KEY(dataset, row_key))"
    )
    database.execute("CREATE INDEX staged_order ON staged_rows(dataset, bucket, row_key)")
    return database
