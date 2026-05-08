from typing import Any

from config import DBNAME, DBSCHEMA_LISTENINGEVENT
from register.db import SimpleDB
from spotifyapi.spotifyclient import SpotifyClient

def _print_all_properties(obj: Any, prefix: str = "") -> None:
	if isinstance(obj, dict):
		for key, value in obj.items():
			path = f"{prefix}.{key}" if prefix else str(key)
			print(path)
			_print_all_properties(value, path)
	elif isinstance(obj, list):
		for i, item in enumerate(obj):
			path = f"{prefix}[{i}]"
			print(path)
			_print_all_properties(item, path)


def _print_as_tree(obj: Any, indent: int = 0, prefix: str = "", is_last: bool = True) -> None:
    branch = "└── " if is_last else "├── "
    if isinstance(obj, dict):
        for i, (key, value) in enumerate(obj.items()):
            is_last_item = i == len(obj) - 1
            print(f"{prefix}{branch}{key}")
            extension = "    " if is_last_item else "│   "
            _print_as_tree(value, indent + 2, prefix + extension, is_last_item)
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            is_last_item = i == len(obj) - 1
            print(f"{prefix}{branch}[{i}]")
            extension = "    " if is_last_item else "│   "
            _print_as_tree(item, indent + 2, prefix + extension, is_last_item)
    else:
        print(f"{prefix}{branch}{obj}")


def print_listening_events(limit: int = None):
    dbname=DBNAME
    output_file= dbname.replace(".db", "") + ".txt"
    db_=SimpleDB(dbname, DBSCHEMA_LISTENINGEVENT)
    db_.print_table("listening_events", order_desc="played_at", output_file=output_file, limit=limit)

if __name__ == "__main__":
    client = SpotifyClient()

    # # Print last 10 listening events
    # print_listening_events(limit = 10)

    # Replace with any valid track ID/URI/URL
    track = client.sp.track("3n3Ppam7vgaVa1iaRUc9Lp")
    _print_as_tree(track)

    # # Fetch and print artist details
    # artist = client.sp.artist("0OdUWJ0sBjDrqHygGUXeCF")
    # _print_as_tree(artist)

    # # Fetch and print album details
    # album = client.sp.album("11lYdxQdsgkvKfDjX0nTHa")
    # _print_as_tree(album)
