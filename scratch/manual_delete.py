import sqlite3
conn = sqlite3.connect('app_data.db')
c = conn.cursor()
tables_to_clear = [
    "releases", # delete dependent first
    "collection_items",
    "item_search_names",
    "items",
    "collections",
    "job_history",
    "audit_log",
    "user_ratings",
]
for table in tables_to_clear:
    try:
        c.execute(f"DELETE FROM {table}")
        print(f"Deleted {table}")
    except Exception as e:
        print(f"Error {table}: {e}")

c.execute("DELETE FROM app_state WHERE key = 'last_visit'")
conn.commit()
conn.close()
print("Done")
