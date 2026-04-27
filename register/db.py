import sqlite3
from typing import Dict, Any

from config import VERBOSE

class SimpleDB:
    def __init__(self, db_path: str, schema: Dict[str, Dict[str, str]]):
        self.conn = sqlite3.connect(db_path)
        self.cursor = self.conn.cursor()
        self.schema = schema
        self.create_tables()

    def create_tables(self):
        for table, cols in self.schema.items():
            parts = []
            for col, typ in cols.items():
                if col.startswith("UNIQUE"):
                    parts.append(col)
                else:
                    parts.append(f"{col} {typ}")
            query = f"CREATE TABLE IF NOT EXISTS {table} ({', '.join(parts)})"
            self.cursor.execute(query)
        self.conn.commit()
        print("Database initialized with tables: " + ", ".join(self.schema.keys())) if VERBOSE else None

    def count_rows(self, table: str) -> int:
        self.cursor.execute(f"SELECT COUNT(*) FROM {table}")
        return self.cursor.fetchone()[0]

    def delete_table(self, table: str):
        self.cursor.execute(f"DELETE FROM {table}")
        self.conn.commit()
        print(f"Table '{table}' cleared") if VERBOSE else None

    def print_table(self, table: str):
        # column names
        self.cursor.execute(f"PRAGMA table_info({table})")
        columns = [col[1] for col in self.cursor.fetchall()]
        # data
        self.cursor.execute(f"SELECT * FROM {table}")
        rows = self.cursor.fetchall()
        if not rows:
            print(f"\n[!] table '{table}' empty")
            return
        # format
        print(f"\n--- Table {table.upper()} ---")
        # print headers
        header_line = " | ".join(f"{col:<15}" for col in columns)
        print(header_line)
        print("-" * len(header_line))
        # print rows
        for row in rows:
            print(" | ".join(f"{str(item):<15}" for item in row))
        print("-" * len(header_line) + "\n")

    def insert(self, table: str, data: Dict[str, Any], print_only_insert=False):
        cols = ", ".join(data.keys())
        placeholders = ", ".join(["?"] * len(data))
        values = list(data.values())

        query = f"""
        INSERT OR IGNORE INTO {table} ({cols})
        VALUES ({placeholders})
        """
        self.cursor.execute(query, values)
        self.conn.commit()

        inserted = self.cursor.rowcount > 0

        if VERBOSE:
            if inserted:
                print(f"[INSERTED] {table}: {data}")
            else:
                print(f"[IGNORED - DUPLICATE] {table}: {data}") if not print_only_insert else None
