from typing import List, Optional
import json

class Track:
    def __init__(self,
                 id: str,
                 name: str,
                 duration_ms: int,
                 album_id: str,
                 album_track: Optional[int] = None,
                 artists_ids: List[str] = None,
                 acousticness: Optional[float] = None,
                 danceability: Optional[float] = None,
                 energy: Optional[float] = None,
                 explicit: Optional[bool] = None,
                 genres: Optional[List[str]] = None,
                 images: Optional[List[str]] = None,
                 instrumentalness: Optional[float] = None,
                 key: Optional[int] = None,
                 liveness: Optional[float] = None,
                 loudness: Optional[float] = None,
                 mode: Optional[int] = None,
                 popularity: Optional[int] = None,
                 speechiness: Optional[float] = None,
                 tempo: Optional[float] = None,
                 time_signature: Optional[int] = None,
                 valence: Optional[float] = None):
        self.id = id
        self.name = name
        self.duration_ms = duration_ms
        self.album_id = album_id
        self.album_track = album_track
        self.artists_ids = artists_ids if artists_ids is not None else []
        self.acousticness = acousticness
        self.danceability = danceability
        self.energy = energy
        self.explicit = explicit
        self.genres = genres if genres is not None else []
        self.images = images if images is not None else []
        self.instrumentalness = instrumentalness
        self.key = key
        self.liveness = liveness
        self.loudness = loudness
        self.mode = mode
        self.popularity = popularity
        self.speechiness = speechiness
        self.tempo = tempo
        self.time_signature = time_signature
        self.valence = valence

    def save(self, db, print_only_insert=False):
        return db.insert("tracks", {
            "id": self.id,
            "name": self.name,
            "duration_ms": self.duration_ms,
            "album_id": self.album_id,
            "album_track": self.album_track,
            "artists_ids": json.dumps(self.artists_ids),
            "acousticness": self.acousticness,
            "danceability": self.danceability,
            "energy": self.energy,
            "explicit": self.explicit,
            "genres": json.dumps(self.genres),
            "images": json.dumps(self.images),
            "instrumentalness": self.instrumentalness,
            "key": self.key,
            "liveness": self.liveness,
            "loudness": self.loudness,
            "mode": self.mode,
            "popularity": self.popularity,
            "speechiness": self.speechiness,
            "tempo": self.tempo,
            "time_signature": self.time_signature,
            "valence": self.valence
        }, print_only_insert=print_only_insert,
        print_columns=["id", "name", "duration_ms", "album_id", "artists_ids"])
