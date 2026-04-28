import sqlite3
conn = sqlite3.connect('app_data.db')
cursor = conn.cursor()
cursor.execute("PRAGMA table_info(items)")
columns = cursor.fetchall()
for col in columns:
    print(col)
conn.close()
