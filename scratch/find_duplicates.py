import sqlite3
conn = sqlite3.connect('app_data.db')
cursor = conn.cursor()

print("--- Дубликаты по Kinopoisk ID ---")
cursor.execute("""
    SELECT kp_id, COUNT(*) as count, GROUP_CONCAT(id || ': ' || title || ' (' || year || ')') as items
    FROM items 
    WHERE kp_id IS NOT NULL AND kp_id != ''
    GROUP BY kp_id 
    HAVING count > 1
""")
for row in cursor.fetchall():
    print(f"KP ID {row[0]} ({row[1]} шт):")
    for item in row[2].split(','):
        print(f"  - {item}")

print("\n--- Дубликаты по IMDb ID ---")
cursor.execute("""
    SELECT imdb_id, COUNT(*) as count, GROUP_CONCAT(id || ': ' || title || ' (' || year || ')') as items
    FROM items 
    WHERE imdb_id IS NOT NULL AND imdb_id != ''
    GROUP BY imdb_id 
    HAVING count > 1
""")
for row in cursor.fetchall():
    print(f"IMDb ID {row[0]} ({row[1]} шт):")
    for item in row[2].split(','):
        print(f"  - {item}")

conn.close()
