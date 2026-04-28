import sqlite3

conn = sqlite3.connect('app_data.db')
c = conn.cursor()
c.execute("SELECT id, title, is_ignored FROM items WHERE title LIKE '%Убежище%' OR title LIKE '%Пацаны%' OR title LIKE '%Сорвиголова%'")
rows = c.fetchall()

print("ID | ИГНОР | НАЗВАНИЕ")
print("-" * 40)
for r in rows:
    print(f"{r[0]} | {r[2]} | {r[1]}")
conn.close()
