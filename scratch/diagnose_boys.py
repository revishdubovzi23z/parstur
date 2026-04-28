import sqlite3
conn = sqlite3.connect('app_data.db')
c = conn.cursor()

print('=== items (Пацаны/Boys) ===')
c.execute("SELECT id, title, year, kp_id, imdb_id, title_norm FROM items WHERE title LIKE '%Boys%' OR title LIKE '%аца%'")
for row in c.fetchall():
    print(row)

print()
print('=== user_ratings (Boys/Пацаны) ===')
c.execute("SELECT item_title, item_year, external_id, title_norm, original_title_norm FROM user_ratings WHERE item_title LIKE '%Boys%' OR item_title LIKE '%аца%' OR original_title LIKE '%Boys%' OR original_title LIKE '%аца%'")
for row in c.fetchall():
    print(row)

print()
print('=== item_search_names (boys/аца) ===')
c.execute("SELECT item_id, name_norm FROM item_search_names WHERE name_norm LIKE '%boys%' OR name_norm LIKE '%аца%'")
for row in c.fetchall():
    print(row)

conn.close()
