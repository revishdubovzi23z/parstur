import sqlite3
import re
import unicodedata

def fix_encodings():
    conn = sqlite3.connect('app_data.db')
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute("SELECT id, title FROM items")
    items = cursor.fetchall()
    
    updated_count = 0
    for item in items:
        item_id = item['id']
        title = item['title']
        
        # Генерируем нормализованное название заново
        # Удаляем год в скобках
        t_clean = re.sub(r'\(.*?\)', '', title)
        t_clean = re.sub(r'\[.*?\]', '', t_clean)
        
        # Разбиваем по слэшам и берем все части
        parts = [p.strip() for p in t_clean.split('/') if p.strip()]
        
        search_names = []
        for p in parts:
            # Нормализуем каждую часть
            norm = unicodedata.normalize('NFC', p).lower().strip()
            if norm:
                search_names.append(norm)
        
        if search_names:
            main_norm = search_names[0]
            
            # Обновляем основное нормализованное имя
            cursor.execute("UPDATE items SET title_norm = ? WHERE id = ?", (main_norm, item_id))
            
            # Обновляем альтернативные имена
            cursor.execute("DELETE FROM item_search_names WHERE item_id = ?", (item_id,))
            for sn in set(search_names):
                cursor.execute("INSERT INTO item_search_names (item_id, name_norm) VALUES (?, ?)", (item_id, sn))
            
            updated_count += 1
            
    conn.commit()
    conn.close()
    print(f"Обновлено нормализованных имен: {updated_count}")

if __name__ == '__main__':
    fix_encodings()
