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
            
            print(f"[{idx}/{total_count}] Search Rezka: {search_title} ({year})")
            
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
                
            best_match = None
            found_name = ""
            
            def normalize_title(t):
                t = t.lower()
                t = t.replace('x', 'х')
                t = re.sub(r'[^a-zа-я0-9\s]', '', t)
                return ' '.join(t.split())

            norm_search = normalize_title(search_title)
            best_score = -1
            
            for res in results:
                res_name = res['title']
                res_url = res['url']
                
                res_clean_name = re.sub(r'\(.*?\)', '', res_name).strip()
                norm_res = normalize_title(res_clean_name)
                
                # Ищем год в названии
                year_match = re.search(r'\((\d{4})\)', res_name)
                if not year_match:
                    year_match = re.search(r'-(\d{4})\.html', res_url)
                
                res_year = int(year_match.group(1)) if year_match else None
                
                score = 0
                if norm_search == norm_res:
                    score += 100
                elif norm_search in norm_res or norm_res in norm_search:
                    score += 50
                
                is_res_series = '/series/' in res_url
                
                if year and res_year:
                    diff = abs(year - res_year)
                    if diff == 0: score += 50
                    elif diff == 1: score += 20
                    elif is_res_series and diff <= 15:
                        # Для сериалов разница лет не так критична
                        score += 10
                    elif diff >= 2: 
                        score -= 200 
                
                if score > best_score and score > 30:
                    best_score = score
                    best_match = res_url
                    found_name = res_name
            
            if best_match:
                print(f"  MATCH: {found_name}")
                rezka = HdRezkaApi(best_match)
                soup = rezka.soup
                
                found_kp_id = kp_id
                found_imdb_id = imdb_id
                found_kp_rating = kp_rating
                found_imdb_rating = imdb_rating
                
                # Парсинг рейтингов
                rate_blocks = soup.find_all(class_=re.compile(r'b-post__info_rates'))
                for block in rate_blocks:
                    block_text = block.text.lower()
                    val_tag = block.find(['span', 'b'], class_=['num', 'bold'])
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

                # Парсинг ID
                links = soup.find_all('a', href=re.compile(r'/help/'))
                import base64
                for link in links:
                    try:
                        b64_url = link['href'].split('/help/')[1].split('.html')[0]
                        # Fix padding
                        missing_padding = len(b64_url) % 4
                        if missing_padding: b64_url += '=' * (4 - missing_padding)
                        
                        real_url = base64.b64decode(b64_url).decode('utf-8')
                        if 'kinopoisk.ru' in real_url:
                            m = re.search(r'film/(\d+)', real_url)
                            if m: found_kp_id = m.group(1)
                        elif 'imdb.com' in real_url:
                            m = re.search(r'title/(tt\d+)', real_url)
                            if m: found_imdb_id = m.group(1)
                    except: pass

                # Обновляем в базе
                cursor.execute("""
                    UPDATE items 
                    SET rezka_url = ?, kp_rating = ?, imdb_rating = ?, 
                        kp_id = COALESCE(kp_id, ?), imdb_id = COALESCE(imdb_id, ?),
                        checked_rezka = 1
                    WHERE id = ?
                """, (best_match, found_kp_rating, found_imdb_rating, found_kp_id, found_imdb_id, item_id))
                conn.commit()
                print(f"    [+] UPDATED: KP: {found_kp_rating}, IMDb: {found_imdb_rating}")
            else:
                print(f"  [-] NOT FOUND on Rezka")
                cursor.execute("UPDATE items SET checked_rezka = 1 WHERE id = ?", (item_id,))
                conn.commit()
                
        except Exception as e:
            print(f"  ❌ Ошибка обработки {item_id}: {e}")
        
        time.sleep(0.5)
        
    conn.close()
    print("\n=== FINISHED ===")

if __name__ == "__main__":
    search_rezka_metadata()
