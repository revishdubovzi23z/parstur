import sqlite3
import time
from tmdb_client import TMDBClient

def fetch_all_imdb_ids():
    conn = sqlite3.connect('app_data.db')
    cursor = conn.cursor()
    client = TMDBClient()

    # Берем только категорию "Видео" (1, 4, 5, 16, 7) и где нет imdb_id
    cursor.execute("""
        SELECT id, title, year 
        FROM items 
        WHERE category_id IN (1, 4, 5, 16, 7) 
        AND (imdb_id IS NULL OR imdb_id = '')
    """)
    items = cursor.fetchall()
    
    import sys
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

    print(f"=== ЗАПУСК: Массовое получение IMDb ID ({len(items)} объектов) ===")
    
    updated_count = 0
    
    for idx, (item_id, title, year) in enumerate(items, 1):
        msg = f"[{idx}/{len(items)}] Ищем: {title} ({year or '?'})"
        print(msg, end="\r")
        with open("imdb_fix.log", "a", encoding="utf-8") as f:
            f.write(msg + "\n")
        
        try:
            # Используем наш обновленный метод
            data = client.search_movie(title, year)
            
            if data and data.get("imdb_id"):
                imdb_id = data["imdb_id"]
                cursor.execute("UPDATE items SET imdb_id = ? WHERE id = ?", (imdb_id, item_id))
                conn.commit()
                updated_count += 1
                res_msg = f"  -> Найдено: {imdb_id}"
                print(res_msg)
                with open("imdb_fix.log", "a", encoding="utf-8") as f:
                    f.write(res_msg + "\n")
            else:
                # Если с годом не нашли, пробуем без года (на всякий случай)
                if year:
                    data = client.search_movie(title, None)
                    if data and data.get("imdb_id"):
                        imdb_id = data["imdb_id"]
                        cursor.execute("UPDATE items SET imdb_id = ? WHERE id = ?", (imdb_id, item_id))
                        conn.commit()
                        updated_count += 1
                        print(f"[{idx}/{len(items)}] {title} -> {imdb_id} ✅ (без года)")
                
        except Exception as e:
            print(f"\nОшибка на {title}: {e}")
            time.sleep(2)

    conn.close()
    print(f"\n=== ГОТОВО! Обновлено: {updated_count} фильмов ===")

if __name__ == "__main__":
    fetch_all_imdb_ids()
