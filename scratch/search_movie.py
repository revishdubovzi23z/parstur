import sqlite3
conn = sqlite3.connect('app_data.db')
cursor = conn.cursor()
cursor.execute("SELECT id, title, year, category_id FROM items WHERE title LIKE '%Братья под огнём%'")
for row in cursor.fetchall():
    print(row)
conn.close()
