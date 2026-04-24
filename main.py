from spotifyapi.spotifyclient import SpotifyClient
from datetime import datetime

def print_tree(data, indent=0):
    prefix = "    " * indent
    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(value, (dict, list)):
                print(f"{prefix}{key}:")
                print_tree(value, indent + 1)
            else:
                print(f"{prefix}{key}: {value}")
    elif isinstance(data, list):
        for index, item in enumerate(data):
            if isinstance(item, (dict, list)):
                print(f"{prefix}[{index}]:")
                print_tree(item, indent + 1)
            else:
                print(f"{prefix}[{index}] {item}")
    else:
        print(f"{prefix}{data}")

def main():
    spotify = SpotifyClient()
    currently_playing = spotify.get_currently_playing()
    recently_played = spotify.get_recently_played()
    print("Currently Playing:")
    print_tree(currently_playing)
    print("\n")
    print("Example Recently Played:")
    print_tree(recently_played['items'][0])  # Print the first recently played track
    print("\n")
    print("Recently Played:")
    prevtime = None
    deltaTime = 0
    for track in recently_played['items']:
        if prevtime is not None:
            deltaTime = (datetime.fromisoformat(prevtime.replace('Z', '+00:00')) - datetime.fromisoformat(track['played_at'].replace('Z', '+00:00'))).total_seconds()
        else:
            deltaTime = 0
        deltaTimeStr= (f"{deltaTime:.0f} s" if deltaTime > 0 else "N/A").rjust(6)
        print(f"{track['played_at']} ({deltaTimeStr}) {track['track']['name']}")
        prevtime = track['played_at']

if __name__ == "__main__":
    main()
