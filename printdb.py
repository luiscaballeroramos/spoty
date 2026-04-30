from register.db import SimpleDB
from config import DBNAME, DBSCHEMA

def print_db():
    # database
    db = SimpleDB(DBNAME, DBSCHEMA)
    # print database contents & number of rows in each table
    for table in DBSCHEMA.keys():
        db.print_table(table)
        count = db.count_rows(table)
        print(f"{count} rows in '{table}': \n")

if __name__ == "__main__":
    print_db()
