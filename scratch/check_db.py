import sqlite3
conn = sqlite3.connect('app_data.db')
c = conn.cursor()
c.execute("SELECT COUNT(*) FROM items")
print(f"Items: {c.fetchone()[0]}")
c.execute("SELECT COUNT(*) FROM collections")
print(f"Collections: {c.fetchone()[0]}")
conn.close()
