from datetime import datetime
import time

from config import DBNAME, DBSCHEMA, UTC_OFFSET, VERBOSE
from register.db import SimpleDB
from register.listeningevent import ListeningEvent
from spotifyapi.spotifyclient import SpotifyClient

def register_listeningevents():
    spotify = SpotifyClient()
    db = SimpleDB(DBNAME, DBSCHEMA)
    print("Starting Spotify tracker...")
    # loop to get currently playing every 10 minutes and save to db
    try:
        while True:
            print(f"Fetching recently played tracks at {datetime.now()}") if VERBOSE else None
            recently_played = spotify.get_recently_played(limit=50)
            if recently_played and 'items' in recently_played:
                for track in recently_played['items']:
                    track_id= track['track']['id']
                    date = datetime.fromisoformat(track['played_at'].replace('Z', UTC_OFFSET))
                    context_uri = track['context']['uri'] if track['context'] else None
                    event = ListeningEvent(
                        track_id=track_id,
                        played_at=date,
                        context_uri=context_uri
                    )
                    event.save(db, print_only_insert=True)
            time.sleep(600)
    except KeyboardInterrupt:
        print("Spotify tracker stopped by user.")


if __name__ == "__main__":
    register_listeningevents()
