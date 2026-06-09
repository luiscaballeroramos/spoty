import sqlite3
from config import DBSCHEMA_LISTENINGEVENT
from register.dbtools import check_table_schema, generate_schema, review_table_byschema

def check_schema(db_path="spotify.db", table_name="listening_events"):
    # check the table's schema again to confirm it matches the expected schema
    check_table_schema(db_path, table_name)

def review_schema(db_path="spotify.db", old_table="listening_events"):
    # review the table by creating a new one with the correct schema, migrating the data, and replacing the old table
    schema_dict = DBSCHEMA_LISTENINGEVENT["listening_events"]
    schema = generate_schema(schema_dict)
    review_table_byschema(db_path, old_table, schema)


if __name__ == "__main__":
    # check schema
    check_schema()
    # review table's schema
    review_schema()


