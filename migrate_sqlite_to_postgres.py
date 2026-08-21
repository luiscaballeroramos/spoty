"""Migrate the legacy Spotify SQLite database into PostgreSQL."""

import argparse
import sqlite3
from pathlib import Path

import psycopg
from psycopg import sql

from config import DATABASE_URL, DBSCHEMA


def _sqlite_tables(connection):
    rows = connection.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    )
    return {row[0] for row in rows}


def _validate_source(connection):
    expected = set(DBSCHEMA)
    actual = _sqlite_tables(connection)
    missing = expected - actual
    unexpected = actual - expected
    if missing or unexpected:
        problems = []
        if missing:
            problems.append(f"faltan tablas: {sorted(missing)}")
        if unexpected:
            problems.append(f"tablas no esperadas: {sorted(unexpected)}")
        raise ValueError("Esquema SQLite incompatible; " + "; ".join(problems))

    for table, columns in DBSCHEMA.items():
        actual_columns = {
            row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')
        }
        expected_columns = {
            column for column in columns if not column.startswith("UNIQUE")
        }
        if actual_columns != expected_columns:
            raise ValueError(
                f"Columnas incompatibles en {table}: "
                f"esperadas {sorted(expected_columns)}, recibidas {sorted(actual_columns)}"
            )


def _create_tables(cursor):
    for table, columns in DBSCHEMA.items():
        definitions = []
        for column, data_type in columns.items():
            if column.startswith("UNIQUE"):
                definitions.append(sql.SQL(column))
            else:
                definitions.append(
                    sql.SQL("{} {}").format(sql.Identifier(column), sql.SQL(data_type))
                )
        cursor.execute(
            sql.SQL("CREATE TABLE IF NOT EXISTS {} ({})").format(
                sql.Identifier(table), sql.SQL(", ").join(definitions)
            )
        )


def migrate(source_path: Path, dry_run: bool = False):
    with sqlite3.connect(source_path) as source:
        _validate_source(source)
        counts = {
            table: source.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            for table in DBSCHEMA
        }
        print("Filas encontradas en SQLite:")
        for table, count in counts.items():
            print(f"  {table}: {count}")

        if dry_run:
            print("Dry run completado; PostgreSQL no fue modificado.")
            return

        with psycopg.connect(DATABASE_URL) as target:
            with target.cursor() as cursor:
                _create_tables(cursor)
                for table in DBSCHEMA:
                    columns = [
                        column
                        for column in DBSCHEMA[table]
                        if not column.startswith("UNIQUE")
                    ]
                    identifiers = sql.SQL(", ").join(
                        sql.Identifier(column) for column in columns
                    )
                    placeholders = sql.SQL(", ").join(
                        sql.Placeholder() for _ in columns
                    )
                    insert = sql.SQL(
                        "INSERT INTO {} ({}) VALUES ({}) ON CONFLICT DO NOTHING"
                    ).format(sql.Identifier(table), identifiers, placeholders)
                    selected_columns = ", ".join(f'"{column}"' for column in columns)
                    rows = source.execute(f'SELECT {selected_columns} FROM "{table}"')
                    cursor.executemany(insert, rows)
                    print(f"  {table}: datos enviados")
            # Exiting the target context commits only after every table succeeds.
    print("Migración completada.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, nargs="?", default=Path("spotify.db"))
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="valida y cuenta sin escribir en PostgreSQL",
    )
    args = parser.parse_args()
    if not args.source.is_file():
        parser.error(f"No existe la base SQLite: {args.source}")
    migrate(args.source, args.dry_run)


if __name__ == "__main__":
    main()
