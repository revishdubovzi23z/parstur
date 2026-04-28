import sqlite3
conn = sqlite3.connect('app_data.db')
cursor = conn.cursor()
cursor.execute("SELECT id, title, is_metadata_fixed, kp_id, imdb_id FROM items WHERE title LIKE '%Мы в разводе%'")
for row in cursor.fetchall():
    print(row)
conn.close()
