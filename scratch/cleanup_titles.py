import sqlite3
import re
import sys
import io

# Обеспечиваем вывод в UTF-8
if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

def clean_title_keep_seasons(title):
    if not title: return title
    
    # Сохраняем сезоны (например, [S01], [S01-02], [S05])
    seasons = re.findall(r'\[S\d+(?:-\d+)?\]', title, flags=re.IGNORECASE)
    for idx, s in enumerate(seasons):
        title = title.replace(s, f"__SEASON_{idx}__")
    
    # 1. Удаляем технические теги качества
    # Отсекаем всё после первого встреченного тега
    title = re.split(r'(?i)SATRip|Web-DL|WEBRip|WEB-Rip|BDRip|BDRemux|HDTV|Rip|2160p|1080p|720p|4K|HDR|HEVC|AVC|MVO|DUB|VO|от\s+|\|', title)[0]
    
    # 2. Удаляем лишние приписки типа Portable, Deluxe Edition и т.д.
    title = re.sub(r'(?i)PC\s*\|\s*Portable.*', '', title)
    title = re.sub(r'(?i)Deluxe Edition', '', title)
    title = re.sub(r'(?i)Premium Edition', '', title)
    
    # 3. Чистим края и скобки (но не сезоны)
    title = title.strip().rstrip('-').rstrip('|').strip()
    
    # Убираем год в скобках из названия, если он там есть (так как год есть в отдельной колонке)
    title = re.sub(r'\(\d{4}\)', '', title).strip()
    
    # 4. Возвращаем сезоны на место
    for idx, s in enumerate(seasons):
        title = title.replace(f"__SEASON_{idx}__", f" {s.upper()}")
        
    # Убираем двойные пробелы
    title = re.sub(r'\s+', ' ', title).strip()
    
    return title

def run_cleanup():
    conn = sqlite3.connect('app_data.db')
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Список того, что считаем мусором для поиска
    trash_pattern = r'(?i)SATRip|Web-DL|WEBRip|WEB-Rip|BDRip|BDRemux|HDTV|Rip|2160p|1080p|720p|4K|HDR|HEVC|AVC|MVO|DUB|VO'
    
    cursor.execute("SELECT id, title, year, category_id FROM items")
    items = cursor.fetchall()
    
    success_count = 0
    conflict_count = 0
    
    print("=== ЗАПУСК ОЧИСТКИ (СЕЗОНЫ ОСТАЮТСЯ) ===\n")
    
    for item in items:
        item_id = item['id']
        title = item['title']
        year = item['year']
        cat_id = item['category_id']
        
        if not title: continue
        
        if re.search(trash_pattern, title) or "Portable" in title or "Edition" in title:
            new_title = clean_title_keep_seasons(title)
            if new_title != title:
                # Проверяем на конфликт UNIQUE(title, year, category_id)
                cursor.execute("SELECT id FROM items WHERE title = ? AND year = ? AND category_id = ? AND id != ?", 
                             (new_title, year, cat_id, item_id))
                conflict = cursor.fetchone()
                
                if conflict:
                    # Если есть конфликт - переносим раздачи к чистому объекту и удаляем этот
                    clean_id = conflict['id']
                    print(f"ID: {item_id} -> СЛИЯНИЕ С {clean_id} (из-за конфликта имен)")
                    cursor.execute("UPDATE releases SET item_id = ? WHERE item_id = ?", (clean_id, item_id))
                    cursor.execute("DELETE FROM items WHERE id = ?", (item_id,))
                    cursor.execute("DELETE FROM item_search_names WHERE item_id = ?", (item_id,))
                    conflict_count += 1
                else:
                    # Конфликта нет - просто обновляем
                    cursor.execute("UPDATE items SET title = ? WHERE id = ?", (new_title, item_id))
                    success_count += 1
                    # print(f"ID: {item_id} | ОБНОВЛЕНО: {new_title}")
    
    conn.commit()
    print(f"\nУспешно очищено: {success_count}")
    print(f"Слито дубликатов: {conflict_count}")
    print("База данных приведена в порядок.")
    
    conn.close()

if __name__ == "__main__":
    run_cleanup()
