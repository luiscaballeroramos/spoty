import json
from typing import Any

import requests


DATA_REPOSITORY = "luiscaballeroramos/spoty-data"
MAX_DISPATCH_BYTES = 60_000
TABLES = ("events", "tracks", "artists", "albums")


def build_dispatches(data: dict[str, Any], source: str) -> list[dict[str, Any]]:
    if not isinstance(source, str) or not source.strip():
        raise ValueError("A data source is required.")
    if not isinstance(data, dict) or set(data) - set(TABLES):
        raise ValueError("Unsupported data tables.")
    batches = []
    payload: dict[str, Any] = {"source": source}
    for table, rows in data.items():
        if not isinstance(rows, list):
            raise ValueError(f"Expected a list of {table}.")
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("Expected a data record.")
            payload.setdefault(table, []).append(row)
            body = {"event_type": "record-data", "client_payload": payload}
            if len(json.dumps(body, ensure_ascii=True, allow_nan=False).encode("utf-8")) > MAX_DISPATCH_BYTES:
                payload[table].pop()
                if not any(payload.get(name) for name in TABLES):
                    raise ValueError("A data record exceeds the dispatch size limit.")
                batches.append({"event_type": "record-data", "client_payload": payload})
                payload = {"source": source, table: [row]}
                if len(json.dumps({"event_type": "record-data", "client_payload": payload}, allow_nan=False).encode("utf-8")) > MAX_DISPATCH_BYTES:
                    raise ValueError("A data record exceeds the dispatch size limit.")
    if any(payload.get(name) for name in TABLES):
        batches.append({"event_type": "record-data", "client_payload": payload})
    return batches


def publish_data(data: dict[str, Any], token: str, source: str) -> None:
    batches = build_dispatches(data, source)
    if not batches:
        return
    if not token:
        raise ValueError("Missing SPOTY_DATA_TOKEN.")
    for index, body in enumerate(batches, start=1):
        response = requests.post(
            f"https://api.github.com/repos/{DATA_REPOSITORY}/dispatches",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            json=body, timeout=20, allow_redirects=False,
        )
        if response.status_code != 204:
            raise RuntimeError(f"GitHub dispatch {index}/{len(batches)} failed (HTTP {response.status_code}); earlier dispatches may have been accepted.")
