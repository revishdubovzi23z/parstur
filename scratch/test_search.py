import sqlite3
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

def py_lower(x):
    return x.lower() if x is not None else None

conn = sqlite3.connect('app_data.db')
conn.create_function("py_lower", 1, py_lower)
cursor = conn.cursor()

search_term = "калевала"
query = "SELECT title FROM items WHERE py_lower(title) LIKE py_lower(?)"
params = (f"%{search_term}%",)

print(f"Searching for: {search_term}")
cursor.execute(query, params)
rows = cursor.fetchall()

if rows:
    for row in rows:
        print(f"Found: {row[0]}")
else:
    print("Nothing found.")

conn.close()
