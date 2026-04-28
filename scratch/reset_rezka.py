import sqlite3

def reset_rezka_flags():
    conn = sqlite3.connect('app_data.db')
    cursor = conn.cursor()
    
    query = """
        UPDATE items 
        SET checked_rezka = 0 
        WHERE checked_rezka = 1 
        AND (
            kp_id IS NULL OR kp_id = '' OR 
            imdb_id IS NULL OR imdb_id = '' OR 
            kp_rating = 0 OR kp_rating IS NULL OR 
            imdb_rating = 0 OR imdb_rating IS NULL
        )
        AND is_ignored = 0
    """
    
    cursor.execute(query)
    count = cursor.rowcount
    conn.commit()
    conn.close()
    
    print(f"Флаг 'checked_rezka' сброшен для {count} объектов, у которых не хватает данных.")

if __name__ == "__main__":
    reset_rezka_flags()
