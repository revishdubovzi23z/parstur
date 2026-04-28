import sqlite3
conn = sqlite3.connect('app_data.db')
cursor = conn.cursor()
cursor.execute("SELECT is_ignored FROM items WHERE id=341")
print(f"Is Ignored: {cursor.fetchone()[0]}")
conn.close()
