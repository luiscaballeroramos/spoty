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
                #  release_date: Optional[str] = None,
                #  release_date_precision: Optional[str] = None,
                 release_decade: Optional[int] = None,
                 release_year: Optional[int] = None):
        self.id = id
        self.name = name
        self.artists = artists
        self.tracks = tracks
        self.total_tracks = total_tracks
        self.disc_number = disc_number
        self.images = images if images is not None else []
        self.popularity = popularity
        # self.release_date = release_date
        # self.release_date_precision = release_date_precision
        self.release_decade = release_decade
        self.release_year = release_year

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
            # "release_date": self.release_date,
            # "release_date_precision": self.release_date_precision,
            "release_decade": self.release_decade,
            "release_year": self.release_year
        }, print_only_insert=print_only_insert,
        print_columns=["id", "name", "total_tracks", "popularity", "release_decade", "release_year"])
