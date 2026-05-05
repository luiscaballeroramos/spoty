from register.db import SimpleDB
from config import DBNAME, DBSCHEMA_LISTENINGEVENT

def print_db(dbname=DBNAME, dbschema=DBSCHEMA_LISTENINGEVENT, output_file=None):
    # database
    db = SimpleDB(dbname, dbschema)
    output = []

    # print database contents & number of rows in each table
    for table in dbschema.keys():
        table_data = db.print_table(table)  # Assuming print_table can return data
        count = db.count_rows(table)
        output.append(f"{count} rows in '{table}':\n{table_data}\n")
        for row in db.cursor.execute(f"SELECT * FROM {table} ORDER BY played_at DESC").fetchall():
            output.append(f"{row}\n")

    if output_file:
        with open(output_file, "w") as f:
            f.writelines(output)
    else:
        print("\n".join(output))

    print(f"Database '{dbname}' contents in table {table}: {sum(db.count_rows(table) for table in dbschema.keys())} registers")

if __name__ == "__main__":
    dbname="spotify.db"
    output_file= dbname.replace(".db", "") + ".txt"
    print_db(output_file=output_file)
