import sqlite3
import requests
import re
import time
from tmdb_client import TMDBClient
from app_core import VIDEO_CATEGORY_IDS

GARBAGE_KEYWORDS = ['BDRIP', '1080P', '720P', 'WEB-DL', 'HDRIP', 'DVDRIP']

def has_garbage_title(title):
    return any(x in (title or '').upper() for x in GARBAGE_KEYWORDS)

def reprocess_one(target_id):
    conn = sqlite3.connect('app_data.db')
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("""
        SELECT items.id, items.title, items.year,
               items.poster_url, items.description,
               items.kp_id, items.imdb_id, items.kinorium_id,
               items.imdb_rating,
               MIN(releases.rutor_id) as rutor_id
        FROM items
        LEFT JOIN releases ON items.id = releases.item_id
        WHERE items.id = ?
        GROUP BY items.id
    """, (target_id,))
    row = cursor.fetchone()

    if not row:
        print(f"Item {target_id} not found.")
        conn.close()
        return

    tmdb = TMDBClient()
    print(f"=== ТЕСТ: Обработка ID {target_id} ({row['title']}) ===")

    item_id      = row['id']
    old_title    = row['title'] or ''
    year         = row['year']
    rutor_id     = row['rutor_id']
    kp_id        = row['kp_id']        or ''
    imdb_id      = row['imdb_id']      or ''
    kinorium_id  = row['kinorium_id']  or ''
    poster       = row['poster_url']   or ''
    desc         = row['description']  or ''
    imdb_rating  = row['imdb_rating']  or 0.0
    final_title  = old_title

    # ── 1. Рутор (для получения ID) ───────────────────────────────────
    if rutor_id and (not imdb_id):
        print(f"  🔍 Проверяем Рутор ({rutor_id})...")
        try:
            resp = requests.get(f"http://rutor.info/torrent/{rutor_id}", timeout=10)
            if resp.status_code == 200:
                m = re.search(r'imdb\.com/title/(tt\d+)', resp.text)
                if m:
                    imdb_id = m.group(1)
                    print(f"  ✨ Нашел IMDb ID на Руторе: {imdb_id}")
        except: pass

    # ── 2. TMDB ───────────────────────────────────────────────────────
    print("  🔍 Запрашиваем TMDB...")
    tmdb_data = None
    if imdb_id:
        tmdb_data = tmdb.find_by_imdb_id(imdb_id)
    if not tmdb_data:
        search_term = old_title.split(' / ')[0].split('/')[0].strip()
        tmdb_data = tmdb.search_movie(search_term, year)

    if tmdb_data:
        print(f"  📦 Данные получены от TMDB:")
        print(f"     - Title: {tmdb_data.get('title')}")
        print(f"     - Rating (vote_average): {tmdb_data.get('rating')}")
        imdb_rating = tmdb_data.get('rating', 0.0)
        imdb_id = imdb_id or tmdb_data.get('imdb_id', '')
        
        # Сохраняем в базу для проверки
        cursor.execute("""
            UPDATE items
            SET imdb_id = ?, imdb_rating = ?
            WHERE id = ?
        """, (imdb_id, imdb_rating, item_id))
        conn.commit()
        print(f"  ✅ Обновлено в базе: ID={imdb_id}, Rating={imdb_rating}")
    else:
        print("  ⚠️ TMDB не нашел фильм.")

    conn.close()

if __name__ == "__main__":
    reprocess_one(38)
