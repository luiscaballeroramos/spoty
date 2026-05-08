import os
import sqlite3

from register.dbtools import compare_schemas, create_db, list_dbfiles, merge_databases, print_dbsummary

def main(path='', merge = False):
    if path == '':
        path = "."

    db_files = list_dbfiles(path)
    if not db_files:
        print("No .db files found.")
        return

    print("Located .db files:")
    for db_file in db_files:
        print(f"- {db_file}")

    print("\nDatabase summaries before merging:")
    for db_file in db_files:
        print_dbsummary(db_file)

    print("\nComparing schemas:")
    for i in range(len(db_files)):
        for j in range(i + 1, len(db_files)):
            are_equal = compare_schemas(db_files[i], db_files[j])
            print(f"Schemas of {db_files[i]} and {db_files[j]} are {'identical' if are_equal else 'different'}.")

    if merge: # or True:  # Force merge to True in the call
        print("\nCreating new target database 'merged_db.db'...")
        target_db = "merged_db.db"
        create_db(target_db)
        print("Merging databases...")
        for source_db in db_files:
            merge_databases(source_db, target_db)
        print(f"All databases merged into {target_db}.")

        print("\nDatabase summary after merging:")
        print_dbsummary(target_db)

if __name__ == "__main__":
    main(merge=False)
