from typing import Any, Dict

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from config import VERBOSE, DBSCHEMA



class SimpleDB:
    def __init__(self, db_path: str):
        self.conn = psycopg.connect(db_path, row_factory=dict_row)
        self.cursor = self.conn.cursor()
        self.schema = DBSCHEMA
        self.create_tables()

    def create_tables(self):
        for table, cols in self.schema.items():
            parts = []
            for col, typ in cols.items():
                if col.startswith("UNIQUE"):
                    parts.append(sql.SQL(col))
                else:
                    parts.append(
                        sql.SQL("{} {}").format(sql.Identifier(col), sql.SQL(typ))
                    )
            query = sql.SQL("CREATE TABLE IF NOT EXISTS {} ({})").format(
                sql.Identifier(table), sql.SQL(", ").join(parts)
            )
            self.cursor.execute(query)
        self.conn.commit()
        (
            print("Database initialized with tables: " + ", ".join(self.schema.keys()))
            if VERBOSE
            else None
        )

    def count_rows(self, table: str) -> int:
        query = sql.SQL("SELECT COUNT(*) AS total FROM {}").format(sql.Identifier(table))
        self.cursor.execute(query)
        row = self.cursor.fetchone()
        return int(row["total"]) if row else 0

    def delete_table(self, table: str):
        query = sql.SQL("DELETE FROM {}").format(sql.Identifier(table))
        self.cursor.execute(query)
        self.conn.commit()
        print(f"Table '{table}' cleared") if VERBOSE else None

    def exists(self, table: str, id: str) -> bool:
        query = sql.SQL("SELECT 1 FROM {} WHERE id = %s LIMIT 1").format(
            sql.Identifier(table)
        )
        self.cursor.execute(query, (id,))
        return self.cursor.fetchone() is not None

    def edit(self, table: str, id: str, data: Dict[str, Any], print_only_updated=False, print_columns: list = None):
        # check if id exist in db
        query = sql.SQL("SELECT * FROM {} WHERE id = %s").format(sql.Identifier(table))
        self.cursor.execute(query, (id,))
        existing_data = self.cursor.fetchone()
        if not existing_data:
            print(f"[NOT UPDATED] {table} with id {id} does not exist in db") if VERBOSE else None
            return False
        # check if update is necessary
        if all(existing_data[col] == data[col] for col in data.keys()):
            print(f"[NOT UPDATED] {table} with id {id} already has the same data") if VERBOSE else None
            return False
        # update record
        set_clause = sql.SQL(", ").join(
            sql.SQL("{} = %s").format(sql.Identifier(col)) for col in data.keys()
        )
        values = list(data.values()) + [id]
        query = sql.SQL("UPDATE {} SET {} WHERE id = %s").format(
            sql.Identifier(table), set_clause
        )
        self.cursor.execute(query, values)
        self.conn.commit()
        if VERBOSE:
            if print_only_updated:
                if print_columns:
                    filtered_data = {key: data[key] for key in print_columns if key in data}
                    print(f"[UPDATED] {table} with id {id}: {filtered_data}")
                else:
                    print(f"[UPDATED] {table} with id {id}: {data}")
        return True

    def insert(self, table: str, data: Dict[str, Any], print_only_insert=False, print_columns: list = None):
        cols = sql.SQL(", ").join(sql.Identifier(col) for col in data.keys())
        placeholders = sql.SQL(", ").join(sql.Placeholder() for _ in data)
        values = list(data.values())
        query = sql.SQL(
            "INSERT INTO {} ({}) VALUES ({}) ON CONFLICT DO NOTHING"
        ).format(sql.Identifier(table), cols, placeholders)
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
        return inserted

    def _get_table_columns(self, table: str):
        query = """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = %s
            ORDER BY ordinal_position
        """
        self.cursor.execute(query, (table,))
        rows = self.cursor.fetchall()
        return [row["column_name"] for row in rows]

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
        columns = self._get_table_columns(table)
        if not columns:
            print(f"\n[!] table '{table}' does not exist")
            return
        # If print_columns requested, keep only existing columns in that order
        if print_columns:
            selected_columns = [c for c in print_columns if c in columns]
            if not selected_columns:
                print(f"[!] none of the requested columns {print_columns} exist in '{table}'")
                return
        else:
            selected_columns = columns
        # data
        query = sql.SQL("SELECT * FROM {}").format(sql.Identifier(table))
        params = []
        (
            print(f"\n[INFO] number of rows in '{table}': {self.count_rows(table)}")
            if VERBOSE
            else None
        )
        if order_asc:
            query += sql.SQL(" ORDER BY {} ASC").format(sql.Identifier(order_asc))
        elif order_desc:
            query += sql.SQL(" ORDER BY {} DESC").format(sql.Identifier(order_desc))
        if limit:
            query += sql.SQL(" LIMIT %s")
            params.append(limit)
        if params:
            self.cursor.execute(query, params)
        else:
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
            with open(output_file, "w", encoding="utf-8") as f:
                f.write("\n".join(output))
