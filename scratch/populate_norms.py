import sqlite3
import re
import unicodedata

def get_names(t):
    if not t: return []
    # Удаляем скобки и лишнее
    t = re.sub(r'\(.*?\)', '', t)
    t = re.sub(r'\[.*?\]', '', t)
    t = re.sub(r'(?i)SATRip|Web-DL|BDRip|1080p|720p|4K|HDR', '', t)
    
    # Разбиваем по слешу и возвращаем все части
    parts = [p.strip() for p in t.split('/') if p.strip()]
    # Также разбиваем по ' / ' если вдруг слеш без пробелов был обработан выше
    final_parts = []
    for p in parts:
        final_parts.extend([pp.strip() for pp in p.split(' / ') if pp.strip()])
    
    return list(set(unicodedata.normalize('NFC', p).lower() for p in final_parts))

def populate():
    conn = sqlite3.connect('app_data.db')
    cursor = conn.cursor()
    
    print("Clearing old search names...")
    cursor.execute("DELETE FROM item_search_names")
    
    print("Populating item_search_names for items...")
    cursor.execute("SELECT id, title FROM items")
    rows = cursor.fetchall()
    for row_id, title in rows:
        names = get_names(title)
        for name in names:
            cursor.execute("INSERT INTO item_search_names (item_id, name_norm) VALUES (?, ?)", (row_id, name))
        # Также сохраняем основной title_norm в самой таблице items для совместимости
        if names:
            cursor.execute("UPDATE items SET title_norm = ? WHERE id = ?", (names[0], row_id))
    
    print("Populating title_norm for user_ratings...")
    cursor.execute("SELECT item_title, item_year, original_title FROM user_ratings")
    rows = cursor.fetchall()
    for item_title, item_year, orig_title in rows:
        # Для оценок просто берем нормализованные имена
        names = get_names(item_title)
        norm = names[0] if names else None
        
        orig_names = get_names(orig_title) if orig_title else []
        orig_norm = orig_names[0] if orig_names else None
        
        cursor.execute("UPDATE user_ratings SET title_norm = ?, original_title_norm = ? WHERE item_title = ? AND (item_year = ? OR item_year IS NULL)", 
                      (norm, orig_norm, item_title, item_year))
        
    conn.commit()
    conn.close()
    print("Done!")

if __name__ == "__main__":
    populate()
