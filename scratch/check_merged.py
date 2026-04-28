import sqlite3
conn = sqlite3.connect('app_data.db')
cursor = conn.cursor()
cursor.execute("SELECT id, title, year, imdb_id, kp_id FROM items WHERE title LIKE '%Хронология воды%' OR title LIKE '%Холостяк в Италии%'")
for row in cursor.fetchall():
    print(row)
conn.close()
