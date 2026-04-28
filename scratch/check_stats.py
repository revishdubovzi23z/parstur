import sqlite3

def get_stats():
    conn = sqlite3.connect('app_data.db')
    cursor = conn.cursor()
    
    video_cats = "(1, 4, 5, 16, 7)"
    
    # 1. Вообще без ID (ни КП, ни IMDb)
    cursor.execute(f"SELECT COUNT(*) FROM items WHERE category_id IN {video_cats} AND (imdb_id IS NULL OR imdb_id = '') AND (kp_id IS NULL OR kp_id = '')")
    no_any_id = cursor.fetchone()[0]
    
    # 2. Без КП ID
    cursor.execute(f"SELECT COUNT(*) FROM items WHERE category_id IN {video_cats} AND (kp_id IS NULL OR kp_id = '')")
    no_kp_id = cursor.fetchone()[0]
    
    # 3. Без IMDb ID
    cursor.execute(f"SELECT COUNT(*) FROM items WHERE category_id IN {video_cats} AND (imdb_id IS NULL OR imdb_id = '')")
    no_imdb_id = cursor.fetchone()[0]
    
    # 4. Без постеров
    cursor.execute(f"SELECT COUNT(*) FROM items WHERE category_id IN {video_cats} AND (poster_url IS NULL OR poster_url = '')")
    no_poster = cursor.fetchone()[0]
    
    # 5. Всего видео
    cursor.execute(f"SELECT COUNT(*) FROM items WHERE category_id IN {video_cats}")
    total = cursor.fetchone()[0]
    
    conn.close()
    
    print(f"Всего видео-карточек: {total}")
    print(f"Полностью без ID (ни КП, ни IMDb): {no_any_id}")
    print(f"Без Кинопоиск ID: {no_kp_id}")
    print(f"Без IMDb ID: {no_imdb_id}")
    print(f"Без постеров: {no_poster}")

if __name__ == '__main__':
    get_stats()
