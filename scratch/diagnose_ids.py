import sqlite3
conn = sqlite3.connect('app_data.db')
c = conn.cursor()

print('=== Пацаны в items ===')
c.execute("SELECT id, title, year, kp_id, imdb_id FROM items WHERE id=121")
row = c.fetchone()
print(f"  id={row[0]}, title={row[1]}, year={row[2]}")
print(f"  kp_id={row[3]!r}, imdb_id={row[4]!r}")

print()
print('=== The Boys в user_ratings ===')
c.execute("SELECT item_title, item_year, external_id, service FROM user_ratings WHERE item_title LIKE '%Boys%' AND item_title NOT LIKE '%Beach%' AND item_title NOT LIKE '%Nickel%' AND item_title NOT LIKE '%Boat%' AND item_title NOT LIKE '%Betrayed%' AND item_title NOT LIKE '%Bad%'")
for row in c.fetchall():
    print(f"  title={row[0]!r}, year={row[1]}, external_id={row[2]!r}, service={row[3]!r}")

print()
print('=== Проверяем совпадение ID ===')
items_kp = '460586'
items_imdb = 'tt1837341'
c.execute("SELECT item_title, external_id FROM user_ratings WHERE external_id=?", (items_kp,))
print(f"  По kp_id={items_kp}:", c.fetchall())
c.execute("SELECT item_title, external_id FROM user_ratings WHERE external_id=?", (items_imdb,))
print(f"  По imdb_id={items_imdb}:", c.fetchall())

conn.close()
