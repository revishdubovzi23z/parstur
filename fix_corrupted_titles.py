import sys
import sqlite3
import requests
from bs4 import BeautifulSoup
import re
import time
import unicodedata
import os

LOG_FILE = 'fix_titles_log.txt'

def log(msg):
    try:
        timestamp = time.strftime('%H:%M:%S')
        line = f"[{timestamp}] {msg}\n"
        with open(LOG_FILE, 'ab') as f:
            f.write(line.encode('utf-8'))
    except Exception as e:
        print(f"Logging error: {e}")

def clean_display_title(full_title):
    # Очищаем заголовок от технического мусора
    t = full_title
    year_match = re.search(r'\((\d{4})\)', t)
    year_str = f" ({year_match.group(1)})" if year_match else ""
    t = re.split(r'SATRip|Web-DL|WEBRip|WEB-Rip|BDRip|BDRemux|HDTV|Rip|1080p|720p|4K|HDR|HEVC|AVC|MVO|DUB|VO|от\s+|\|', t, flags=re.IGNORECASE)[0]
    t = re.sub(r'\(.*?\)|\[.*?\]', '', t).strip()
    clean = f"{t}{year_str}".strip().replace('  ', ' ')
    return clean.replace('x', 'х').replace('X', 'Х')

def normalize_title(t):
    t = t.lower()
    t = t.replace('x', 'х') 
    t = re.sub(r'[^a-zа-я0-9\s]', '', t)
    return ' '.join(t.split())

def fix_titles():
    conn = sqlite3.connect('app_data.db')
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Исправляем ВСЕ видео-карточки, чтобы они стали красивыми
    # (Не только битые, но и слишком длинные)
    cursor.execute("""
        SELECT DISTINCT i.id, i.title, r.rutor_id 
        FROM items i
        JOIN releases r ON i.id = r.item_id
        WHERE i.category_id IN (1, 4, 5, 16, 7)
    """)
    items = cursor.fetchall()
    
    total = len(items)
    log(f"=== ЗАПУСК ПРИЧЕСЫВАНИЯ ИМЕН ({total} шт) ===")
    
    headers = {"User-Agent": "Mozilla/5.0"}
    
    for idx, item in enumerate(items, 1):
        item_id = item['id']
        rutor_id = item['rutor_id']
        old_title = item['title']
        
        url = f"http://rutor.info/torrent/{rutor_id}"
        
        try:
            res = requests.get(url, headers=headers, timeout=10)
            if res.status_code == 200:
                soup = BeautifulSoup(res.content, 'html.parser')
                h1 = soup.find('h1')
                if h1:
                    full_title = h1.text.strip()
                    display_title = clean_display_title(full_title)
                    
                    # Генерируем нормализованное имя для поиска
                    t_clean = re.sub(r'\(.*?\)', '', full_title)
                    t_clean = re.sub(r'\[.*?\]', '', t_clean)
                    parts = [p.strip() for p in t_clean.split('/') if p.strip()]
                    
                    if parts:
                        main_norm = normalize_title(parts[0])
                        
                        if display_title != old_title:
                            log(f"[{idx}/{total}] FIXED: {display_title}")
                            cursor.execute("UPDATE items SET title = ?, title_norm = ? WHERE id = ?", (display_title, main_norm, item_id))
                            
                            # Обновляем алиасы
                            cursor.execute("DELETE FROM item_search_names WHERE item_id = ?", (item_id,))
                            for p in parts:
                                sn = normalize_title(p)
                                if sn:
                                    cursor.execute("INSERT OR IGNORE INTO item_search_names (item_id, name_norm) VALUES (?, ?)", (item_id, sn))
                            conn.commit()
                        else:
                            # Даже если имя уже ок, проверим алиасы
                            pass
            else:
                log(f"[{idx}/{total}] SKIP (Error {res.status_code})")
                
        except Exception as e:
            log(f"[{idx}/{total}] ERROR: {str(e)}")
            
        time.sleep(0.5) # Немного быстрее, так как это важная правка

    conn.close()
    log("=== ПРИЧЕСЫВАНИЕ ЗАВЕРШЕНО ===")

if __name__ == '__main__':
    fix_titles()
