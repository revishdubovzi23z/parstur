import sqlite3

conn = sqlite3.connect("app_data.db")
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

# Search for "Ночная смена" or "Last Straw"
rows = cursor.execute(
    "SELECT id, title, year, category_id, kp_id, imdb_id, checked_kinopub, kinopub_id FROM items WHERE title LIKE ?",
    ("%Ночная смена%",),
).fetchall()

print(f"Found {len(rows)} matching items:")
for r in rows:
    print(dict(r))

conn.close()
