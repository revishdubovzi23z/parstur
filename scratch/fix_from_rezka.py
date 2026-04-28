import sqlite3
import re
import unicodedata

def fix_from_rezka_urls():
    conn = sqlite3.connect('app_data.db')
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Ищем тех, у кого есть ссылка на резку, но битое название
    # (Битое название обычно содержит спецсимволы или пустые строки в нормализации)
    cursor.execute("SELECT id, title, rezka_url FROM items WHERE rezka_url IS NOT NULL AND rezka_url != ''")
    items = cursor.fetchall()
    
    updated_count = 0
    for item in items:
        item_id = item['id']
        url = item['rezka_url']
        
        # Извлекаем транслит из URL
        # https://rezka.ag/films/drama/63845-rodnaya-dusha-2023.html
        match = re.search(r'/\d+-(.*?)-\d+\.html', url)
        if not match:
            match = re.search(r'/\d+-(.*?)\.html', url)
            
        if match:
            slug = match.group(1).replace('-', ' ')
            # Если в базе совсем мусор, попробуем хотя бы по слагу найти
            # Но лучше восстановить title_norm
            norm = slug.lower().strip()
            
            # Обновляем title_norm и добавляем в алиасы
            cursor.execute("UPDATE items SET title_norm = ? WHERE id = ?", (norm, item_id))
            cursor.execute("INSERT OR IGNORE INTO item_search_names (item_id, name_norm) VALUES (?, ?)", (item_id, norm))
            updated_count += 1
            
    conn.commit()
    conn.close()
    print(f"Восстановлено имен по ссылкам Rezka: {updated_count}")

if __name__ == '__main__':
    fix_from_rezka_urls()
