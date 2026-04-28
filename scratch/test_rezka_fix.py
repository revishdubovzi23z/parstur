import sqlite3
import re
import time
import base64
import urllib.parse
import requests
from HdRezkaApi import HdRezkaApi, HdRezkaSearch

def test_rezka_fix(item_id):
    conn = sqlite3.connect('app_data.db')
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM items WHERE id = ?", (item_id,))
    item = cursor.fetchone()
    if not item:
        print("Item not found")
        return
        
    url = item['rezka_url']
    print(f"Testing for: {item['title']} ({item['year']})")
    print(f"Rezka URL: {url}")
    
    rezka = HdRezkaApi(url)
    soup = rezka.soup
    
    rate_blocks = soup.find_all(class_=re.compile(r'b-post__info_rates'))
    print(f"Found {len(rate_blocks)} rate blocks")
    
    found_kp_id = None
    found_imdb_id = None
    found_kp_rating = 0
    found_imdb_rating = 0
    
    for block in rate_blocks:
        block_text = block.text.lower()
        val_tag = block.find('span', class_='num')
        if not val_tag: val_tag = block.find(['b', 'span'], class_='bold')
        if not val_tag: val_tag = block.find('b')
        
        if val_tag:
            try:
                clean_val = val_tag.text.strip().replace(',', '.')
                val = float(clean_val)
                if 'кинопоиск' in block_text or 'kp' in block_text:
                    found_kp_rating = val
                elif 'imdb' in block_text:
                    found_imdb_rating = val
            except: pass

        for a in block.find_all('a', href=re.compile(r'/help/')):
            try:
                href_parts = a['href'].strip('/').split('/')
                b64_part = href_parts[-1]
                # Rezka: сначала URL-encode, потом Base64. Значит декодируем наоборот.
                raw_decoded = base64.b64decode(b64_part).decode('utf-8', errors='ignore')
                decoded_url = urllib.parse.unquote(raw_decoded)
                print(f"  Decoded URL: {decoded_url}")
                
                if 'kinopoisk.ru' in decoded_url:
                    mid = re.search(r'film/(\d+)', decoded_url)
                    if mid: found_kp_id = mid.group(1)
                
                if 'imdb.com' in decoded_url:
                    mid = re.search(r'title/(tt\d+)', decoded_url)
                    if mid: found_imdb_id = mid.group(1)
            except: pass
            
    print(f"Results: KP_ID={found_kp_id}, IMDb_ID={found_imdb_id}, KP_Rating={found_kp_rating}, IMDb_Rating={found_imdb_rating}")
    
    if found_kp_id or found_imdb_id:
        cursor.execute("UPDATE items SET kp_id = ?, imdb_id = ?, kp_rating = ?, imdb_rating = ? WHERE id = ?",
                     (found_kp_id or item['kp_id'], found_imdb_id or item['imdb_id'], found_kp_rating, found_imdb_rating, item_id))
        conn.commit()
        print("Database updated!")

    conn.close()

if __name__ == "__main__":
    test_rezka_fix(1621)
