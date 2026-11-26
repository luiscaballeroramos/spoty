from __future__ import annotations

import base64
import binascii
import csv
import hashlib
import io
import json
import re
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import quote

import requests


DATA_REPOSITORY_OWNER = "luiscaballeroramos"
DATA_REPOSITORY_NAME = "spoty-data"
DATA_BRANCH = "main"
MAX_BLOB_SIZE = 100 * 1024 * 1024

CSV_COLUMNS = {
    "listening_events": (
        "schema_version", "track_id", "played_at", "context_source",
        "context_type", "context_id",
    ),
    "tracks": (
        "id", "name", "duration_ms", "album_id", "album_track",
        "artists_ids", "explicit", "popularity", "disc_number",
    ),
    "artists": ("id", "name"),
    "albums": (
        "id", "name", "artists", "tracks", "total_tracks", "images",
        "release_year",
    ),
}
LIST_FIELDS = {"artists_ids", "artists", "tracks", "images"}
INTEGER_FIELDS = {
    "played_at", "duration_ms", "album_track", "explicit", "popularity",
    "disc_number", "total_tracks", "release_year",
}
SPOTIFY_ID = re.compile(r"[A-Za-z0-9]{22}\Z")
GIT_SHA = re.compile(r"[a-f0-9]{40}\Z")


class DataReadError(RuntimeError):
    """A safe, user-facing error while loading the read-only data snapshot."""


@dataclass(frozen=True)
class SpotySnapshot:
    commit: str
    listening_events: tuple[dict[str, Any], ...]
    tracks: tuple[dict[str, Any], ...]
    artists: tuple[dict[str, Any], ...]
    albums: tuple[dict[str, Any], ...]


def _decode_cell(field: str, value: str, dataset: str, line: int) -> Any:
    if value == r"\N":
        return None
    if value.startswith("\\\\"):
        value = value[1:]
    if field in LIST_FIELDS:
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            raise DataReadError(f"{dataset}.csv tiene una lista JSON invalida en la linea {line}.") from None
        if not isinstance(decoded, list) or any(
            not isinstance(item, str) or not item for item in decoded
        ):
            raise DataReadError(f"{dataset}.csv tiene una lista invalida en la linea {line}.")
        if field in {"artists_ids", "artists", "tracks"} and any(
            not SPOTIFY_ID.fullmatch(item) for item in decoded
        ):
            raise DataReadError(f"{dataset}.csv tiene un identificador de lista invalido en la linea {line}.")
        return decoded
    if field in INTEGER_FIELDS:
        try:
            decoded = int(value)
        except ValueError:
            raise DataReadError(f"{dataset}.csv tiene un numero invalido en la linea {line}.") from None
        if decoded < 0 or (field == "explicit" and decoded not in (0, 1)) or (
            field == "popularity" and decoded > 100
        ):
            raise DataReadError(f"{dataset}.csv tiene un numero fuera de rango en la linea {line}.")
        return decoded
    return value


def parse_csv(dataset: str, content: bytes) -> tuple[dict[str, Any], ...]:
    if dataset not in CSV_COLUMNS:
        raise DataReadError("Dataset no admitido.")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise DataReadError(f"{dataset}.csv no esta codificado como UTF-8.") from None

    reader = csv.DictReader(io.StringIO(text, newline=""))
    if tuple(reader.fieldnames or ()) != CSV_COLUMNS[dataset]:
        raise DataReadError(f"El encabezado de {dataset}.csv no coincide con el esquema esperado.")

    rows = []
    seen = set()
    for line, raw in enumerate(reader, start=2):
        if None in raw or any(value is None for value in raw.values()):
            raise DataReadError(f"{dataset}.csv tiene una fila incompleta o con columnas extra en la linea {line}.")
        row = {
            field: _decode_cell(field, value, dataset, line)
            for field, value in raw.items()
        }
        identifier_field = "track_id" if dataset == "listening_events" else "id"
        identifier = row[identifier_field]
        if not isinstance(identifier, str) or not SPOTIFY_ID.fullmatch(identifier):
            raise DataReadError(f"{dataset}.csv tiene un identificador invalido en la linea {line}.")
        if dataset == "listening_events":
            if row["schema_version"] != "1" or type(row["played_at"]) is not int or row["played_at"] < 0:
                raise DataReadError(f"listening_events.csv tiene una escucha invalida en la linea {line}.")
            key = (identifier, row["played_at"])
        else:
            key = identifier
        if key in seen:
            raise DataReadError(f"{dataset}.csv contiene claves duplicadas.")
        seen.add(key)
        rows.append(row)
    return tuple(rows)


class GitHubDataReader:
    """Read the four current spoty-data CSV files from one immutable commit."""

    def __init__(
        self,
        *,
        owner: str = DATA_REPOSITORY_OWNER,
        repository: str = DATA_REPOSITORY_NAME,
        branch: str = DATA_BRANCH,
        token: str | None = None,
        timeout: float = 30,
        request: Callable[..., Any] = requests.get,
    ) -> None:
        if not isinstance(owner, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*", owner):
            raise ValueError("Invalid GitHub owner.")
        if not isinstance(repository, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", repository):
            raise ValueError("Invalid GitHub repository.")
        if not isinstance(branch, str) or not branch.strip():
            raise ValueError("A GitHub branch is required.")
        if token is not None and (
            not isinstance(token, str) or not token or any(char.isspace() for char in token)
        ):
            raise ValueError("Invalid GitHub read token.")
        if type(timeout) not in (int, float) or not 0 < timeout <= 120:
            raise ValueError("Timeout must be between 0 and 120 seconds.")
        self.owner = owner
        self.repository = repository
        self.branch = branch
        self.token = token
        self.timeout = timeout
        self._request = request
        self._base_url = f"https://api.github.com/repos/{owner}/{repository}"

    def _get_json(self, path: str) -> dict[str, Any]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "spoty-dashboard",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            response = self._request(
                f"{self._base_url}{path}", headers=headers, timeout=self.timeout,
            )
        except requests.RequestException:
            raise DataReadError("No se pudo conectar con GitHub.") from None
        if response.status_code != 200:
            raise DataReadError(f"GitHub rechazo la lectura de datos (HTTP {response.status_code}).")
        try:
            payload = response.json()
        except (ValueError, json.JSONDecodeError):
            raise DataReadError("GitHub devolvio una respuesta JSON invalida.") from None
        if not isinstance(payload, dict):
            raise DataReadError("GitHub devolvio una respuesta inesperada.")
        return payload

    def _read_blob(self, blob_sha: str) -> bytes:
        if not isinstance(blob_sha, str) or not GIT_SHA.fullmatch(blob_sha):
            raise DataReadError("GitHub devolvio una identidad de archivo invalida.")
        blob = self._get_json(f"/git/blobs/{blob_sha}")
        size = blob.get("size")
        encoded = blob.get("content")
        if (
            blob.get("sha") != blob_sha or blob.get("encoding") != "base64"
            or type(size) is not int or not 0 <= size <= MAX_BLOB_SIZE
            or not isinstance(encoded, str)
        ):
            raise DataReadError("GitHub devolvio un archivo incompleto o demasiado grande.")
        try:
            content = base64.b64decode("".join(encoded.split()), validate=True)
        except (binascii.Error, ValueError):
            raise DataReadError("GitHub devolvio un archivo codificado de forma invalida.") from None
        expected_sha = hashlib.sha1(f"blob {len(content)}\0".encode("ascii") + content).hexdigest()
        if len(content) != size or expected_sha != blob_sha:
            raise DataReadError("La integridad de un archivo CSV no pudo verificarse.")
        return content

    def load(self) -> SpotySnapshot:
        branch = quote(self.branch, safe="")
        commit_data = self._get_json(f"/commits/{branch}")
        commit = commit_data.get("sha")
        commit_tree = commit_data.get("commit")
        tree_data = commit_tree.get("tree") if isinstance(commit_tree, dict) else None
        tree_sha = tree_data.get("sha") if isinstance(tree_data, dict) else None
        if not isinstance(commit, str) or not GIT_SHA.fullmatch(commit):
            raise DataReadError("GitHub devolvio un commit invalido.")
        if not isinstance(tree_sha, str) or not GIT_SHA.fullmatch(tree_sha):
            raise DataReadError("GitHub devolvio un arbol de archivos invalido.")

        tree = self._get_json(f"/git/trees/{tree_sha}?recursive=1")
        entries = tree.get("tree")
        if tree.get("sha") != tree_sha or tree.get("truncated") is not False or not isinstance(entries, list):
            raise DataReadError("GitHub devolvio un arbol incompleto.")
        blobs = {}
        for entry in entries:
            if isinstance(entry, dict) and entry.get("path") in {
                f"{name}.csv" for name in CSV_COLUMNS
            }:
                if entry.get("type") != "blob" or not isinstance(entry.get("sha"), str):
                    raise DataReadError("GitHub devolvio una entrada CSV invalida.")
                blobs[entry["path"][:-4]] = entry["sha"]
        missing = set(CSV_COLUMNS) - set(blobs)
        if missing:
            raise DataReadError(f"Faltan archivos de datos en el commit seleccionado: {', '.join(sorted(missing))}.")

        datasets = {
            name: parse_csv(name, self._read_blob(blobs[name]))
            for name in CSV_COLUMNS
        }
        return SpotySnapshot(
            commit=commit,
            listening_events=datasets["listening_events"],
            tracks=datasets["tracks"],
            artists=datasets["artists"],
            albums=datasets["albums"],
        )


def read_spoty_data(token: str | None = None) -> SpotySnapshot:
    return GitHubDataReader(token=token).load()
