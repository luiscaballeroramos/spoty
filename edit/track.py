import spotipy
from spotifyapi.spotifyclient import SpotifyClient

def save_track(track_id):
    sp = SpotifyClient().sp
    sp.current_user_saved_tracks_add([track_id])

