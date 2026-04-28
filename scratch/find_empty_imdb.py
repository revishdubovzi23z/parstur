import sqlite3

conn = sqlite3.connect('app_data.db')
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

cursor.execute("""
    SELECT id, title, year, imdb_id, imdb_rating 
    FROM items 
    WHERE (imdb_id IS NULL OR imdb_id = '') 
      AND (imdb_rating IS NULL OR imdb_rating = 0)
      AND category_id IN (1, 4, 5, 16, 7)
    LIMIT 5
""")

rows = cursor.fetchall()
if not rows:
    print("No matching items found.")
else:
    for row in rows:
        print(f"ID: {row['id']} | Title: {row['title']} | Year: {row['year']} | IMDb ID: {row['imdb_id']} | Rating: {row['imdb_rating']}")

conn.close()
