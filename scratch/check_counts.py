import sqlite3
conn = sqlite3.connect('app_data.db')
cursor = conn.cursor()

# Всего видео-категорий
video_cats = (1, 4, 5, 16, 7)
ids_str = ",".join(map(str, video_cats))

# Считаем все не игнорируемые без KP ID
cursor.execute(f"SELECT COUNT(*) FROM items WHERE category_id IN ({ids_str}) AND is_ignored = 0 AND (kp_id IS NULL OR kp_id = '')")
total_missing_kp = cursor.fetchone()[0]

# Считаем те, что просмотрены (из user_ratings)
cursor.execute("SELECT imdb_id, kp_id, title_norm FROM user_ratings")
ratings = cursor.fetchall()

# Для простоты просто найдем сколько из missing_kp есть в user_ratings (по названию или ID)
# Но в reprocess_database мы берем ВСЕ.

print(f"Total missing KP (not ignored): {total_missing_kp}")

conn.close()
