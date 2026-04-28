import sqlite3

conn = sqlite3.connect('app_data.db')
c = conn.cursor()

print("--- user_ratings schema ---")
for col in c.execute("PRAGMA table_info(user_ratings)").fetchall():
    print(col)

print("\n--- items schema ---")
for col in c.execute("PRAGMA table_info(items)").fetchall():
    print(col)

conn.close()
