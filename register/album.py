from datetime import datetime
from typing import List, Optional
import json

class Album:

    def __init__(self,
                 id: str,
                 name: str,
                 artists: List[str],
                 tracks: List[str],
                 total_tracks: int,
                 disc_number: Optional[int] = None,
                 images: Optional[List[str]] = None,
                 popularity: Optional[int] = None,
                 release_date: Optional[str] = None,
                 release_date_precision: Optional[str] = None):
        self.id = id
        self.name = name
        self.artists = artists
        self.tracks = tracks
        self.total_tracks = total_tracks
        self.disc_number = disc_number
        self.images = images if images is not None else []
        self.popularity = popularity
        _date=self.__getyear(release_date, release_date_precision)
        self.release_year = _date if _date else None

    def __getyear(self, release_date: str, precision: str) -> Optional[int]:
        # check translation to datetime
        _datetime=None
        try:
            if precision == "day":
                _datetime = datetime.strptime(release_date, "%Y-%m-%d")
            elif precision == "month":
                _datetime = datetime.strptime(release_date, "%Y-%m")
            elif precision == "year":
                _datetime = datetime.strptime(release_date, "%Y")
        except ValueError:
            return None
        # check translation to int
        _int=None
        if precision == "day" or precision == "month":
            _int = int(release_date.split("-")[0])
        elif precision == "year":
            _int = int(release_date)
        else:
            _int = None
        # check  datetime and int are the same
        if _datetime and _int and _datetime.year == _int:
            return _int
        return None

    def edit(self,
             db,
             id,
             name: Optional[str] = None,
             artists: Optional[List[str]] = None,
             tracks: Optional[List[str]] = None,
             total_tracks: Optional[int] = None,
             disc_number: Optional[int] = None,
             images: Optional[List[str]] = None,
             popularity: Optional[int] = None,
             release_date: Optional[str] = None,
             release_date_precision: Optional[str] = None):
        # check id exist in db
        if not db.exists("albums", id):
            print(f"Album with id {id} does not exist in db")
        else:
            _release_year=self.__getyear(release_date, release_date_precision) if release_date and release_date_precision else self.release_year
            db.edit("albums", id, {
                "name": name if name is not None else self.name,
                "artists": json.dumps(artists) if artists is not None else json.dumps(self.artists),
                "tracks": json.dumps(tracks) if tracks is not None else json.dumps(self.tracks),
                "total_tracks": total_tracks if total_tracks is not None else self.total_tracks,
                "disc_number": disc_number if disc_number is not None else self.disc_number,
                "images": json.dumps(images) if images is not None else json.dumps(self.images),
                "popularity": popularity if popularity is not None else self.popularity,
                "release_year": _release_year
            }, print_columns=["id", "name", "total_tracks", "popularity", "release_year"])

    def save(self, db, print_only_insert=False):
        return db.insert("albums", {
            "id": self.id,
            "name": self.name,
            "artists": json.dumps(self.artists),
            "tracks": json.dumps(self.tracks),
            "total_tracks": self.total_tracks,
            "disc_number": self.disc_number,
            "images": json.dumps(self.images),
            "popularity": self.popularity,
            "release_year": self.release_year
        }, print_only_insert=print_only_insert,
        print_columns=["id", "name", "total_tracks", "popularity", "release_year"])
