import sqlite3
import requests
import re
import time
from tmdb_client import TMDBClient
from rutor_parser import RutorParser
from app_core import VIDEO_CATEGORY_IDS

GARBAGE_KEYWORDS = [
    'BDRIP', '1080P', '720P', 'WEB-DL', 'HDRIP', 'DVDRIP',
    'AVC', 'HEVC', 'H.264', 'H.265', 'DUB', 'L1', 'L2',
    'VO', 'MVO', 'ITUNES', 'DEDEP', 'LINE', 'TS', 'CAMRIP',
    'VIDEOFILM', 'EXKINORAY', 'COLD FILM', 'SUNSHINE STUDIO'
]

def has_garbage_title(title):
    return any(x in (title or '').upper() for x in GARBAGE_KEYWORDS)

def reprocess_all(force_all=False):
    conn = sqlite3.connect('app_data.db')
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    ids_str = ",".join(map(str, VIDEO_CATEGORY_IDS))

    # Берём карточки для обработки
    garbage_like = " OR ".join(
        [f"items.title LIKE '%{kw}%'" for kw in GARBAGE_KEYWORDS[:6]]
    )
    
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

    cursor.execute(f"""
        SELECT items.id, items.title, items.year,
               items.poster_url, items.description,
               items.kp_id, items.imdb_id, items.kinorium_id,
               items.imdb_rating, items.kp_rating,
               MIN(releases.rutor_id) as rutor_id
        FROM items
        LEFT JOIN releases ON items.id = releases.item_id
        WHERE {where_clause}
        GROUP BY items.id
        ORDER BY items.id DESC
        LIMIT 500
    """)
    items = cursor.fetchall()

    tmdb = TMDBClient()
    rutor = RutorParser()

    mode_str = "ПОЛНОЕ ОБНОВЛЕНИЕ" if force_all else "УМНАЯ ПРОВЕРКА"
    print(f"=== ЗАПУСК: {mode_str} ({len(items)} карточек) ===", flush=True)
    if not items:
        print("💎 Все карточки полностью заполнены!", flush=True)
        conn.close()
        return

    updated_count = 0
    fixed_count = 0

    for idx, row in enumerate(items, 1):
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
            break

        # Показываем что именно нужно найти
        needs = []
        if not kp_id:              needs.append('KP ID')
        if not imdb_id:            needs.append('IMDb ID')
        if not poster:             needs.append('постер')
        if not desc:               needs.append('описание')
        if not imdb_rating:        needs.append('рейтинг IMDb')
        if has_garbage_title(old_title): needs.append('чистое название')

        print(f"\n[{idx}/{len(items)}] 🎬 {old_title}", flush=True)
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
                        # Ищем KP ID (поддерживаем разные форматы ссылок)
                        if not kp_id:
                            # Формат 1: Картинка-рейтинг (gif)
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
                        print(f"    ⚠️ Рутор вернул {resp.status_code}", flush=True)
                except Exception as e:
                    print(f"    ⚠️ Ошибка связи с Рутором: {e}", flush=True)
                    has_error = True

        # ── 1.2 Глубокий поиск на Руторе ───────────────────────────────
        if not kp_id or not imdb_id:
            try:
                # Чистим название для поиска
                parts = old_title.split(' / ')
                search_term = parts[0].split('/')[0].strip()
                search_term = re.sub(r'\(.*?\)', '', search_term)
                search_term = re.sub(r'\[.*?\]', '', search_term).strip()
                
                orig_term = None
                if len(parts) > 1:
                    orig_term = parts[1].split('/')[0].strip()
                    orig_term = re.sub(r'\(.*?\)', '', orig_term)
                    orig_term = re.sub(r'\[.*?\]', '', orig_term).strip()

                def try_rutor_search(term, label):
                    nonlocal kp_id, imdb_id
                    if not term: return False
                    print(f"  🔍 Рутор (1.2 {label}): {term}", flush=True)
                    results = rutor.search_releases(term)
                    
                    # Фильтруем по году
                    matches = [res for res in results if res.get('year') and year and abs(res.get('year') - year) <= 1]
                    if not matches:
                        if results: print(f"    ⚠️ Найдено {len(results)} раздач, но ни одна не подошла по году ({year}).", flush=True)
                        return False
                    
                    print(f"    🔎 Найдено подходящих: {len(matches)}. Проверяю первые {min(3, len(matches))}...", flush=True)
                    
                    found_any = False
                    for m_idx, m in enumerate(matches[:3]):
                        if kp_id and imdb_id: break
                        rid = m['rutor_id']
                        time.sleep(0.4)
                        resp = requests.get(f"{rutor.mirror}/torrent/{rid}", timeout=20)
                        if resp.status_code == 200:
                            if not kp_id:
                                m_kp = re.search(r'kinopoisk\.ru/rating/(\d+)\.gif', resp.text)
                                if not m_kp: m_kp = re.search(r'kinopoisk\.ru/(?:level/1/)?(?:film|series)/(\d+)', resp.text)
                                if m_kp:
                                    kp_id = m_kp.group(1)
                                    changes.append(f"    ✅ Нашел KP ID (в архиве): {kp_id}")
                                    found_any = True
                            if not imdb_id:
                                m_imdb = re.search(r'imdb\.com/title/(tt\d+)', resp.text)
                                if m_imdb:
                                    imdb_id = m_imdb.group(1)
                                    changes.append(f"    ✅ Нашел IMDb ID (в архиве): {imdb_id}")
                                    found_any = True
                    return found_any

                # Сначала по русскому, потом по оригиналу
                if not try_rutor_search(search_term, "Глубокий поиск"):
                    if orig_term and (not kp_id or not imdb_id):
                        try_rutor_search(orig_term, "Поиск по оригиналу")

            except Exception as e:
                print(f"    ⚠️ Ошибка глубокого поиска: {e}", flush=True)


        # ── 2. TMDB ───────────────────────────────────────────────────
        if not poster or not desc or not imdb_rating or has_garbage_title(old_title):
            try:
                tmdb_data = None
                if imdb_id:
                    tmdb_data = tmdb.find_by_imdb_id(imdb_id)
                
                if not tmdb_data:
                    # Пробуем найти название для поиска
                    parts = old_title.split(' / ')
                    search_term = parts[0].split('/')[0].strip()
                    orig_term = parts[1].split('/')[0].strip() if len(parts) > 1 else None
                    
                    # Приоритет оригиналу если он есть
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
                    if has_garbage_title(old_title) and tmdb_data.get("title"):
                        new_title = tmdb_data["title"]
                        if year: new_title += f" ({year})"
                        if new_title != old_title:
                            final_title = new_title
                            changes.append(f"    ✨ Название очищено: {final_title}")
                else:
                    print("  ⚠️ TMDB: ничего не найдено", flush=True)
            except Exception as e:
                print(f"    ⚠️ Ошибка TMDB: {e}", flush=True)
                has_error = True

        # ── Итог ──────────────────────────────────────────────────────
        for c in changes: print(c, flush=True)

        all_ok = (poster and desc and kp_id and imdb_id and (kp_rating > 0) and not has_garbage_title(final_title))
        is_fixed = 1 if all_ok else 0

        if is_fixed:
            print("  ✅ Карточка полностью заполнена.", flush=True)
            fixed_count += 1
        elif not changes:
            print("  💎 Изменений не найдено.", flush=True)
            if kp_id and imdb_id: is_fixed = 1; fixed_count += 1

        # ── Сохраняем ─────────────────────────────────────────────────
        try:
            cursor.execute("""
                UPDATE items
                SET title = ?, poster_url = ?, description = ?, imdb_id = ?, kp_id = ?,
                    kp_rating = ?, imdb_rating = ?, is_metadata_fixed = ?, is_reprocessed = 1
                WHERE id = ?
            """, (final_title, poster, desc, imdb_id, kp_id, kp_rating, imdb_rating, is_fixed, item_id))
            conn.commit()
            updated_count += 1
        except sqlite3.IntegrityError:
            # Если возникла ошибка уникальности - значит такая карточка уже есть.
            print(f"  🔗 Обнаружен дубликат для '{final_title}'. Сливаю карточки...", flush=True)
            
            cursor.execute("SELECT id FROM items WHERE title = ? AND year = ? AND category_id = ? AND id != ?", 
                          (final_title, year, row['category_id'] if 'category_id' in row.keys() else 1, item_id))
            existing = cursor.fetchone()
            if existing:
                existing_id = existing[0]
                cursor.execute("UPDATE releases SET item_id = ? WHERE item_id = ?", (existing_id, item_id))
                # Помечаем существующую как "проверенную", раз мы только что её актуализировали
                cursor.execute("UPDATE items SET is_reprocessed = 1 WHERE id = ?", (existing_id,))
                cursor.execute("DELETE FROM items WHERE id = ?", (item_id,))
                conn.commit()
                print(f"  ✅ Успешно слито с карточкой ID {existing_id}", flush=True)
            else:
                # На всякий случай помечаем и текущую, если слияние не вышло
                cursor.execute("UPDATE items SET is_reprocessed = 1 WHERE id = ?", (item_id,))
                conn.commit()
                print("  ⚠️ Не удалось найти существующую карточку для слияния.", flush=True)
        except Exception as e:
            print(f"  ⚠️ Ошибка сохранения: {e}", flush=True)

    conn.close()
    print(f"\n=== ГОТОВО! Обработано: {updated_count}, зафиксировано: {fixed_count} ===", flush=True)

if __name__ == "__main__":
    import sys
    force = "--force" in sys.argv
    reprocess_all(force)

