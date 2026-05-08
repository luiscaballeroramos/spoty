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
        (
            print("Database initialized with tables: " + ", ".join(self.schema.keys()))
            if VERBOSE
            else None
        )

    def count_rows(self, table: str) -> int:
        self.cursor.execute(f"SELECT COUNT(*) FROM {table}")
        return self.cursor.fetchone()[0]

    def delete_table(self, table: str):
        self.cursor.execute(f"DELETE FROM {table}")
        self.conn.commit()
        print(f"Table '{table}' cleared") if VERBOSE else None

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
                (
                    print(f"[IGNORED - DUPLICATE] {table}: {data}")
                    if not print_only_insert
                    else None
                )

    def print_table(
        self,
        table: str,
        limit: int = None,
        order_asc: str = None,
        order_desc: str = None,
        output_file: str = None,
    ):
        # column names
        self.cursor.execute(f"PRAGMA table_info({table})")
        columns = [col[1] for col in self.cursor.fetchall()]
        # data
        query = f"SELECT * FROM {table}"
        (
            print(f"\n[INFO] number of rows in '{table}': {self.count_rows(table)}")
            if VERBOSE
            else None
        )
        if order_asc:
            query += f" ORDER BY {order_asc} ASC"
        elif order_desc:
            query += f" ORDER BY {order_desc} DESC"
        if limit:
            query += f" LIMIT {limit}"
        self.cursor.execute(query)
        rows = self.cursor.fetchall()
        if not rows:
            print(f"\n[!] table '{table}' empty")
            return
        # Calculate column widths based on the first 10 rows
        sample_rows = rows[:10]
        col_widths = [
            max(len(str(item)) for item in col)
            for col in zip(*([columns] + sample_rows))
        ]
        # Prepare output
        output = []
        output.append(f"\n--- Table {table.upper()} ---")
        # headers
        header_line = " | ".join(
            f"{col:<{col_widths[i]}}" for i, col in enumerate(columns)
        )
        output.append(header_line)
        output.append("-" * len(header_line))
        # rows
        for row in rows:
            output.append(
                " | ".join(
                    f"{str(item):<{col_widths[i]}}" for i, item in enumerate(row)
                )
            )
        output.append("-" * len(header_line) + "\n")
        # Print to console in chunks
        chunk_size = 50  # Number of lines to print at a time
        for i in range(0, len(output), chunk_size):
            print("\n".join(output[i : i + chunk_size]))
        # Write to file if output_file is provided
        if output_file:
            with open(output_file, "w") as f:
                f.write("\n".join(output))
