import sqlite3
import requests
import time
import re
import sys
import io

# Форсируем UTF-8 для вывода в консоль Windows
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

def test_kinorium_match():
    conn = sqlite3.connect('app_data.db')
    cursor = conn.cursor()
    
    cursor.execute("SELECT id, title, imdb_id FROM items WHERE imdb_id IS NOT NULL AND imdb_id != '' LIMIT 10")
    items = cursor.fetchall()
    
    print(f"=== TEST: Kinorium ID Match for {len(items)} items ===\n")
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    found_count = 0
    for item_id, title, imdb_id in items:
        # Пытаемся найти через поиск по IMDb ID
        search_url = f"https://ru.kinorium.com/search/?q={imdb_id}"
        
        try:
            time.sleep(1.5)
            # Запрашиваем страницу, разрешаем редиректы
            resp = requests.get(search_url, headers=headers, allow_redirects=True, timeout=10)
            final_url = resp.url
            
            print(f"Movie: {title} | IMDb: {imdb_id}")
            print(f"  Result URL: {final_url}")
            
            match = re.search(r'kinorium\.com/(\d+)', final_url)
            if match:
                kinorium_id = match.group(1)
                found_count += 1
                print(f"  [OK] Found ID: {kinorium_id}")
            else:
                print(f"  [FAIL] No ID in URL")
                
        except Exception as e:
            print(f"  [ERROR] {e}")

    conn.close()
    print(f"\n=== TOTAL: Found {found_count} out of {len(items)} ===")

if __name__ == "__main__":
    test_kinorium_match()
