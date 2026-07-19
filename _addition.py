from edit.track import save_track
from spotifyapi.spotifyclient import SpotifyClient

if __name__ == "__main__":
    track_id = "6CymvIvL8XVmXnGRREmWBX"
    track_name = ""

    client = SpotifyClient()
    library = client.get_saved_tracks()

    try:
        track_name = next(t['track']['name'] for t in library if t['track']['id'] == track_id)
        print(f"Track: {track_name} is already in your library.")
    except StopIteration:
        save_track(track_id)
        print(f"Track: {track_name} added to your library.")
