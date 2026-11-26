from typing import List, Optional
import json


def _bool_to_int(value: Optional[bool]) -> Optional[int]:
    if value is None:
        return None
    return int(value)

class Track:
    def __init__(self,
                 id: str,
                 name: str,
                 duration_ms: int,
                 album_id: str,
                 album_track: Optional[int] = None,
                 disc_number: Optional[int] = None,
                 artists_ids: List[str] = None,
                 explicit: Optional[bool] = None,
                 popularity: Optional[int] = None):
        self.id = id
        self.name = name
        self.duration_ms = duration_ms
        self.album_id = album_id
        self.album_track = album_track
        self.disc_number = disc_number
        self.artists_ids = artists_ids if artists_ids is not None else []
        self.explicit = explicit
        self.popularity = popularity

    def edit(self,
             db,
             id,
             name: Optional[str] = None,
             duration_ms: Optional[int] = None,
             album_id: Optional[str] = None,
             album_track: Optional[int] = None,
             disc_number: Optional[int] = None,
             artists_ids: Optional[List[str]] = None,
             explicit: Optional[bool] = None,
             popularity: Optional[int] = None):
        # check id exist in db
        if not db.exists("tracks", id):
            print(f"Track with id {id} does not exist in db")
        else:
            db.edit("tracks", id, {
                "name": name if name is not None else self.name,
                "duration_ms": duration_ms if duration_ms is not None else self.duration_ms,
                "album_id": album_id if album_id is not None else self.album_id,
                "album_track": album_track if album_track is not None else self.album_track,
                "disc_number": disc_number if disc_number is not None else self.disc_number,
                "artists_ids": json.dumps(artists_ids) if artists_ids is not None else json.dumps(self.artists_ids),
                "explicit": _bool_to_int(explicit) if explicit is not None else _bool_to_int(self.explicit),
                "popularity": popularity if popularity is not None else self.popularity,
            }, print_columns=["id", "name", "duration_ms", "album_id", "artists_ids"])

    def save(self, db, print_only_insert=False):
        return db.insert("tracks", {
            "id": self.id,
            "name": self.name,
            "duration_ms": self.duration_ms,
            "album_id": self.album_id,
            "album_track": self.album_track,
            "disc_number": self.disc_number,
            "artists_ids": json.dumps(self.artists_ids),
            "explicit": _bool_to_int(self.explicit),
            "popularity": self.popularity,
        }, print_only_insert=print_only_insert,
        print_columns=["id", "name", "duration_ms", "album_id", "artists_ids"])
