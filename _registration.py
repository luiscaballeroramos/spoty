from datetime import datetime
import time

from config import DBNAME, DBSCHEMA, UTC_OFFSET, VERBOSE
from register.album import Album
from register.artist import Artist
from register.db import SimpleDB
from register.track import Track
from register.listeningevent import ListeningEvent
from spotifyapi.spotifyclient import SpotifyClient

def register_listeningevents():
    spotify = SpotifyClient()
    db = SimpleDB(DBNAME)
    print("Starting Spotify tracker...")
    # loop to get currently playing every 10 minutes and save to db
    try:
        while True:
            print(f"Fetching recently played tracks at {datetime.now()}") if VERBOSE else None
            recently_played = spotify.get_recently_played(limit=50)
            if recently_played and 'items' in recently_played:
                for item in recently_played['items']:
                    # track
                    track = item['track']
                    track_id= track['id']
                    track_name = track['name']
                    track_duration_ms = track['duration_ms']
                    track_album_id = track['album']['id']
                    track_album_track = track['track_number']
                    track_artists_ids = [artist['id'] for artist in track['artists']]
                    track_explicit = track['explicit']
                    _track =Track(
                        id=track_id,
                        name=track_name,
                        duration_ms=track_duration_ms,
                        album_id=track_album_id,
                        album_track=track_album_track,
                        artists_ids=track_artists_ids,
                        explicit=track_explicit
                    )
                    print(f'Save TRACK: {track_name} ({track_id})') if VERBOSE else None
                    # _track.save(db, print_only_insert=True)
                    # album
                    album=track['album']
                    album_id=album['id']
                    album_name=album['name']
                    album_artists_ids=[artist['id'] for artist in album['artists']]
                    album_total_tracks=album['total_tracks']
                    album_images=[img['url'] for img in album['images']] if 'images' in album else []
                    # album_release_date=album['release_date']
                    # album_release_date_precision=album['release_date_precision']
                    _album=Album(
                        id=album_id,
                        name=album_name,
                        artists=album_artists_ids,
                        total_tracks=album_total_tracks,
                        images=album_images
                    )
                    print(f'Save ALBUM: {album_name} ({album_id})') if VERBOSE else None
                    # _album.save(db, print_only_insert=True)
                    # artists
                    artists=[]
                    for artist in track['artists'] + album['artists']:
                        artist_id=artist['id']
                        artist_name=artist['name']
                        _artist=Artist(
                            id=artist_id,
                            name=artist_name
                        )
                        artists.append(_artist)
                        print(f'Save ARTIST: {artist_name} ({artist_id})') if VERBOSE else None
                        # _artist.save(db, print_only_insert=True)



                    # event
                    date = datetime.fromisoformat(item['played_at'].replace('Z', UTC_OFFSET))
                    context_uri = item['track']['context']['uri'] if item['track']['context'] else None
                    event = ListeningEvent(
                        track_id=track_id,
                        played_at=date,
                        context_uri=context_uri
                    )
                    print(f'Save LISTENING EVENT: {track_name} at {date}') if VERBOSE else None
                    # event.save(db, print_only_insert=True)
            time.sleep(600)
    except KeyboardInterrupt:
        print("Spotify tracker stopped by user.")


if __name__ == "__main__":
    register_listeningevents()
