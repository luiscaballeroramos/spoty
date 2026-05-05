from register.db import SimpleDB
from config import DBNAME, DBSCHEMA_LISTENINGEVENT

def print_listening_events(limit: int = None):
    dbname=DBNAME
    output_file= dbname.replace(".db", "") + ".txt"
    db_=SimpleDB(dbname, DBSCHEMA_LISTENINGEVENT)
    db_.print_table("listening_events", order_desc="played_at", output_file=output_file, limit=limit)

if __name__ == "__main__":
    print_listening_events(limit = 10)
