import os
import sqlite3
import time

def check_table_schema(db_path, table_name):
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Obtener el esquema de la tabla
        cursor.execute(f"PRAGMA table_info({table_name})")
        columns = cursor.fetchall()
        print("Columnas de la tabla:")
        for column in columns:
            print(column)

        # Obtener las restricciones UNIQUE
        cursor.execute(f"SELECT sql FROM sqlite_master WHERE type='table' AND name='{table_name}'")
        table_definition = cursor.fetchone()
        if table_definition:
            print("\nDefinición de la tabla:")
            print(table_definition[0])
        else:
            print("\nNo se encontró la tabla.")

    except sqlite3.Error as e:
        print(f"Error al acceder a la base de datos: {e}")
    finally:
        if conn:
            conn.close()

def compare_schemas(db1_path, db2_path):
    """Compare the schemas of two SQLite databases."""
    with sqlite3.connect(db1_path) as conn1, sqlite3.connect(db2_path) as conn2:
        schema1 = conn1.execute("SELECT sql FROM sqlite_master WHERE type='table'").fetchall()
        schema2 = conn2.execute("SELECT sql FROM sqlite_master WHERE type='table'").fetchall()
        return set(schema1) == set(schema2)

def create_db(target_path, overwrite_if_exists=True):
    """Create a new SQLite database file as the target for merging."""
    if os.path.exists(target_path) and overwrite_if_exists:
        os.remove(target_path)  # Remove if it already exists
    with sqlite3.connect(target_path) as conn:
        pass  # Create an empty database file

def generate_schema(schema_dict):
    schema_parts = []
    for column, definition in schema_dict.items():
        if column.startswith("UNIQUE"):
            # Handle UNIQUE constraints properly
            constraint = column[column.find("(") + 1:column.find(")")]
            schema_parts.append(f"UNIQUE({constraint})")
        else:
            schema_parts.append(f"{column} {definition}")
    return ", ".join(schema_parts)

def list_dbfiles(path="."):
    """Locate all .db files in the given path."""
    return [os.path.join(path, file) for file in os.listdir(path) if file.endswith('.db')]

def merge_databases(source_db, target_db):
    """Merge the contents of source_db into target_db, only if rows are different."""
    with sqlite3.connect(source_db) as src_conn, sqlite3.connect(target_db) as tgt_conn:
        src_cursor = src_conn.cursor()
        tgt_cursor = tgt_conn.cursor()

        # Get all tables from the source database
        tables = src_cursor.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        for (table_name,) in tables:
            # Copy data from each table
            rows = src_cursor.execute(f"SELECT * FROM {table_name}").fetchall()
            columns = [desc[0] for desc in src_cursor.description]

            # Create table in target database if it doesn't exist
            column_definitions = ", ".join([f"{col} TEXT" for col in columns])
            tgt_cursor.execute(f"CREATE TABLE IF NOT EXISTS {table_name} ({column_definitions})")

            # Insert only unique rows into the target database
            for row in rows:
                placeholders = ", ".join(["?" for _ in columns])
                query = f"SELECT 1 FROM {table_name} WHERE " + " AND ".join([f"{col} = ?" for col in columns])
                exists = tgt_cursor.execute(query, row).fetchone()
                if not exists:
                    tgt_cursor.execute(f"INSERT INTO {table_name} VALUES ({placeholders})", row)

        tgt_conn.commit()

def print_dbsummary(db_path):
    """Print the number of tables and the number of entries per table in the database."""
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        tables = cursor.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        print(f"Database: {db_path}")
        print(f"Number of tables: {len(tables)}")
        for (table_name,) in tables:
            count = cursor.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
            print(f"- Table '{table_name}': {count} entries")

def review_table_byschema(db_path, old_table, schema):
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        # Generate a unique name for the new table to avoid conflicts
        new_table = f"{old_table}_{int(time.time())}"  # Append timestamp to avoid conflicts

        # Crear la nueva tabla con el esquema correcto
        cursor.execute(f"DROP TABLE IF EXISTS {new_table}")
        cursor.execute(f"CREATE TABLE {new_table} ({schema})")
        print(f"Tabla '{new_table}' creada correctamente.")

        # Migrar los datos de la tabla antigua a la nueva
        cursor.execute(f"INSERT OR IGNORE INTO {new_table} SELECT * FROM {old_table}")
        conn.commit()
        print(f"Datos migrados de '{old_table}' a '{new_table}'.")

        # Eliminar la tabla antigua
        cursor.execute(f"DROP TABLE {old_table}")
        conn.commit()
        print(f"Tabla antigua '{old_table}' eliminada.")

        # Renombrar la nueva tabla a la original
        cursor.execute(f"ALTER TABLE {new_table} RENAME TO {old_table}")
        conn.commit()
        print(f"Tabla '{new_table}' renombrada a '{old_table}'.")

    except sqlite3.Error as e:
        print(f"Error durante la migración: {e}")
    finally:
        if conn:
            conn.close()


