from typing import List, Optional

from register.db import SimpleDB
import json

class Artist:
    def __init__(self,
                 id: str,
                 name: str,
                 end_year: Optional[int] = None,
                 followers: Optional[int] = None,
                 images: Optional[List[str]] = None,
                 popularity: Optional[int] = None,
                 start_year: Optional[int] = None):
        self.id = id
        self.name = name
        self.end_year = end_year
        self.followers = followers
        self.images = images if images is not None else []
        self.popularity = popularity
        self.start_year = start_year

    def save(self, db: SimpleDB, print_only_insert=False) -> bool:
        return db.insert("artists", {
            "id": self.id,
            "name": self.name,
            "end_year": self.end_year,
            "followers": self.followers,
            "images": json.dumps(self.images),  # Serialize list to JSON
            "popularity": self.popularity,
            "start_year": self.start_year
        }, print_only_insert=print_only_insert,
        print_columns=["id", "name", "followers", "popularity", "start_year", "end_year"])
