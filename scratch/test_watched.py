import sqlite3

conn = sqlite3.connect('app_data.db')
c = conn.cursor()

c.execute("SELECT external_id, title_norm, original_title_norm, item_year FROM user_ratings")
ratings = c.fetchall()

rated_ext_ids = set()
rated_names = {}

for ext_id, title_norm, orig_norm, item_year in ratings:
    if ext_id:
        rated_ext_ids.add(ext_id)
    for name in [title_norm, orig_norm]:
        if name:
            if name not in rated_names:
                rated_names[name] = []
            rated_names[name].append(item_year)

watched_ids = set()

# По ext_id
if rated_ext_ids:
    placeholders = ','.join('?'*len(rated_ext_ids))
    c.execute(f"SELECT id FROM items WHERE kp_id IN ({placeholders}) OR imdb_id IN ({placeholders})", list(rated_ext_ids)*2)
    for row in c.fetchall():
        watched_ids.add(row[0])

print(f"By ID: {len(watched_ids)} items hidden")

# По названиям
if rated_names:
    name_list = list(rated_names.keys())
    placeholders = ','.join('?'*len(name_list))
    c.execute(f"""
        SELECT sn.item_id, sn.name_norm, i.year 
        FROM item_search_names sn 
        JOIN items i ON sn.item_id = i.id
        WHERE sn.name_norm IN ({placeholders})
    """, name_list)
    for item_id, name_norm, item_year in c.fetchall():
        watched_ids.add(item_id)

print(f"By ID + title: {len(watched_ids)} items hidden")

# Проверяем "Пацаны" (item_id=121)
print(f"\nItem 121 (Пацаны/The Boys) in watched_ids: {121 in watched_ids}")
c.execute("SELECT title, year, kp_id, imdb_id FROM items WHERE id=121")
print("Item 121:", c.fetchone())

conn.close()
