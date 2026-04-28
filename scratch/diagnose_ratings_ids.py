import sqlite3
conn = sqlite3.connect('app_data.db')
c = conn.cursor()

print('=== Структура таблицы user_ratings ===')
c.execute("PRAGMA table_info(user_ratings)")
for col in c.fetchall():
    print(col)

print()
print('=== Примеры записей из KP (service=kp) ===')
c.execute("SELECT item_title, item_year, external_id, service FROM user_ratings WHERE service='kp' LIMIT 5")
for row in c.fetchall():
    print(row)

print()
print('=== Примеры записей из IMDb (service=imdb) ===')
c.execute("SELECT item_title, item_year, external_id, service FROM user_ratings WHERE service='imdb' LIMIT 5")
for row in c.fetchall():
    print(row)

print()
print('=== Статистика по external_id ===')
c.execute("SELECT service, COUNT(*), SUM(CASE WHEN external_id IS NULL OR external_id='' THEN 1 ELSE 0 END) as no_id FROM user_ratings GROUP BY service")
for row in c.fetchall():
    print(f"  service={row[0]}: total={row[1]}, БЕЗ ID={row[2]}")

conn.close()
