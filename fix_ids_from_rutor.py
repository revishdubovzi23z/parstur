import sqlite3
import requests
import re
import time

def fix_from_rutor():
    conn = sqlite3.connect('app_data.db')
    cursor = conn.cursor()
    
    # Берем фильмы, у которых нет KP ID (external_id)
    cursor.execute("""
        SELECT items.id, items.title, MIN(releases.rutor_id) 
        FROM items 
        JOIN releases ON items.id = releases.item_id
        WHERE items.category_id IN (1, 4, 5, 16, 7)
        AND (items.external_id IS NULL OR items.external_id = '')
        GROUP BY items.id
    """)
    items = cursor.fetchall()
    
    print(f"=== ЗАПУСК: Извлечение точных ID с Рутора для {len(items)} фильмов ===")
    
    updated_count = 0
    
    for idx, (item_id, title, rutor_id) in enumerate(items, 1):
        rel_url = f"http://rutor.info/torrent/{rutor_id}"
        print(f"[{idx}/{len(items)}] Проверяем: {title}...", end="\r")
        
        try:
            time.sleep(0.5) # Бережем Рутор
            resp = requests.get(rel_url, timeout=10)
            if resp.status_code == 200:
                kp_id = ""
                imdb_id = ""
                kinorium_id = ""
                
                # Ищем KP ID
                kp_match = re.search(r'kinopoisk\.ru/rating/(\d+)\.gif', resp.text)
                if not kp_match:
                    kp_match = re.search(r'kinopoisk\.ru/level/1/film/(\d+)', resp.text)
                if kp_match:
                    kp_id = kp_match.group(1)
                
                # Ищем IMDb ID
                imdb_match = re.search(r'imdb\.com/title/(tt\d+)', resp.text)
                if imdb_match:
                    imdb_id = imdb_match.group(1)
                
                if kp_id or imdb_id:
                    cursor.execute("""
                        UPDATE items 
                        SET external_id = CASE WHEN external_id IS NULL OR external_id = '' THEN ? ELSE external_id END,
                            imdb_id = CASE WHEN imdb_id IS NULL OR imdb_id = '' THEN ? ELSE imdb_id END
                        WHERE id = ?
                    """, (kp_id, imdb_id, item_id))
                    conn.commit()
                    updated_count += 1
                    print(f"[{idx}/{len(items)}] {title} -> KP:{kp_id} IMDb:{imdb_id} ✅")
            
        except Exception as e:
            print(f"\nОшибка при парсинге {rel_url}: {e}")
            time.sleep(2)

    conn.close()
    print(f"\n=== ГОТОВО! Уточнено ID для {updated_count} фильмов ===")

if __name__ == "__main__":
    fix_from_rutor()
