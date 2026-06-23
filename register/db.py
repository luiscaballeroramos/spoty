import sqlite3
from typing import Dict, Any

from config import VERBOSE, DBSCHEMA



class SimpleDB:
    def __init__(self, db_path: str):
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row  # Set row_factory to return rows as dictionaries
        self.cursor = self.conn.cursor()
        self.schema = DBSCHEMA
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

    def edit(self, table: str, id: str, data: Dict[str, Any], print_only_updated=False, print_columns: list = None):
        # check if id exist in db
        self.cursor.execute(f"SELECT * FROM {table} WHERE id = ?", (id,))
        if not self.cursor.fetchone():
            print(f"[NOT UPDATED] {table} with id {id} does not exist in db") if VERBOSE else None
            return False
        # check if update is necessary
        set_clause = ", ".join([f"{col} = ?" for col in data.keys()])
        values = list(data.values()) + [id]
        query = f"SELECT * FROM {table} WHERE id = ?"
        self.cursor.execute(query, (id,))
        existing_data = self.cursor.fetchone()
        if all(existing_data[col] == data[col] for col in data.keys()):
            print(f"[NOT UPDATED] {table} with id {id} already has the same data") if VERBOSE else None
            return False
        # update record
        set_clause = ", ".join([f"{col} = ?" for col in data.keys()])
        values = list(data.values()) + [id]
        query = f"UPDATE {table} SET {set_clause} WHERE id = ?"
        self.cursor.execute(query, values)
        self.conn.commit()
        if VERBOSE:
            if print_only_updated:
                if print_columns:
                    filtered_data = {key: data[key] for key in print_columns if key in data}
                    print(f"[UPDATED] {table} with id {id}: {filtered_data}")
                else:
                    print(f"[UPDATED] {table} with id {id}: {data}")

    def insert(self, table: str, data: Dict[str, Any], print_only_insert=False, print_columns: list = None):
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
                if print_columns:
                    filtered_data = {key: data[key] for key in print_columns if key in data}
                    print(f"[INSERTED] {table}: {filtered_data}")
                else:
                    print(f"[INSERTED] {table}: {data}")
            else:
                if not print_only_insert:
                    if print_columns:
                        filtered_data = {key: data[key] for key in print_columns if key in data}
                        print(f"[IGNORED - DUPLICATE] {table}: {filtered_data}")
                    else:
                        print(f"[IGNORED - DUPLICATE] {table}: {data}")

    def print_table(
        self,
        table: str,
        limit: int = None,
        order_asc: str = None,
        order_desc: str = None,
        output_file: str = None,
        print_columns: list = None,
    ):
        # column names
        self.cursor.execute(f"PRAGMA table_info({table})")
        columns = [col[1] for col in self.cursor.fetchall()]
        # If print_columns requested, keep only existing columns in that order
        if print_columns:
            selected_columns = [c for c in print_columns if c in columns]
            if not selected_columns:
                print(f"[!] none of the requested columns {print_columns} exist in '{table}'")
                return
        else:
            selected_columns = columns
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
        # Calculate column widths based on the first 10 rows for selected columns
        sample_rows = rows[:10]
        # Build rows as lists of values for selected columns
        sample_values = [[r[c] for c in selected_columns] for r in sample_rows]
        col_widths = [
            max(len(str(item)) for item in col)
            for col in zip(*([selected_columns] + sample_values))
        ]
        # Prepare output
        output = []
        output.append(f"\n--- Table {table.upper()} ---")
        # headers
        header_line = " | ".join(
            f"{col:<{col_widths[i]}}" for i, col in enumerate(selected_columns)
        )
        output.append(header_line)
        output.append("-" * len(header_line))
        # rows
        for row in rows:
            row_values = [row[c] for c in selected_columns]
            output.append(
                " | ".join(
                    f"{str(item):<{col_widths[i]}}" for i, item in enumerate(row_values)
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
