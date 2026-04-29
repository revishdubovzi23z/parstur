import sqlite3
import sys
import time
import requests
import re
import os
import json
from tmdb_client import TMDBClient
from poiskkino_client import PoiskKinoClient
from kinopoisk_client import KinopoiskClient
from rezka_sync import RezkaParser

def report_progress(current, total, status_key="single_update"):
    try:
        with open(f"progress_{status_key}.json", "w") as f:
            json.dump({"current": current, "total": total}, f)
    except: pass

def update_single_item(item_id):
    conn = sqlite3.connect("app_data.db", timeout=30.0)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM items WHERE id = ?", (item_id,))
    item = cursor.fetchone()
    if not item:
        print(f"Item {item_id} not found.")
        return

    print(f"=== ОБНОВЛЕНИЕ МЕТАДАННЫХ ДЛЯ ID {item_id} ===")
    print(f"🎬 {item['title']} ({item['year']})")

    # 1. Попытка достать ID с Rutor (если их нет)
    kp_id = item['kp_id']
    imdb_id = item['imdb_id']
    
    if not kp_id or not imdb_id:
        cursor.execute("SELECT rutor_id FROM releases WHERE item_id = ?", (item_id,))
        rels = cursor.fetchall()
        for rel in rels:
            if kp_id and imdb_id: break
            try:
                url = f"http://rutor.info/torrent/{rel['rutor_id']}"
                print(f"  🔍 Проверка Rutor: {url}")
                resp = requests.get(url, timeout=15)
                if resp.status_code == 200:
                    if not kp_id:
                        m = re.search(r'kinopoisk\.ru/rating/(\d+)\.gif', resp.text)
                        if not m: m = re.search(r'kinopoisk\.ru/(?:film|series)/(\d+)', resp.text)
                        if m: kp_id = m.group(1); print(f"    ✅ Нашел KP ID: {kp_id}")
                    if not imdb_id:
                        m = re.search(r'imdb\.com/title/(tt\d+)', resp.text)
                        if m: imdb_id = m.group(1); print(f"    ✅ Нашел IMDb ID: {imdb_id}")
            except: pass

    # 2. TMDB
    tmdb = TMDBClient()
    tmdb_data = None
    if imdb_id:
        tmdb_data = tmdb.find_by_imdb_id(imdb_id)
    if not tmdb_data:
        search_title = item['title'].split(' / ')[0].split('/')[0].strip()
        tmdb_data = tmdb.search_movie(search_title, item['year'])
    
    if tmdb_data:
        print("  ✅ TMDB данные получены")
        poster = tmdb_data.get("poster_url", item['poster_url'])
        desc = tmdb_data.get("description", item['description'])
        if not imdb_id: imdb_id = tmdb_data.get("imdb_id", imdb_id)
        
        cursor.execute("""
            UPDATE items SET poster_url = ?, description = ?, imdb_id = ?, kp_id = ?
            WHERE id = ?
        """, (poster, desc, imdb_id, kp_id, item_id))
        conn.commit()

    # 3. PoiskKino (для рейтингов)
    poisk = PoiskKinoClient()
    print("  🔍 Запрос к PoiskKino...")
    pk_data = None
    if kp_id: pk_data = poisk.get_by_id(kp_id)
    if not pk_data: pk_data = poisk.search_movie(item['title'].split(' / ')[0], item['year'])
    
    if pk_data:
        print("  ✅ PoiskKino данные получены")
        kp_rating = pk_data.get("kp_rating", 0.0)
        imdb_rating = pk_data.get("imdb_rating", 0.0)
        cursor.execute("""
            UPDATE items SET 
                kp_rating = CASE WHEN kp_rating = 0 OR kp_rating IS NULL THEN ? ELSE kp_rating END,
                imdb_rating = CASE WHEN imdb_rating = 0 OR imdb_rating IS NULL THEN ? ELSE imdb_rating END
            WHERE id = ?
        """, (kp_rating, imdb_rating, item_id))
        conn.commit()

    # 4. Rezka (для ссылки)
    rezka = RezkaParser()
    print("  🔍 Поиск на Rezka...")
    search_title = item['title'].split(' / ')[0]
    rezka_results = rezka.search(search_title, item['year'])
    if rezka_results:
        # Берем первый с хорошим скором
        best = rezka_results[0]
        if best['score'] >= 80:
            print(f"  ✅ Rezka найдена: {best['url']}")
            details = rezka.get_item_details(best['url'])
            cursor.execute("""
                UPDATE items SET 
                    rezka_url = ?,
                    kp_rating = CASE WHEN kp_rating = 0 OR kp_rating IS NULL THEN ? ELSE kp_rating END,
                    imdb_rating = CASE WHEN imdb_rating = 0 OR imdb_rating IS NULL THEN ? ELSE imdb_rating END
                WHERE id = ?
            """, (best['url'], details.get('kp_rating', 0), details.get('imdb_rating', 0), item_id))
            conn.commit()

    print("=== ОБНОВЛЕНИЕ ЗАВЕРШЕНО ===")
    conn.close()

if __name__ == "__main__":
    if len(sys.argv) > 1:
        update_single_item(int(sys.argv[1]))
    else:
        print("Usage: python single_item_update.py <item_id>")
