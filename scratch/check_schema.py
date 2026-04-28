import sqlite3
conn = sqlite3.connect('app_data.db')
cursor = conn.cursor()
cursor.execute("PRAGMA table_info(releases)")
for row in cursor.fetchall():
    print(row)
conn.close()
