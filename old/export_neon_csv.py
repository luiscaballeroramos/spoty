"""One-off, read-only export of application tables using PostgreSQL COPY."""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

import psycopg
from psycopg import sql

from config import require_database_url


TABLES_QUERY = """
    SELECT namespace.nspname, relation.relname, relation.relkind
    FROM pg_catalog.pg_class AS relation
    JOIN pg_catalog.pg_namespace AS namespace
      ON namespace.oid = relation.relnamespace
    WHERE relation.relkind IN ('r', 'p')
      AND NOT relation.relispartition
      AND namespace.nspname <> 'information_schema'
      AND namespace.nspname !~ '^pg_'
    ORDER BY namespace.nspname, relation.relname
"""


def export_tables(output: Path) -> int:
    """Stream all user tables in the configured database into a new directory."""
    database_url = require_database_url()
    output.mkdir(parents=True, exist_ok=False)
    tables = []
    started_at = datetime.now(timezone.utc).isoformat()
    with psycopg.connect(
        database_url, autocommit=True, connect_timeout=15,
        application_name="spoty_csv_export",
    ) as connection:
        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.execute(
                    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
                )
                cursor.execute("SET LOCAL row_security = off")
                cursor.execute("SET LOCAL TIME ZONE 'UTC'")
                cursor.execute("SET LOCAL DateStyle = 'ISO, YMD'")
                cursor.execute("SET LOCAL lock_timeout = '5s'")
                cursor.execute(TABLES_QUERY)
                for schema, table, kind in cursor.fetchall():
                    folder = output / ("schema-" + quote(schema, safe=""))
                    folder.mkdir(exist_ok=True)
                    target = folder / ("table-" + quote(table, safe="") + ".csv")
                    partial = target.with_suffix(".csv.part")
                    source = sql.Identifier(schema, table)
                    selection = sql.SQL("SELECT * FROM {}{}").format(
                        sql.SQL("") if kind == "p" else sql.SQL("ONLY "), source
                    )
                    statement = sql.SQL(
                        "COPY ({}) TO STDOUT WITH (FORMAT CSV, HEADER TRUE, ENCODING 'UTF8')"
                    ).format(selection)
                    with partial.open("xb") as destination:
                        with cursor.copy(statement) as stream:
                            for block in stream:
                                destination.write(block)
                    partial.rename(target)
                    tables.append({
                        "schema": schema,
                        "table": table,
                        "file": target.relative_to(output).as_posix(),
                        "bytes": target.stat().st_size,
                    })
                    print(f"Exported table {len(tables)}", flush=True)
    manifest = {
        "status": "complete",
        "started_at": started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "format": "PostgreSQL CSV, UTF-8, header; NULL is unquoted empty field",
        "tables": tables,
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=True) + "\n", encoding="utf-8"
    )
    return len(tables)


def main(argv: list[str] | None = None) -> int:
    """Run an explicit export without exposing connection details on failure."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path,
        help="New output directory; existing directories are never overwritten.",
    )
    args = parser.parse_args(argv)
    output = args.output or (
        Path(__file__).resolve().parent / "database_backups" / "neon"
        / f"csv_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}_{uuid4().hex[:8]}"
    )
    try:
        count = export_tables(output)
    except (Exception, KeyboardInterrupt) as error:
        print(
            f"Export failed ({type(error).__name__}). No complete export was confirmed. "
            "Check configuration, permissions, connection and disk space; "
            "any partial files are retained. Retry with a new directory.",
            file=sys.stderr,
        )
        return 1
    print(f"Complete: {count} tables saved to {output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
