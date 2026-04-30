import os
from typing import Any

from spotifyapi.spotifyclient import SpotifyClient


def print_all_properties(obj: Any, prefix: str = "") -> None:
	if isinstance(obj, dict):
		for key, value in obj.items():
			path = f"{prefix}.{key}" if prefix else str(key)
			print(path)
			print_all_properties(value, path)
	elif isinstance(obj, list):
		for i, item in enumerate(obj):
			path = f"{prefix}[{i}]"
			print(path)
			print_all_properties(item, path)


def print_as_tree(obj: Any, indent: int = 0, prefix: str = "", is_last: bool = True) -> None:
    branch = "└── " if is_last else "├── "
    if isinstance(obj, dict):
        for i, (key, value) in enumerate(obj.items()):
            is_last_item = i == len(obj) - 1
            print(f"{prefix}{branch}{key}")
            extension = "    " if is_last_item else "│   "
            print_as_tree(value, indent + 2, prefix + extension, is_last_item)
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            is_last_item = i == len(obj) - 1
            print(f"{prefix}{branch}[{i}]")
            extension = "    " if is_last_item else "│   "
            print_as_tree(item, indent + 2, prefix + extension, is_last_item)
    else:
        print(f"{prefix}{branch}{obj}")


def main() -> None:
    client = SpotifyClient()

    # Replace with any valid track ID/URI/URL
    track = client.sp.track("3n3Ppam7vgaVa1iaRUc9Lp")
    print_as_tree(track)

    # # Fetch and print artist details
    # artist = client.sp.artist("0OdUWJ0sBjDrqHygGUXeCF")
    # print_as_tree(artist)

    # # Fetch and print album details
    # album = client.sp.album("11lYdxQdsgkvKfDjX0nTHa")
    # print_as_tree(album)



if __name__ == "__main__":
    main()
