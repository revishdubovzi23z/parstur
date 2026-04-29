import sqlite3
import time
import requests
import re
import json
from bs4 import BeautifulSoup
from tmdb_client import TMDBClient
from app_core import VIDEO_CATEGORY_IDS

GARBAGE_KEYWORDS = [
    'S01', 'S02', 'S03', 'S04', 'S05', 'S06', 'S07', 'S08', 'S09', 'S10',
    'L1', 'L2', 'MVO', 'DVO', 'ПОЛНЫЙ', 'СЕЗОН', 'WEB-DL', 'BDRIP', '1080P', '720P'
]

class RutorParser:
    def __init__(self):
        self.mirror = "http://rutor.info"

def has_garbage_title(title):
    return any(x in (title or '').upper() for x in GARBAGE_KEYWORDS)

def report_progress(current, total, status_key="reprocess"):
    try:
        with open(f"progress_{status_key}.json", "w") as f:
            json.dump({"current": current, "total": total}, f)
    except: pass

def reprocess_all(force_all=False, specific_id=None):

    conn = sqlite3.connect('app_data.db', timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    print(f"Подключено к БД. Режим WAL включен.", flush=True)

    ids_str = ",".join(map(str, VIDEO_CATEGORY_IDS))

    # Берём карточки для обработки
    garbage_like = " OR ".join(
        [f"items.title LIKE '%{kw}%'" for kw in GARBAGE_KEYWORDS[:6]]
    )
    
    if specific_id:
        where_clause = f"items.id = {specific_id}"
    else:
        where_clause = f"items.category_id IN ({ids_str}) AND items.is_reprocessed = 0"
    if not force_all:
        where_clause += """
          AND (items.is_metadata_fixed = 0 OR items.kp_id IS NULL OR items.kp_id = '' OR items.imdb_id IS NULL OR items.imdb_id = '')
          AND (
            items.poster_url   IS NULL OR items.poster_url   = '' OR
            items.description  IS NULL OR items.description  = '' OR
            items.imdb_id      IS NULL OR items.imdb_id      = '' OR
            items.kp_id        IS NULL OR items.kp_id        = '' OR
            items.imdb_rating  IS NULL OR items.imdb_rating  = 0  OR
            {garbage_like}
          )
        """.format(garbage_like=garbage_like)

    tmdb = TMDBClient()
    rutor = RutorParser()

    mode_str = "ПОЛНОЕ ОБНОВЛЕНИЕ" if force_all else "УМНАЯ ПРОВЕРКА"
    print(f"=== ЗАПУСК: {mode_str} ===", flush=True)

    cursor.execute(f"SELECT COUNT(*) FROM items WHERE {where_clause}")
    total_to_process = cursor.fetchone()[0]
    print(f"Найдено элементов для обработки: {total_to_process}", flush=True)

    total_updated = 0
    total_fixed_all = 0

    while True:
        cursor.execute(f"""
            SELECT items.id, items.title, items.year,
                   items.poster_url, items.description,
                   items.kp_id, items.imdb_id, items.kinorium_id,
                   items.imdb_rating, items.kp_rating,
                   MIN(releases.rutor_id) as rutor_id,
                   items.category_id
            FROM items
            LEFT JOIN releases ON items.id = releases.item_id
            WHERE {where_clause}
            GROUP BY items.id
            ORDER BY items.id DESC
            LIMIT 100
        """)
        items = cursor.fetchall()
        
        if not items:
            report_progress(total_to_process, total_to_process)
            if total_updated == 0:
                print("💎 Все карточки полностью заполнены!", flush=True)
            else:
                print(f"\n✅ Обработка завершена. Всего обновлено: {total_updated}, полностью заполнено: {total_fixed_all}", flush=True)
            break

        print(f"\n📦 Обработка пакета из {len(items)} карточек...", flush=True)
        
        for idx, row in enumerate(items, 1):

            report_progress(total_updated + idx, total_to_process)
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
            kp_rating    = row['kp_rating']    or 0.0
            final_title  = old_title

            if tmdb.is_limited:
                print("\n[!] Лимит TMDB исчерпан. Остановка процесса.", flush=True)
                conn.close()
                return

            # Показываем что именно нужно найти
            needs = []
            if not kp_id:              needs.append('KP ID')
            if not imdb_id:            needs.append('IMDb ID')
            if not poster:             needs.append('постер')
            if not desc:               needs.append('описание')
            if not imdb_rating:        needs.append('рейтинг IMDb')
            if has_garbage_title(old_title): needs.append('чистое название')

            print(f"\n[{total_updated + idx}] 🎬 {old_title}", flush=True)
            if needs:
                print(f"  📋 Нужно: {', '.join(needs)}", flush=True)

            changes = []
            has_error = False

            # ── 1. Страница на Руторе ─────────────────────────────────────
            if not kp_id or not imdb_id:
                cursor.execute("SELECT rutor_id FROM releases WHERE item_id = ?", (item_id,))
                rels = cursor.fetchall()
                
                for rel_idx, rel in enumerate(rels):
                    if kp_id and imdb_id: break
                        
                    rid = rel['rutor_id']
                    try:
                        time.sleep(0.3)
                        MIRROR = "http://rutor.info"
                        print(f"  🔍 Рутор (1.1): {MIRROR}/torrent/{rid}", flush=True)
                        resp = requests.get(f"{MIRROR}/torrent/{rid}", timeout=20)
                        if resp.status_code == 200:
                            # Ищем KP ID
                            if not kp_id:
                                m = re.search(r'kinopoisk\.ru/rating/(\d+)\.gif', resp.text)
                                if not m: m = re.search(r'kinopoisk\.ru/(?:level/1/)?(?:film|series)/(\d+)', resp.text)
                                if not m: m = re.search(r'film/(\d+)', resp.text)
                                if m:
                                    kp_id = m.group(1)
                                    changes.append(f"    ✅ Нашел KP ID: {kp_id}")

                            if not imdb_id:
                                m = re.search(r'imdb\.com/title/(tt\d+)', resp.text)
                                if m:
                                    imdb_id = m.group(1)
                                    changes.append(f"    ✅ Нашел IMDb ID: {imdb_id}")
                        else:
                            print(f"    ⚠️ Ошибка Rutor: {resp.status_code}", flush=True)
                    except Exception as e:
                        print(f"    ⚠️ Ошибка глубокого поиска: {e}", flush=True)

            # ── 2. TMDB ───────────────────────────────────────────────────
            tmdb_data = None
            if not poster or not desc or not imdb_rating or has_garbage_title(old_title):
                try:

                    if imdb_id:
                        tmdb_data = tmdb.find_by_imdb_id(imdb_id)
                    
                    if not tmdb_data:
                        parts = old_title.split(' / ')
                        search_term = parts[0].split('/')[0].strip()
                        orig_term = parts[1].split('/')[0].strip() if len(parts) > 1 else None
                        tmdb_data = tmdb.search_movie(orig_term or search_term, year)
                        if not tmdb_data and orig_term:
                            tmdb_data = tmdb.search_movie(search_term, year)

                    if tmdb_data:
                        print("  ✅ Данные в TMDB получены", flush=True)
                        if not imdb_id and tmdb_data.get("imdb_id"):
                            imdb_id = tmdb_data["imdb_id"]
                            changes.append(f"    🎯 Нашел IMDb ID (через TMDB): {imdb_id}")
                        if not poster and tmdb_data.get("poster_url"):
                            poster = tmdb_data["poster_url"]
                            changes.append("    ✅ Постер добавлен")
                        if not desc and tmdb_data.get("description"):
                            desc = tmdb_data["description"]
                            changes.append("    ✅ Описание добавлено")
                        if tmdb_data.get("title"):
                            new_ru = tmdb_data["title"]
                            new_orig = tmdb_data.get("original_title") or tmdb_data.get("title")
                            
                            # Формируем красивое название: Русское / Оригинальное (Год)
                            if new_ru.lower() != new_orig.lower():
                                new_title = f"{new_ru} / {new_orig}"
                            else:
                                new_title = new_ru
                            
                            if year: new_title += f" ({year})"
                            
                            if new_title != old_title:
                                final_title = new_title
                                changes.append(f"    ✨ Название обновлено: {final_title}")
                            
                            if new_orig:
                                original_title = new_orig
                    else:
                        print("  ⚠️ TMDB: ничего не найдено", flush=True)
                except Exception as e:
                    print(f"    ⚠️ Ошибка TMDB: {e}", flush=True)

            # ── Итог ──────────────────────────────────────────────────────
            for c in changes: print(c, flush=True)

            all_ok = (poster and desc and kp_id and imdb_id and (kp_rating > 0) and not has_garbage_title(final_title))
            is_fixed = 1 if all_ok else 0

            if is_fixed:
                print("  ✅ Карточка полностью заполнена.", flush=True)
                total_fixed_all += 1
            elif not changes:
                print("  💎 Изменений не найдено.", flush=True)
                if kp_id and imdb_id: is_fixed = 1; total_fixed_all += 1

            # ── Сохраняем ─────────────────────────────────────────────────
            try:
                cursor.execute("""
                    UPDATE items
                    SET title = ?, poster_url = ?, description = ?, imdb_id = ?, kp_id = ?,
                        kp_rating = ?, imdb_rating = ?, is_metadata_fixed = ?, is_reprocessed = 1,
                        original_title = ?
                    WHERE id = ?
                """, (final_title, poster, desc, imdb_id, kp_id, kp_rating, imdb_rating, is_fixed, tmdb_data.get("original_title") if tmdb_data else None, item_id))
                conn.commit()
                total_updated += 1
            except sqlite3.IntegrityError:
                print(f"  🔗 Обнаружен дубликат для '{final_title}'. Сливаю карточки...", flush=True)
                cursor.execute("SELECT id FROM items WHERE title = ? AND year = ? AND category_id = ? AND id != ?", 
                              (final_title, year, row['category_id'], item_id))
                existing = cursor.fetchone()
                if existing:
                    existing_id = existing[0]
                    cursor.execute("UPDATE releases SET item_id = ? WHERE item_id = ?", (existing_id, item_id))
                    cursor.execute("UPDATE items SET is_reprocessed = 1 WHERE id = ?", (existing_id,))
                    cursor.execute("DELETE FROM items WHERE id = ?", (item_id,))
                    conn.commit()
                    print(f"  ✅ Успешно слито с карточкой ID {existing_id}", flush=True)
                else:
                    cursor.execute("UPDATE items SET is_reprocessed = 1 WHERE id = ?", (item_id,))
                    conn.commit()
            
            time.sleep(0.5)
        
        if specific_id:
            break


    conn.close()

if __name__ == "__main__":
    import sys
    force = "--force" in sys.argv
    specific_id = None
    if "--id" in sys.argv:
        try:
            idx = sys.argv.index("--id")
            specific_id = int(sys.argv[idx + 1])
        except: pass
    
    reprocess_all(force, specific_id)
