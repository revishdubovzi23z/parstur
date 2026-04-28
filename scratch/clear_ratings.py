import sqlite3

def clear_tmdb_ratings():
    conn = sqlite3.connect('app_data.db')
    cursor = conn.cursor()
    
    # Зарубежные фильмы (1), Зарубежные сериалы (4), Наши фильмы (5), Наши сериалы (16), Мультипликация (7)
    VIDEO_CATEGORY_IDS = (1, 4, 5, 16, 7)
    ids_str = ",".join(map(str, VIDEO_CATEGORY_IDS))
    
    cursor.execute(f"""
        UPDATE items 
        SET imdb_rating = 0,
            is_metadata_fixed = 0
        WHERE category_id IN ({ids_str})
    """)
    
    count = cursor.rowcount
    conn.commit()
    conn.close()
    print(f"Очищено {count} записей. Рейтинг IMDb сброшен до 0.")

if __name__ == "__main__":
    clear_tmdb_ratings()
