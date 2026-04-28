import sqlite3
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

conn = sqlite3.connect('app_data.db')
conn.row_factory = sqlite3.Row
cursor = conn.cursor()
cursor.execute("SELECT id, title, category_id, year FROM items WHERE title LIKE '%Калевала%'")
rows = cursor.fetchall()
for row in rows:
    print(f"ID: {row['id']} | Title: {row['title']} | Cat: {row['category_id']} | Year: {row['year']}")
    # Print hex representation to check for weird characters
    print(f"Hex: {row['title'].encode('utf-8').hex()}")
conn.close()
