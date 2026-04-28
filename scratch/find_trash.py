import sqlite3
import re
import sys
import io

# Обеспечиваем вывод в UTF-8
if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

def find_trash_titles():
    conn = sqlite3.connect('app_data.db')
    cursor = conn.cursor()
    
    # Регулярка для поиска технического мусора
    # Ищем: Rip, DL, 1080, 720, 4K, HDR, HEVC, AVC, MVO, DUB, VO, S01, E01 и т.д.
    trash_pattern = r'(?i)SATRip|Web-DL|WEBRip|WEB-Rip|BDRip|BDRemux|HDTV|Rip|1080p|720p|4K|HDR|HEVC|AVC|MVO|DUB|VO|S\d{2}|E\d{2}'
    
    cursor.execute("SELECT id, title FROM items")
    items = cursor.fetchall()
    
    found = []
    for item_id, title in items:
        if title and re.search(trash_pattern, title):
            found.append((item_id, title))
    
    print(f"=== Найдено объектов с техническим мусором: {len(found)} ===\n")
    for i, (item_id, title) in enumerate(found, 1):
        try:
            print(f"{i}. [ID: {item_id}] {title}")
        except:
            print(f"{i}. [ID: {item_id}] (ошибка вывода названия)")
    
    conn.close()

if __name__ == "__main__":
    find_trash_titles()
