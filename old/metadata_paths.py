import json
import re
from pathlib import Path
from typing import Any


_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}


def _safe_path_name(value: str, fallback: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")
    if not name:
        return fallback
    if name.upper() in _WINDOWS_RESERVED_NAMES:
        return f"_{name}"
    return name


def get_metadata_output_dir(
    project_dir: Path,
    artist_name: str,
    album_name: str,
) -> Path:
    output_dir = (
        project_dir
        / "outputs"
        / _safe_path_name(artist_name, "unknown_artist")
        / _safe_path_name(album_name, "unknown_album")
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def save_json(output_dir: Path, filename: str, data: Any) -> None:
    with open(output_dir / filename, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=4, ensure_ascii=False)
