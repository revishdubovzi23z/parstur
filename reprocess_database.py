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

def reprocess_all():
    conn = sqlite3.connect('app_data.db')
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    ids_str = ",".join(map(str, VIDEO_CATEGORY_IDS))

    # Берём ВСЕ карточки, у которых есть хоть одно незаполненное поле
    garbage_like = " OR ".join(
        [f"items.title LIKE '%{kw}%'" for kw in GARBAGE_KEYWORDS[:6]]
    )
    cursor.execute(f"""
        SELECT items.id, items.title, items.year,
               items.poster_url, items.description,
               items.kp_id, items.imdb_id, items.kinorium_id,
               items.imdb_rating, items.kp_rating,
               MIN(releases.rutor_id) as rutor_id
        FROM items
        LEFT JOIN releases ON items.id = releases.item_id
        WHERE items.category_id IN ({ids_str})
          AND (items.is_metadata_fixed = 0 OR items.kp_id IS NULL OR items.kp_id = '' OR items.imdb_id IS NULL OR items.imdb_id = '')
          AND (
            items.poster_url   IS NULL OR items.poster_url   = '' OR
            items.description  IS NULL OR items.description  = '' OR
            items.imdb_id      IS NULL OR items.imdb_id      = '' OR
            items.kp_id        IS NULL OR items.kp_id        = '' OR
            items.imdb_rating  IS NULL OR items.imdb_rating  = 0  OR
            {garbage_like}
          )
        GROUP BY items.id
        ORDER BY items.id
    """)
    items = cursor.fetchall()

    tmdb = TMDBClient()
    rutor = RutorParser()

    print(f"=== ЗАПУСК: Проверка базы ({len(items)} карточек с неполными данными) ===", flush=True)
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

        # Показываем что именно нужно найти
        needs = []
        if not kp_id:              needs.append('KP ID')
        if not imdb_id:            needs.append('IMDb ID')
        if not poster:             needs.append('постер')
        if not desc:               needs.append('описание')
        if not imdb_rating:        needs.append('рейтинг IMDb')
        if has_garbage_title(old_title): needs.append('чистое название')

        print(f"\n[{idx}/{len(items)}] 🎬 {old_title}", flush=True)
        print(f"  📋 Нужно: {', '.join(needs)}", flush=True)

        changes = []
        has_error = False

        # ── 1. Страница на Руторе ─────────────────────────────────────
        if not kp_id or not imdb_id:
            cursor.execute("SELECT rutor_id FROM releases WHERE item_id = ?", (item_id,))
            rels = cursor.fetchall()
            
            for rel_idx, rel in enumerate(rels):
                if kp_id and imdb_id:
                    break
                    
                rid = rel['rutor_id']
                try:
                    time.sleep(0.5)
                    MIRROR = "http://rutor.info"
                    print(f"  🔍 Рутор ({rel_idx+1}/{len(rels)}): {MIRROR}/torrent/{rid}", flush=True)
                    resp = requests.get(f"{MIRROR}/torrent/{rid}", timeout=20)
                    if resp.status_code == 200:
                        if not kp_id:
                            m = re.search(r'kinopoisk\.ru/rating/(\d+)\.gif', resp.text)
                            if not m:
                                m = re.search(r'film/(\d+)', resp.text)
                            if m:
                                kp_id = m.group(1)
                                changes.append(f"  ✨ KP ID: {kp_id}")

                        if not imdb_id:
                            m = re.search(r'imdb\.com/title/(tt\d+)', resp.text)
                            if m:
                                imdb_id = m.group(1)
                                changes.append(f"  ✨ IMDb ID: {imdb_id}")
                    else:
                        print(f"  ⚠️ Рутор вернул {resp.status_code}", flush=True)
                except Exception as e:
                    print(f"  ⚠️ Ошибка связи с Рутором: {e}", flush=True)
                    has_error = True

        # ── 1.1 Глубокий поиск на Руторе ───────────────────────────────
        if not kp_id or not imdb_id:
            try:
                # Чистим название для поиска (убираем всё в скобках)
                search_term = old_title.split(' / ')[0].split('/')[0].strip()
                search_term = re.sub(r'\(.*?\)', '', search_term)
                search_term = re.sub(r'\[.*?\]', '', search_term).strip()
                
                print(f"  🔍 Рутор (глубокий поиск): {search_term}", flush=True)
                search_results = rutor.search_releases(search_term)
                
                # Фильтруем по году
                matches = []
                for res in search_results:
                    res_year = res.get('year')
                    if res_year and year and abs(res_year - year) <= 1:
                        matches.append(res)
                
                if not matches and not search_results:
                    print("    ⚠️ Ничего не найдено.", flush=True)

                for m_idx, m in enumerate(matches[:5]): # Проверяем первые 5 совпадений
                    if kp_id and imdb_id: break
                    
                    rid = m['rutor_id']
                    print(f"    🔎 Проверка результата {m_idx+1}: {rutor.mirror}/torrent/{rid}", flush=True)
                    time.sleep(0.5)
                    resp = requests.get(f"{rutor.mirror}/torrent/{rid}", timeout=20)
                    if resp.status_code == 200:
                        # Ищем KP ID
                        if not kp_id:
                            found_kp = re.search(r'kinopoisk\.ru/rating/(\d+)\.gif', resp.text)
                            if not found_kp: found_kp = re.search(r'kinopoisk\.ru/(?:level/1/)?film/(\d+)', resp.text)
                            if not found_kp: found_kp = re.search(r'film/(\d+)', resp.text)
                            if found_kp:
                                kp_id = found_kp.group(1)
                                changes.append(f"  ✨ KP ID (поиск): {kp_id}")
                        
                        # Ищем IMDb ID
                        if not imdb_id:
                            found_imdb = re.search(r'imdb\.com/title/(tt\d+)', resp.text)
                            if found_imdb:
                                imdb_id = found_imdb.group(1)
                                changes.append(f"  ✨ IMDb ID (поиск): {imdb_id}")
            except Exception as e:
                print(f"  ⚠️ Ошибка глубокого поиска Рутора: {e}", flush=True)

        # ── 2. TMDB ───────────────────────────────────────────────────
        if not poster or not desc or not imdb_rating or has_garbage_title(old_title):
            try:
                tmdb_data = None
                if imdb_id:
                    tmdb_data = tmdb.find_by_imdb_id(imdb_id)
                if not tmdb_data:
                    search_term = old_title.split(' / ')[0].split('/')[0].strip()
                    tmdb_data = tmdb.search_movie(search_term, year)

                if tmdb_data:
                    if not imdb_id and tmdb_data.get("imdb_id"):
                        imdb_id = tmdb_data["imdb_id"]
                        changes.append(f"  ✨ IMDb ID (TMDB): {imdb_id}")
                    if not poster and tmdb_data.get("poster_url"):
                        poster = tmdb_data["poster_url"]
                        changes.append("  ✨ Постер добавлен")
                    if not desc and tmdb_data.get("description"):
                        desc = tmdb_data["description"]
                        changes.append("  ✨ Описание добавлено")
                    # Мы больше не берем рейтинг с TMDB, так как он не является оригинальным IMDb рейтингом
                    # if not imdb_rating and tmdb_data.get("rating"):
                    #     imdb_rating = tmdb_data["rating"]
                    #     changes.append(f"  ✨ IMDb рейтинг: {imdb_rating}")
                    if has_garbage_title(old_title) and tmdb_data.get("title"):
                        new_title = tmdb_data["title"]
                        if year:
                            new_title += f" ({year})"
                        if new_title != old_title:
                            final_title = new_title
                            changes.append(f"  ✨ Чистое название: {final_title}")
                else:
                    print("  ⚠️ TMDB: ничего не найдено", flush=True)
            except Exception as e:
                print(f"  ⚠️ Ошибка TMDB: {e}", flush=True)
                has_error = True

        # ── Итог ──────────────────────────────────────────────────────
        for c in changes:
            print(c, flush=True)

        # Карточка считается полностью готовой (is_fixed=1) только если есть ОБА числовых рейтинга
        # Теперь мы строго требуем наличие IMDb и KP рейтинга > 0
        kp_rating = row['kp_rating'] or 0.0
        
        all_ok = (
            poster and desc and
            kp_id and imdb_id and
            (imdb_rating and imdb_rating > 0) and
            (kp_rating and kp_rating > 0) and
            not has_garbage_title(final_title)
        )
        is_fixed = 1 if all_ok else 0

        if is_fixed:
            print("  ✅ Все поля заполнены — карточка зафиксирована.", flush=True)
            fixed_count += 1
        elif has_error:
            print("  ⚠️ Ошибка — карточка будет повторно обработана при следующем запуске.", flush=True)
        elif not changes:
            print("  💎 Изменений не найдено (данных нет нигде).", flush=True)
            # Не фиксируем карточку, если в ней всё еще нет ID, чтобы можно было попробовать позже
            if not has_error and kp_id and imdb_id:
                is_fixed = 1
                fixed_count += 1

        # ── Сохраняем ─────────────────────────────────────────────────
        try:
            cursor.execute("""
                UPDATE items
                SET title            = ?,
                    poster_url       = ?,
                    description      = ?,
                    imdb_id          = ?,
                    kp_id            = ?,
                    kinorium_id      = ?,
                    kp_rating        = ?,
                    imdb_rating      = ?,
                    is_metadata_fixed = ?
                WHERE id = ?
            """, (final_title, poster, desc, imdb_id, kp_id, kinorium_id, kp_rating, imdb_rating, is_fixed, item_id))
            conn.commit()
            updated_count += 1
        except sqlite3.IntegrityError:
            # Если возникла ошибка уникальности - значит такая карточка уже есть.
            # Нам нужно СЛИТЬ их: перекинуть релизы из текущей в существующую.
            print(f"  🔗 Обнаружен дубликат для '{final_title}'. Сливаю карточки...", flush=True)
            
            # Находим ID существующей карточки
            cursor.execute("SELECT id FROM items WHERE title = ? AND year = ? AND category_id = ? AND id != ?", 
                          (final_title, year, row['category_id'] if 'category_id' in row.keys() else 1, item_id))
            existing = cursor.fetchone()
            if existing:
                existing_id = existing[0]
                # Переносим релизы
                cursor.execute("UPDATE releases SET item_id = ? WHERE item_id = ?", (existing_id, item_id))
                # Удаляем текущую (дубликат)
                cursor.execute("DELETE FROM items WHERE id = ?", (item_id,))
                conn.commit()
                print(f"  ✅ Успешно слито с карточкой ID {existing_id}", flush=True)
            else:
                print("  ⚠️ Не удалось найти существующую карточку для слияния.", flush=True)

    conn.close()
    print(f"\n=== ГОТОВО! Обработано: {updated_count}, зафиксировано: {fixed_count} ===", flush=True)


if __name__ == "__main__":
    reprocess_all()
