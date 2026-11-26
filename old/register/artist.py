from register.db import SimpleDB
from typing import Optional

class Artist:
    def __init__(self,
                 id: str,
                 name: str):
        self.id = id
        self.name = name

    def edit(self,
             db: SimpleDB,
             id,
             name: Optional[str] = None):
        # check id exist in db
        if not db.exists("artists", id):
            print(f"Artist with id {id} does not exist in db")
        else:
            db.edit("artists", id, {
                "name": name if name is not None else self.name,
            }, print_columns=["id", "name"])


    def save(self, db: SimpleDB, print_only_insert=False) -> bool:
        return db.insert("artists", {
            "id": self.id,
            "name": self.name,
        }, print_only_insert=print_only_insert,
        print_columns=["id", "name"])
