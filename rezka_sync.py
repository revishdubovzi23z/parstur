import sqlite3
import requests
import time
import datetime
import re
import sys
import os
from HdRezkaApi import HdRezkaApi, HdRezkaSearch

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
    print(f"=== ЗАПУСК REZKA SYNC (Всего к обработке: {total_count}) ===")
    
    searcher = HdRezkaSearch("https://rezka.ag")
    
    for idx, row in enumerate(items, 1):
        try:
            item_id = row['id']
            title = row['title']
            year = row['year']
            kp_id = row['kp_id']
            imdb_id = row['imdb_id']
            kp_rating = row['kp_rating']
            imdb_rating = row['imdb_rating']
            rezka_url = row['rezka_url']
            
            # Чистим название
            # Чистим название от мусора (скобки, технические пометки)
            search_title = title.split(' / ')[0].split('/')[0]
            search_title = re.sub(r'\(.*?\)', '', search_title)
            search_title = re.sub(r'\[.*?\]', '', search_title)
            # Удаляем технические термины Rutor (UHD, BDRemux и т.д.)
            search_title = re.sub(r'(?i)\b(UHD|BDRemu[xх]|BDRip|Web-DL|Blu-Ray|Remux|1080p|720p|4K|HDR|HEVC)\b', '', search_title)
            search_title = search_title.strip()
            
            print(f"\n[{idx}/{total_count}] 🎬 {title} ({year})", flush=True)
            if kp_id or imdb_id:
                print(f"    📋 Имеем ID: KP:{kp_id or '-'}, IMDb:{imdb_id or '-'}", flush=True)
            print(f"    🔍 Поиск: {search_title}", flush=True)
            
            results = []
            # Сначала ищем с годом для точности
            try:
                results = searcher.fast_search(f"{search_title} {year}")
            except Exception as e:
                print(f"  ⚠️ Ошибка поиска: {e}")
                time.sleep(2)
                continue
            
            # Если ничего не нашли с годом, пробуем просто по названию
            if not results:
                try:
                    results = searcher.fast_search(search_title)
                except: pass
                
            def normalize_title(t):
                t = (t or "").lower()
                t = t.replace('x', 'х')
                t = re.sub(r'[^a-zа-я0-9\s]', '', t)
                return ' '.join(t.split())

            norm_search = normalize_title(search_title)
            
            # Сортируем результаты по баллам
            results_with_scores = []
            for res in results:
                res_name = res['title']
                res_url = res['url']
                res_clean_name = re.sub(r'\(.*?\)', '', res_name).strip()
                norm_res = normalize_title(res_clean_name)
                
                year_match = re.search(r'\((\d{4})\)', res_name)
                if not year_match: year_match = re.search(r'-(\d{4})\.html', res_url)
                res_year = int(year_match.group(1)) if year_match else None
                
                score = 0
                if norm_search == norm_res: score += 100
                elif norm_search in norm_res or norm_res in norm_search: score += 50
                
                if year and res_year:
                    diff = abs(year - res_year)
                    if diff == 0: score += 50
                    elif diff == 1: score += 20
                    elif diff >= 2: score -= 200
                
                results_with_scores.append({'res': res, 'score': score, 'year': res_year})
            
            # Сортируем: сначала самые высокие баллы
            results_with_scores.sort(key=lambda x: x['score'], reverse=True)
            
            final_res = None
            final_data = {}

            for item in results_with_scores:
                res = item['res']
                score = item['score']
                res_year = item['year']
                
                if score < 80: continue # Слишком слабое совпадение
                
                print(f"    🔎 Проверяю: {res['title']} (Score: {score})")
                rezka = HdRezkaApi(res['url'])
                soup = rezka.soup
                
                # 1. СБОР ВСЕХ ВОЗМОЖНЫХ ID СО СТРАНИЦЫ
                page_kp_id = None
                page_imdb_id = None
                
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

                # Б) Из скрытых ссылок (Base64)
                links = soup.find_all('a', href=re.compile(r'/help/'))
                import base64
                for link in links:
                    try:
                        b64_url = link['href'].split('/help/')[1].split('.html')[0]
                        missing_padding = len(b64_url) % 4
                        if missing_padding: b64_url += '=' * (4 - missing_padding)
                        real_url = base64.b64decode(b64_url).decode('utf-8')
                        if 'kinopoisk.ru' in real_url:
                            kp_m = re.search(r'/film/(\d+)|/series/(\d+)|/(\d+)/', real_url)
                            if kp_m and not page_kp_id: page_kp_id = next(g for g in kp_m.groups() if g)
                        elif 'imdb.com' in real_url:
                            imdb_m = re.search(r'/title/(tt\d+)', real_url)
                            if imdb_m and not page_imdb_id: page_imdb_id = imdb_m.group(1)
                    except: pass

                # 2. ВЕРИФИКАЦИЯ
                is_valid = True
                print(f"      🔎 Проверка ID: База({kp_id or '-'}) vs Резка({page_kp_id or '-'})", flush=True)
                
                # Если оба ID есть и они РАЗНЫЕ - это точно не наш фильм
                if kp_id and page_kp_id and str(kp_id) != str(page_kp_id):
                    print(f"      ❌ ОТКЛОНЕНО: Несовпадение KP ID ({kp_id} != {page_kp_id})")
                    is_valid = False
                
                if imdb_id and page_imdb_id and str(imdb_id) != str(page_imdb_id):
                    print(f"      ❌ ОТКЛОНЕНО: Несовпадение IMDb ID ({imdb_id} != {page_imdb_id})")
                    is_valid = False
                
                # Если у нас есть ID, а на Резке НЕТ - доверяем только идеальному совпадению (score > 140)
                if (kp_id or imdb_id) and not (page_kp_id or page_imdb_id) and score < 140:
                    print(f"      ❌ ОТКЛОНЕНО: У нас есть ID, на Резке нет, а совпадение слабое (Score: {score})")
                    is_valid = False

                if is_valid:
                    final_res = res
                    final_data = {
                        'kp_id': page_kp_id or kp_id,
                        'imdb_id': page_imdb_id or imdb_id,
                        'score': score,
                        'soup': soup
                    }
                    break
                else:
                    print(f"      ❌ Отклонено: не прошел верификацию ID или года.")

            if not final_res:
                print(f"  ❌ Подходящих результатов не найдено.")
                cursor.execute("UPDATE items SET checked_rezka = 1 WHERE id = ?", (item_id,))
                conn.commit()
                continue

            print(f"  ✅ ПОДТВЕРЖДЕНО: {final_res['title']} (Score: {final_data['score']})")
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
                SET rezka_url = ?, kp_rating = ?, imdb_rating = ?, 
                    kp_id = COALESCE(kp_id, ?), imdb_id = COALESCE(imdb_id, ?),
                    poster_url = COALESCE(poster_url, ?),
                    checked_rezka = 1
                WHERE id = ?
            """, (final_res['url'], found_kp_rating, found_imdb_rating, final_data['kp_id'], final_data['imdb_id'], found_poster, item_id))
            conn.commit()
            
            print(f"    📊 Успех! Рейтинги: KP: {found_kp_rating or '-'}, IMDb: {found_imdb_rating or '-'}")
            if found_poster: print(f"    🖼️ Постер подтянут.")
                
        except Exception as e:

            print(f"  ❌ Ошибка обработки {item_id}: {e}")
        
        time.sleep(0.5)
        
    conn.close()
    print("\n=== FINISHED ===")

if __name__ == "__main__":
    search_rezka_metadata()

