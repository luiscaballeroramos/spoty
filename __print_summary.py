from config import DBNAME
from register.db import SimpleDB


def print_database_summary() -> None:
    db = SimpleDB(DBNAME)
    db.cursor.execute("""
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = current_schema()
          AND table_type = 'BASE TABLE'
        ORDER BY table_name
        """)
    tables = [row["table_name"] for row in db.cursor.fetchall()]

    print("\n--- Database summary ---")
    if not tables:
        print("No tables found")
        return

    for table in tables:
        print(f"{table}: {db.count_rows(table)} entries")


if __name__ == "__main__":
    print_database_summary()
