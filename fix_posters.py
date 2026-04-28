import sqlite3
import time
import re
import sys
import os
from kinopoisk_client import KinopoiskClient
from kinopoisk_uz_client import KinopoiskUzClient
from poiskkino_client import PoiskKinoClient

class Logger:
    def __init__(self, filename="fix_log.txt"):
        self.terminal = sys.stdout
        self.log = open(filename, "a", encoding="utf-8") # Дописываем в лог

    def write(self, message):
        self.terminal.write(message)
        self.log.write(message)
        self.log.flush()

    def flush(self):
        self.terminal.flush()
        self.log.flush()

def get_db_stats(cursor, video_cats):
    cursor.execute(f"SELECT COUNT(*) FROM items WHERE category_id IN {video_cats} AND (poster_url IS NULL OR poster_url = '')")
    no_poster = cursor.fetchone()[0]
    cursor.execute(f"SELECT COUNT(*) FROM items WHERE category_id IN {video_cats} AND (kp_rating = 0 OR kp_rating IS NULL OR imdb_rating = 0 OR imdb_rating IS NULL)")
    no_ratings = cursor.fetchone()[0]
    cursor.execute(f"SELECT COUNT(*) FROM items WHERE category_id IN {video_cats}")
    total = cursor.fetchone()[0]
    return total, no_poster, no_ratings

def fix_metadata(api_type="tech"):
    sys.stdout = Logger("fix_log.txt")
    if api_type == "tech":
        client = KinopoiskClient()
        api_name = "Кинопоиск (Legacy/Tech)"
    elif api_type == "uz":
        client = KinopoiskUzClient()
        api_name = "Кинопоиск (Новый/UZ)"
    else:
        client = PoiskKinoClient()
        api_name = "PoiskKino (Dev)"
        
    print(f"\n\n=== ЗАПУСК: {api_name} ({time.strftime('%H:%M:%S')}) ===")
    video_categories = [1, 4, 5, 16, 7]
    cats_str = "(1, 4, 5, 16, 7)"
    
    with sqlite3.connect("app_data.db", timeout=30.0) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Сбрасываем старые TMDB-оценки если они есть (только при первом запуске)
        cursor.execute(f"""
            UPDATE items SET kp_rating = 0, imdb_rating = 0, is_metadata_fixed = 0
            WHERE category_id IN {cats_str}
            AND kp_rating > 0 AND imdb_rating > 0 AND kp_rating = imdb_rating
        """)
        conn.commit()

        # Ищем ВСЕ видео, где чего-то не хватает (постера или оценок)
        # Исключаем те, которые уже ПРОВЕРЕНЫ именно этим API
        check_col = f"checked_{api_type}"
        cursor.execute(f"""
            SELECT id, title, year, poster_url, kp_rating, imdb_rating, kp_id, imdb_id, description
            FROM items 
            WHERE category_id IN {cats_str} 
            AND (
                poster_url IS NULL OR poster_url = '' 
                OR kp_rating = 0 OR kp_rating IS NULL 
                OR imdb_rating = 0 OR imdb_rating IS NULL
            )
            AND is_ignored = 0
            AND is_metadata_fixed = 0
            AND {check_col} = 0
            ORDER BY id DESC
            LIMIT 300
        """)
        items = cursor.fetchall()
        
        print(f"Найдено {len(items)} релизов с пропусками данных.")
        if not items:
            print("Все данные в порядке!")
        else:
            for idx, item_data in enumerate(items, 1):
                if client.is_limited:
                    print(f"\n[!] Лимит API исчерпан. Остановка.")
                    break
                
                item_id = item_data['id']
                title = item_data['title']
                year = item_data['year']
                kp_id = item_data['kp_id']
                imdb_id = item_data['imdb_id']
                
                # Аварийный поиск года если его нет
                if not year or year == 0:
                    cursor.execute("SELECT torrent_title FROM releases WHERE item_id = ? LIMIT 1", (item_id,))
                    rel_row = cursor.fetchone()
                    if rel_row:
                        year_match = re.search(r'\((\d{4})\)', rel_row[0])
                        if year_match:
                            year = int(year_match.group(1))
                            cursor.execute("UPDATE items SET year = ? WHERE id = ?", (year, item_id))
                
                # Глубокая очистка названия для поиска
                search_title = title.split(' / ')[0].split('/')[0]
                search_title = re.sub(r'\(.*?\)', '', search_title)
                search_title = re.sub(r'\[.*?\]', '', search_title)
                tags = ['SATRip', 'Web-DL', 'BDRip', '1080p', '720p', '4K', 'HDR', 'S01', 'S02', 'L1', 'L2']
                for tag in tags:
                    search_title = re.sub(f'(?i){tag}', '', search_title)
                search_title = search_title.strip()
                
                print(f"\n[{idx}/{len(items)}] 🎬 {search_title} ({year})")
                
                needed = []
                if not item_data['poster_url']: needed.append("постер")
                if not item_data['kp_rating']: needed.append("КП")
                if not item_data['imdb_rating']: needed.append("IMDb")
                print(f"  📋 Нужно: {', '.join(needed)}")

                data = None
                # ПРИОРИТЕТ: Если есть KP ID и API поддерживает поиск по ID
                if kp_id and hasattr(client, 'get_by_id'):
                    print(f"  🎯 Прямой запрос по ID: {kp_id}")
                    data = client.get_by_id(kp_id)
                
                # Если нет ID или поиск по нему не дал результата - ищем по названию
                if not data:
                    print(f"  🔍 Поиск через API по названию: {search_title}...")
                    data = client.search_movie(search_title, year)
                
                if data:
                    kp_rating = data.get("kp_rating", 0.0)
                    imdb_rating = data.get("imdb_rating", 0.0)
                    new_poster = data.get("poster_url", "")
                    desc = data.get("description", "")
                    new_imdb_id = data.get("imdb_id", "")
                    
                    # Обновляем только пустые поля
                    cursor.execute("""
                        UPDATE items 
                        SET kp_rating = CASE WHEN kp_rating = 0 OR kp_rating IS NULL THEN ? ELSE kp_rating END,
                            imdb_rating = CASE WHEN imdb_rating = 0 OR imdb_rating IS NULL THEN ? ELSE imdb_rating END,
                            poster_url = CASE WHEN poster_url IS NULL OR poster_url = '' THEN ? ELSE poster_url END,
                            description = CASE WHEN description IS NULL OR description = '' THEN ? ELSE description END,
                            imdb_id = CASE WHEN imdb_id IS NULL OR imdb_id = '' THEN ? ELSE imdb_id END
                        WHERE id = ?
                    """, (kp_rating, imdb_rating, new_poster, desc, new_imdb_id, item_id))
                    
                    # Проверяем, стали ли данные полными
                    cursor.execute("SELECT kp_rating, imdb_rating, poster_url, description FROM items WHERE id = ?", (item_id,))
                    row_check = cursor.fetchone()
                    if row_check['kp_rating'] > 0 and row_check['imdb_rating'] > 0 and row_check['poster_url'] and row_check['description']:
                        cursor.execute("UPDATE items SET is_metadata_fixed = 1 WHERE id = ?", (item_id,))
                    
                    conn.commit()
                    
                    conn.commit()
                    
                    found_parts = []
                    if kp_rating > 0 and (not item_data['kp_rating']): found_parts.append(f"✨ Рейтинг КП: {kp_rating}")
                    if imdb_rating > 0 and (not item_data['imdb_rating']): found_parts.append(f"✨ Рейтинг IMDb: {imdb_rating}")
                    if new_poster and (not item_data['poster_url']): found_parts.append("🖼️ Постер добавлен")
                    if desc and (not item_data['description']): found_parts.append("📝 Описание получено")
                    
                    if found_parts:
                        for p in found_parts: print(f"    {p}")
                        print(f"  ✅ Данные обновлены")
                    else:
                        print(f"  💎 Новых данных не найдено.")
                else:
                    print(f"  ❌ API не вернул данных.")

                # В любом случае помечаем, что ЭТОТ API проверил эту карточку
                if not client.is_limited:
                    cursor.execute(f"UPDATE items SET {check_col} = 1 WHERE id = ?", (item_id,))
                    conn.commit()
                
                time.sleep(0.5)

        # Финальная статистика
        total, no_p, no_r = get_db_stats(cursor, cats_str)
        print(f"\n=== ИТОГОВАЯ СТАТИСТИКА ===")
        print(f"Всего видео: {total}")
        print(f"Осталось БЕЗ постеров: {no_p}")
        print(f"Осталось БЕЗ оценок: {no_r}")
        print(f"===========================\n")

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "tech"
    fix_metadata(mode)
