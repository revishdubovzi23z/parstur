import sqlite3
import sys
import io

# Set encoding for stdout
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

conn = sqlite3.connect('app_data.db')
cursor = conn.cursor()
cursor.execute("SELECT * FROM categories;")
for row in cursor.fetchall():
    print(row)
conn.close()
