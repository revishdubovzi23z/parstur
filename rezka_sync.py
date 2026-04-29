import sqlite3
import requests
import time
import datetime
import re
import sys
import os
import json
from HdRezkaApi import HdRezkaApi, HdRezkaSearch
from app_core import normalize_title

def report_progress(current, total, status_key="rezka"):
    try:
        with open(f"progress_{status_key}.json", "w") as f:
            json.dump({"current": current, "total": total}, f)
    except: pass

def get_db():

    conn = sqlite3.connect('app_data.db')
    conn.row_factory = sqlite3.Row
    return conn

def search_rezka_metadata():
    conn = get_db()
    cursor = conn.cursor()
    
    # Ищем тех, у кого не хватает ID или оценок
    video_cats = "(1, 4, 5, 16, 7)"
    cursor.execute(f"""
        SELECT id, title, year, kp_id, imdb_id, kp_rating, imdb_rating, rezka_url, poster_url
        FROM items
        WHERE category_id IN {video_cats}
        AND (
            kp_id IS NULL OR kp_id = '' OR
            imdb_id IS NULL OR imdb_id = '' OR
            kp_rating = 0 OR kp_rating IS NULL OR
            imdb_rating = 0 OR imdb_rating IS NULL OR
            rezka_url IS NULL OR rezka_url = ''
        )
        AND is_ignored = 0
        AND checked_rezka = 0
        ORDER BY id DESC
    """)
    items = cursor.fetchall()
    
    total_count = len(items)
    print(f"=== REZKA SYNC (Total: {total_count}) ===")
    
    searcher = HdRezkaSearch("https://rezka.ag")
    
    for idx, row in enumerate(items, 1):
        try:
            report_progress(idx, total_count)
            item_id = row['id']

            title = row['title']
            year = row['year']
            kp_id = row['kp_id']
            imdb_id = row['imdb_id']
            kp_rating = row['kp_rating']
            imdb_rating = row['imdb_rating']
               # --- ПОДГОТОВКА ПОИСКОВЫХ ЗАПРОСОВ ---
            # Разделяем название на части (русское / английское)
            parts = [p.strip() for p in title.split('/')]
            clean_parts = []
            for p in parts:
                # Убираем год в скобках и лишние пробелы
                p_clean = re.sub(r'\(.*?\)', '', p).strip()
                if p_clean and len(p_clean) > 1:
                    clean_parts.append(p_clean)
            
            # Приоритет поиска: Английское название (обычно точнее), потом Русское
            search_queries = sorted(clean_parts, key=lambda x: (not x.isascii(), len(x)), reverse=True)
            
            print(f"\n[{idx}/{total_count}] TITLE: {title} ({year})", flush=True)
            if kp_id or imdb_id:
                print(f"    📋 Имеем ID: KP:{kp_id or '-'}, IMDb:{imdb_id or '-'}", flush=True)

            all_results = []
            seen_urls = set()

            for s_title in search_queries:
                try:
                    # 1. Пробуем с годом
                    res_with_year = searcher.fast_search(f"{s_title} {year}")
                    
                    # 2. Сразу же добавляем результаты без года, если их мало или если результаты с годом не точные
                    # (Но чтобы не спамить лишними запросами, сначала проверим качество результатов с годом)
                    need_no_year = True
                    if res_with_year:
                        # Если хотя бы один результат с годом имеет точное совпадение названия
                        for r in res_with_year:
                            res_name_norm = normalize_title(re.sub(r'\(.*?\)', '', r['title']))
                            if any(res_name_norm == db_norm for db_norm in [normalize_title(p) for p in clean_parts]):
                                need_no_year = False
                                break
                    
                    res_no_year = []
                    if need_no_year:
                        res_no_year = searcher.fast_search(s_title)
                    
                    for r in res_with_year + res_no_year:
                        if r['url'] not in seen_urls:
                            all_results.append(r)
                            seen_urls.add(r['url'])
                except Exception as e:
                    print(f"    ⚠️ Ошибка поиска по '{s_title}': {e}")

            if not all_results:
                # Если совсем ничего не нашли, пробуем самый агрессивный поиск по первой части названия
                if clean_parts:
                    try:
                        print(f"    [?] Trying fallback search for '{clean_parts[0]}'...")
                        res_fallback = searcher.fast_search(clean_parts[0])
                        for r in res_fallback:
                            if r['url'] not in seen_urls:
                                all_results.append(r)
                                seen_urls.add(r['url'])
                    except: pass

            if not all_results:
                print(f"  [-] Nothing found on Rezka.")
                cursor.execute("UPDATE items SET checked_rezka = 1 WHERE id = ?", (item_id,))
                conn.commit()
                continue
            
            print(f"    [*] Found {len(all_results)} results in search.")

            results_with_scores = []
            norm_db_titles = [normalize_title(p) for p in clean_parts]

            for res in all_results:
                res_name = res['title']
                res_url = res['url']
                
                # Извлекаем год из названия Резки "Название (Год)" или из URL
                year_match = re.search(r'\((\d{4})\)', res_name)
                if not year_match: 
                    # Ищем год в URL: ищем 4 цифры после дефиса
                    year_match = re.search(r'-(\d{4})', res_url)
                res_year = int(year_match.group(1)) if year_match else None
                
                # Нормализуем название результата для сравнения
                res_clean_name = re.sub(r'\(.*?\)', '', res_name).replace(' / ', '/').strip()
                res_parts = [normalize_title(p.strip()) for p in res_clean_name.split('/')]
                
                score = 0
                # Проверка совпадения названия (любая часть)
                match_found = False
                exact_name_match = False
                for db_norm in norm_db_titles:
                    for res_norm in res_parts:
                        if db_norm == res_norm:
                            score += 130 # Увеличиваем за точное совпадение
                            match_found = True
                            exact_name_match = True
                            break
                        elif (len(db_norm) > 4 and db_norm in res_norm) or (len(res_norm) > 4 and res_norm in db_norm):
                            score += 50
                            match_found = True
                    if match_found: break

                # Проверка года (базовая по результатам поиска)
                if year and res_year:
                    diff = abs(year - res_year)
                    if diff == 0: score += 60
                    elif diff == 1: score += 50 # Для сериалов разброс в 1 год - это норма
                    elif diff <= 3: score -= 40 # Небольшой штраф за малый разброс
                    else: score -= 150 # Другой год - большой штраф
                
                results_with_scores.append({
                    'res': res, 
                    'score': score, 
                    'year': res_year,
                    'exact_name_match': exact_name_match
                })
            
            results_with_scores.sort(key=lambda x: x['score'], reverse=True)
            
            final_res = None
            final_data = {}

            for item in results_with_scores:
                res = item['res']
                score = item['score']
                exact_name_match = item.get('exact_name_match', False)
                
                # Если название совпало точно, заходим даже при плохом годе (чтобы проверить ID)
                if score < 70 and not exact_name_match: continue 
                
                print(f"    [?] Checking: {res['title']} (Score: {score})")
                try:
                    rezka = HdRezkaApi(res['url'])
                    soup = rezka.soup
                except:
                    print(f"      [!] Page load error.")
                    continue
                
                # 1. СБОР ID И ГОДА СО СТРАНИЦЫ
                page_kp_id = None
                page_imdb_id = None
                page_year = item.get('year') # Год из результатов поиска
                
                # Дополнительно ищем год на самой странице (в th или li)
                if not page_year:
                    # Ищем во всех тегах, содержащих "Год" или "Дата выхода"
                    year_label = soup.find(lambda tag: tag.name in ['th', 'b', 'span', 'h2'] and tag.text and any(x in tag.text for x in ['Год', 'Дата выхода']))
                    if year_label:
                        container = year_label.find_next_sibling(['td', 'span']) or year_label.parent
                        if container:
                            y_m = re.search(r'(\d{4})', container.text)
                            if y_m: page_year = int(y_m.group(1))
                    
                    if not page_year:
                        y_m = re.search(r'(?:Год|Дата выхода):.*?(\d{4})', str(soup), re.S | re.I)
                        if y_m: page_year = int(y_m.group(1))
                
                if page_year:
                    print(f"      [*] Year on page: {page_year}")

                # Пересчитываем score с учетом уточненного года
                current_score = score
                if not item.get('year') and page_year and year:
                    diff = abs(year - page_year)
                    if diff == 0: current_score += 60
                    elif diff == 1: current_score += 50
                    elif diff <= 3: current_score -= 40
                    else: current_score -= 150
                
                # А) Из кнопок рейтинга
                rate_blocks = soup.find_all(class_=re.compile(r'b-post__info_rates'))
                for block in rate_blocks:
                    kp_link = block.find('a', href=re.compile(r'kinopoisk\.ru'))
                    if kp_link:
                        kp_m = re.search(r'/film/(\d+)|/series/(\d+)|/(\d+)/', kp_link['href'])
                        if kp_m: page_kp_id = next(g for g in kp_m.groups() if g)
                    
                    imdb_link = block.find('a', href=re.compile(r'imdb\.com'))
                    if imdb_link:
                        imdb_m = re.search(r'/title/(tt\d+)', imdb_link['href'])
                        if imdb_m: page_imdb_id = imdb_m.group(1)

                # Б) Из скрытых ссылок
                links = soup.find_all('a', href=re.compile(r'/help/'))
                import base64
                from urllib.parse import unquote
                for link in links:
                    try:
                        href = link['href']
                        if '/help/' not in href: continue
                        b64_url = href.split('/help/')[1].split('.html')[0].strip('/')
                        b64_url = unquote(b64_url)
                        # Чистим от возможных лишних символов
                        b64_url = re.sub(r'[^a-zA-Z0-9+/=]', '', b64_url)
                        missing_padding = len(b64_url) % 4
                        if missing_padding: b64_url += '=' * (4 - missing_padding)
                        
                        real_url = base64.b64decode(b64_url).decode('utf-8', errors='ignore')
                        real_url = unquote(real_url) # Разкодируем URL-encoded символы типа %3A
                        
                        if 'kinopoisk.ru' in real_url:
                            kp_m = re.search(r'/(?:film|series)/(\d+)|/(\d+)/', real_url)
                            if kp_m and not page_kp_id: page_kp_id = next(g for g in kp_m.groups() if g)
                        elif 'imdb.com' in real_url:
                            imdb_m = re.search(r'/title/(tt\d+)', real_url)
                            if imdb_m and not page_imdb_id: imdb_m = imdb_m.group(1)
                    except: pass

                # --- ЖЕЛЕЗНАЯ ВЕРИФИКАЦИЯ ПО ID ---
                id_match = False
                id_conflict = False
                
                print(f"      [?] ID Check: DB({kp_id or '-'}, {imdb_id or '-'}) vs Rezka({page_kp_id or '-'}, {page_imdb_id or '-'})")
                
                if kp_id and page_kp_id:
                    if str(kp_id) == str(page_kp_id):
                        print(f"      [+] MATCH KP ID: {kp_id}")
                        id_match = True
                    else:
                        print(f"      [-] CONFLICT KP ID: {kp_id} != {page_kp_id}")
                        id_conflict = True

                if imdb_id and page_imdb_id:
                    if str(imdb_id) == str(page_imdb_id):
                        print(f"      [+] MATCH IMDb ID: {imdb_id}")
                        id_match = True
                    else:
                        print(f"      [-] CONFLICT IMDb ID: {imdb_id} != {page_imdb_id}")
                        id_conflict = True

                if id_conflict: continue # Если ID не совпали - это точно другой фильм

                # --- ИТОГОВОЕ РЕШЕНИЕ ---
                is_valid = False
                if id_match:
                    is_valid = True # Если ID совпали - берем 100%
                elif (kp_id or imdb_id) and not (page_kp_id or page_imdb_id):
                    # Если в базе ID есть, а на резке нет - доверяем при Score >= 90
                    # (Например: совпадение названия, даже если год чуть отличается)
                    if current_score >= 90:
                        print(f"      [+] Trusting by title (Score: {current_score})")
                        is_valid = True
                    else:
                        print(f"      [-] Not enough data (Score: {current_score})")
                elif not (kp_id or imdb_id) and current_score >= 90:
                    # Если ID нет нигде - доверяем названию (снижаем порог)
                    is_valid = True
                
                # Специальный случай: Если у нас нет ID в базе, но они есть на Резке - принимаем при хорошем Score
                if not (kp_id or imdb_id) and (page_kp_id or page_imdb_id) and current_score >= 110:
                    is_valid = True
                
                if is_valid:
                    final_res = res
                    final_data = {
                        'kp_id': page_kp_id or kp_id,
                        'imdb_id': page_imdb_id or imdb_id,
                        'score': current_score,
                        'soup': soup
                    }
                    break

            if not final_res:
                print(f"  [-] No suitable results found.")
                cursor.execute("UPDATE items SET checked_rezka = 1 WHERE id = ?", (item_id,))
                conn.commit()
                continue

            print(f"  [+] CONFIRMED: {final_res['title']} (Score: {final_data['score']})")
            soup = final_data['soup']
            
            # Собираем данные
            found_kp_rating = kp_rating
            found_imdb_rating = imdb_rating
            
            # Парсим рейтинги заново (уже со страницы подтвержденного фильма)
            rate_blocks = soup.find_all(class_=re.compile(r'b-post__info_rates'))
            for block in rate_blocks:
                block_text = block.text.lower()
                val_tag = block.find(['span', 'b'], class_=['num', 'bold'])
                if not val_tag: val_tag = block.find('b')
                if val_tag:
                    try:
                        val = float(val_tag.text.strip().replace(',', '.'))
                        if 'кинопоиск' in block_text or 'kp' in block_text: found_kp_rating = val
                        elif 'imdb' in block_text: found_imdb_rating = val
                    except: pass

            # Постер (с исправлением протокола)
            found_poster = None
            og_image = soup.find('meta', property='og:image')
            if og_image and og_image.get('content'): found_poster = og_image['content']
            if not found_poster:
                itemprop_img = soup.find('img', itemprop='image')
                if itemprop_img: found_poster = itemprop_img.get('src') or itemprop_img.get('data-src')
            
            if found_poster and found_poster.startswith('//'):
                found_poster = 'https:' + found_poster

            # Обновляем
            cursor.execute("""
                UPDATE items 
                SET rezka_url = ?, 
                    kp_rating = CASE WHEN kp_rating IS NULL OR kp_rating = 0 THEN ? ELSE kp_rating END, 
                    imdb_rating = CASE WHEN imdb_rating IS NULL OR imdb_rating = 0 THEN ? ELSE imdb_rating END, 
                    kp_id = COALESCE(kp_id, ?), 
                    imdb_id = COALESCE(imdb_id, ?),
                    poster_url = CASE WHEN poster_url IS NULL OR poster_url = '' THEN ? ELSE poster_url END,
                    checked_rezka = 1
                WHERE id = ?
            """, (final_res['url'], found_kp_rating, found_imdb_rating, final_data['kp_id'], final_data['imdb_id'], found_poster, item_id))
            conn.commit()
            
            print(f"    [*] Success! Ratings: KP: {found_kp_rating or '-'}, IMDb: {found_imdb_rating or '-'}")
            if found_poster: print(f"    [*] Poster updated.")
                
        except Exception as e:

            print(f"  [-] Error processing {item_id}: {e}")
        
        time.sleep(0.5)
        
    conn.close()
    print("\n=== FINISHED ===")

if __name__ == "__main__":
    search_rezka_metadata()

