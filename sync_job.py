import sqlite3
import requests
import time
import datetime
import re
import sys
import os
import unicodedata
from rutor_parser import RutorParser
from tmdb_client import TMDBClient
from kinopoisk_client import KinopoiskClient
from app_core import RUTOR_CATEGORIES, VIDEO_CATEGORY_IDS

# Настройки фильтрации года
MIN_YEAR = int(os.getenv("SYNC_MIN_YEAR", 1900))
MAX_YEAR = int(os.getenv("SYNC_MAX_YEAR", 2099))

class TrackerAppCore:
    def __init__(self, db_path="app_data.db"):
        self.db_path = db_path
        self.parser = RutorParser()
        self.tmdb = TMDBClient()
        self.kinopoisk = KinopoiskClient()

    def get_last_sync_date(self, category_id=None):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            if category_id:
                # Берем дату последнего релиза именно в этой категории
                cursor.execute("""
                    SELECT MAX(r.date_added) 
                    FROM releases r 
                    JOIN items i ON r.item_id = i.id 
                    WHERE i.category_id = ?
                """, (category_id,))
            else:
                cursor.execute("SELECT MAX(date_added) FROM releases")
            
            res = cursor.fetchone()[0]
            if res:
                return res
            # Если в этой категории ничего нет — берем за последние 30 дней
            import datetime
            return (datetime.date.today() - datetime.timedelta(days=30)).isoformat()

    def update_last_sync_date(self, date_str):
        # В нашей структуре дата берется из самих релизов, так что это опционально
        pass

def parse_rutor_date(date_str):
    # Форматы: "25 Апр 26", "Сегодня 12:34", "Вчера 10:15"
    from datetime import datetime, date, timedelta
    now = datetime.now()
    
    MONTHS = {
        'Янв': 1, 'Фев': 2, 'Мар': 3, 'Апр': 4, 'Май': 5, 'Июн': 6,
        'Июл': 7, 'Авг': 8, 'Сен': 9, 'Окт': 10, 'Ноя': 11, 'Дек': 12
    }
    
    date_str = date_str.strip()
    try:
        if 'Сегодня' in date_str:
            t_str = date_str.replace('Сегодня', '').strip()
            h, m = map(int, t_str.split(':'))
            return now.replace(hour=h, minute=m, second=0, microsecond=0).isoformat(sep=' ')
        
        if 'Вчера' in date_str:
            t_str = date_str.replace('Вчера', '').strip()
            h, m = map(int, t_str.split(':'))
            yesterday = now - timedelta(days=1)
            return yesterday.replace(hour=h, minute=m, second=0, microsecond=0).isoformat(sep=' ')
        
        parts = date_str.split()
        if len(parts) >= 3:
            day = int(parts[0])
            month = MONTHS.get(parts[1], 1)
            year = int(parts[2])
            if year < 100: year += 2000
            # Если времени нет, ставим 00:00:00
            return datetime(year, month, day).isoformat(sep=' ')
    except:
        pass
        
    return now.isoformat(sep=' ')

CATEGORIES_TO_SYNC = [
    {"id": 1, "use_tmdb": True},
    {"id": 4, "use_tmdb": True},
    {"id": 5, "use_tmdb": True},
    {"id": 16, "use_tmdb": True},
    {"id": 6, "use_tmdb": False},
    {"id": 7, "use_tmdb": True},
    {"id": 10, "use_tmdb": False},
    {"id": 15, "use_tmdb": False},
    {"id": 8, "use_tmdb": False},
    {"id": 12, "use_tmdb": False}
]

# Добавляем имена из центрального хранилища
for c in CATEGORIES_TO_SYNC:
    c["name"] = RUTOR_CATEGORIES.get(c["id"], f"Unknown {c['id']}")

def clean_title(t):
    # Глубокая очистка для объединения дублей - СОХРАНЯЕМ обе части (RU / EN)
    t = re.sub(r'\(.*?\)', '', t)
    t = re.sub(r'\[.*?\]', '', t)
    t = re.sub(r'(?i)\b(UHD|BDRemu[xх]|BDRip|Web-DL|Blu-Ray|Remux|1080p|720p|4K|HDR|HEVC|SATRip)\b', '', t)
    return t.strip().lower()

def deduplicate_releases(raw_list):
    """
    Группирует раздачи по чистому названию и году.
    """
    groups = {}
    for r in raw_list:
        # Используем "чистое" название для ключа группировки
        c_title = clean_title(r["parsed_title"])
        key = (c_title, r["year"])
        
        if key not in groups:
            groups[key] = {
                "display_title": r["parsed_title"], # Название для отображения
                "year": r["year"],
                "releases": []
            }
        groups[key]["releases"].append(r)
    return groups

def run_sync(mode="video", manual_min_date=None):
    app = TrackerAppCore()
    # Если передана ручная дата — используем её, иначе берем из базы
    if manual_min_date:
        target_date = manual_min_date
        print(f"=== ЗАПУСК ПАРСИНГА (Режим: {mode}, РУЧНАЯ ДАТА: {target_date}) ===")
    else:
        target_date = app.get_last_sync_date(category_id=None)
        print(f"=== ЗАПУСК ПАРСИНГА (Режим: {mode}, Цель: {target_date}) ===")
    
    with sqlite3.connect(app.db_path, timeout=30.0) as conn:
        cursor = conn.cursor()
        
        for cat in CATEGORIES_TO_SYNC:
            cat_id = cat["id"]
            cat_name = cat["name"]
            use_tmdb = cat["use_tmdb"]
            
            # Логика разделения кнопок
            if mode == "video" and not use_tmdb: continue
            if mode == "other" and use_tmdb: continue
            
            # Определяем дату именно для этой категории (если не задана вручную)
            current_target = manual_min_date if manual_min_date else app.get_last_sync_date(category_id=cat_id)
            print(f"\n--- Категория: {cat_name} (Ищем новее {current_target}) ---")
            
            all_raw_releases = []
            for page in range(20): # Глубина 20 страниц
                raw = app.parser.get_category_releases(cat_id, page=page)
                if not raw: break
                
                new_ones = []
                for r in raw:
                    # 1. Проверка даты добавления на Рутор (с точностью до времени)
                    rutor_dt = parse_rutor_date(r["date_str"])
                    if rutor_dt < current_target:
                        continue
                        
                    # 2. Фильтр по году выпуска (только для видео-категорий)
                    if cat_id in VIDEO_CATEGORY_IDS:
                        ry = r.get("year")
                        if ry:
                            if ry < MIN_YEAR or ry > MAX_YEAR:
                                continue
                        else:
                            # Если года нет в названии, но мы в категории видео - 
                            # можно либо пропускать, либо оставлять. Оставим для ручной проверки.
                            pass

                    new_ones.append(r)

                all_raw_releases.extend(new_ones)
                
                print(f"  Стр {page}: новых (с учетом фильтров) {len(new_ones)}")
                if len(new_ones) < len(raw) - 5: # Если пошли старые раздачи
                    break
                time.sleep(0.3)
            
            if not all_raw_releases: continue
            
            unique_movies = deduplicate_releases(all_raw_releases)
            for key, movie_data in unique_movies.items():
                clean_t_key, year = key
                display_title = movie_data["display_title"]
                
                # Ищем существующий фильм УМНЫМ способом (сначала точно, потом по чистому названию)
                cursor.execute("SELECT id, title FROM items WHERE year=? AND category_id=?", (year, cat_id))
                potential_matches = cursor.fetchall()
                
                item_id = None
                for p_id, p_title in potential_matches:
                    if clean_title(p_title) == clean_t_key:
                        item_id = p_id
                        break
                
                is_new_item = False
                if not item_id:
                    is_new_item = True
                    print(f"\n[НОВЫЙ] 🎬 {display_title} ({year})")
                    # ПЫТАЕМСЯ ДОСТАТЬ ID ПРЯМО С РУТОРА (для точности)
                    rutor_kp_id = None
                    rutor_imdb_id = None
                    
                    if cat_id in [1, 4, 5, 16, 7]:
                        for rel_idx, rel in enumerate(movie_data["releases"]):
                            if rutor_kp_id and rutor_imdb_id: break
                                
                            try:
                                rel_url = f"{app.parser.mirror}/torrent/{rel['rutor_id']}"
                                print(f"  🔍 Рутор (1.1): {rel_url}")
                                resp = requests.get(rel_url, timeout=20)
                                if resp.status_code == 200:
                                    # --- Достаем H1 ---
                                    from bs4 import BeautifulSoup
                                    soup = BeautifulSoup(resp.text, "html.parser")
                                    h1 = soup.find("h1")
                                    if h1:
                                        full_h1_title = h1.text.strip()
                                        if "Раздача не существует" not in full_h1_title:
                                            display_title = app.parser.clean_display_title(full_h1_title)
                                            print(f"    ✨ Название уточнено: {display_title}")

                                    # Ищем KP ID
                                    if not rutor_kp_id:
                                        kp_match = re.search(r'kinopoisk\.ru/rating/(\d+)\.gif', resp.text)
                                        if not kp_match: kp_match = re.search(r'kinopoisk\.ru/(?:film|series)/(\d+)', resp.text)
                                        if kp_match:
                                            rutor_kp_id = kp_match.group(1)
                                            print(f"    ✅ Нашел KP ID: {rutor_kp_id}")
                                    
                                    # Ищем IMDb ID
                                    if not rutor_imdb_id:
                                        imdb_match = re.search(r'imdb\.com/title/(tt\d+)', resp.text)
                                        if imdb_match:
                                            rutor_imdb_id = imdb_match.group(1)
                                            print(f"    ✅ Нашел IMDb ID: {rutor_imdb_id}")
                            except Exception as e:
                                print(f"    ⚠️ Ошибка парсинга страницы: {e}")
                            
                            if len(movie_data["releases"]) > 1: time.sleep(0.4)

                        # --- Глубокий поиск ---
                        if not rutor_kp_id or not rutor_imdb_id:
                            try:
                                search_term = display_title.split(' / ')[0].split('/')[0].strip()
                                search_term = re.sub(r'\(.*?\)', '', search_term)
                                search_term = re.sub(r'\[.*?\]', '', search_term).strip()
                                
                                print(f"  🔍 Рутор (1.2 Глубокий поиск): {search_term}")
                                search_results = app.parser.search_releases(search_term)
                                matches = [res for res in search_results if res.get('year') and year and abs(res.get('year') - year) <= 1]
                                
                                if matches:
                                    print(f"    🔎 Найдено в архиве: {len(matches)}. Проверяю...")
                                    for m_idx, m in enumerate(matches[:3]):
                                        if rutor_kp_id and rutor_imdb_id: break
                                        rid = m['rutor_id']
                                        time.sleep(0.4)
                                        resp = requests.get(f"{app.parser.mirror}/torrent/{rid}", timeout=20)
                                        if resp.status_code == 200:
                                            if not rutor_kp_id:
                                                m_kp = re.search(r'kinopoisk\.ru/rating/(\d+)\.gif', resp.text)
                                                if not m_kp: m_kp = re.search(r'film/(\d+)', resp.text)
                                                if m_kp: 
                                                    rutor_kp_id = m_kp.group(1)
                                                    print(f"      ✅ Нашел KP ID в архиве: {rutor_kp_id}")
                                            if not rutor_imdb_id:
                                                m_imdb = re.search(r'imdb\.com/title/(tt\d+)', resp.text)
                                                if m_imdb: 
                                                    rutor_imdb_id = m_imdb.group(1)
                                                    print(f"      ✅ Нашел IMDb ID в архиве: {rutor_imdb_id}")
                                else:
                                    print("    ⚠️ В архиве Рутора совпадений не найдено.")
                            except Exception as e:
                                print(f"    ⚠️ Ошибка глубокого поиска: {e}")

                    poster = ""
                    desc = ""
                    imdb_id = rutor_imdb_id
                    imdb_rating = 0.0
                    clean_display_title = display_title
                    
                    if use_tmdb:
                        if app.tmdb.is_limited:
                            print("  ⚠️ Лимит TMDB исчерпан, пропускаю обогащение.")
                            tmdb_data = None
                        else:
                            tmdb_data = None
                            if imdb_id:
                                print(f"  🔍 TMDB (2.1 по ID): {imdb_id}")
                                tmdb_data = app.tmdb.find_by_imdb_id(imdb_id)
                            
                            if not tmdb_data:
                                search_title = display_title.split(' / ')[0].split('/')[0].strip()
                                print(f"  🔍 TMDB (2.2 поиск): {search_title} ({year})")
                                tmdb_data = app.tmdb.search_movie(search_title, year)

                            if tmdb_data:
                                poster = tmdb_data.get("poster_url", "")
                                desc = tmdb_data.get("description", "")
                                if tmdb_data.get("title"):
                                    clean_display_title = tmdb_data["title"]
                                    if year and str(year) not in clean_display_title:
                                        clean_display_title += f" ({year})"
                                if not imdb_id: imdb_id = tmdb_data.get("imdb_id", "")
                                print(f"    🎯 TMDB: данные получены (Постер: {'✅' if poster else '❌'})")
                            else:
                                print("    ⚠️ TMDB: ничего не найдено.")
                    
                    # Сохраняем
                    title_norm = ""
                    search_names = []
                    t_clean = re.sub(r'\(.*?\)', '', clean_display_title)
                    t_clean = re.sub(r'\[.*?\]', '', t_clean)
                    t_clean = re.sub(r'(?i)\b(UHD|BDRemu[xх]|BDRip|Web-DL|Blu-Ray|Remux|1080p|720p|4K|HDR|HEVC|SATRip)\b', '', t_clean)
                    parts = [p.strip() for p in t_clean.split('/') if p.strip()]
                    for p in parts:
                        for pp in p.split(' / '):
                            if pp.strip():
                                import unicodedata as ud
                                search_names.append(ud.normalize('NFC', pp.strip()).lower())
                    
                    search_names = list(set(search_names))
                    if search_names: title_norm = search_names[0]

                    cursor.execute('''
                        INSERT OR IGNORE INTO items (title, year, category_id, poster_url, description, imdb_id, kp_id, imdb_rating, kp_rating, is_metadata_fixed, title_norm)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?)
                    ''', (clean_display_title, year, cat_id, poster, desc, imdb_id, rutor_kp_id, imdb_rating, title_norm))
                    item_id = cursor.lastrowid
                    
                    if not item_id:
                        cursor.execute("SELECT id FROM items WHERE title=? AND year=? AND category_id=?", (clean_display_title, year, cat_id))
                        row = cursor.fetchone()
                        if row: item_id = row[0]
                    
                    for sn in search_names:
                        cursor.execute("INSERT INTO item_search_names (item_id, name_norm) VALUES (?, ?)", (item_id, sn))
                        
                    print(f"  ➕ ДОБАВЛЕН: {display_title} ({year})")
                
                # Добавляем раздачи
                added_any = False
                for rel in movie_data["releases"]:
                    cursor.execute("SELECT 1 FROM releases WHERE rutor_id=?", (rel["rutor_id"],))
                    if not cursor.fetchone():
                        if not added_any and not is_new_item:
                             print(f"  🔗 Добавлен новый релиз к существующему фильму: {display_title}")
                             added_any = True
                        
                        # Дата релиза (чтобы поднялся наверх)
                        rel_date = parse_rutor_date(rel["date_str"])
                        cursor.execute('''
                            INSERT INTO releases (item_id, rutor_id, torrent_title, quality, date_added, magnet, link)
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                        ''', (item_id, rel["rutor_id"], rel["full_title"], rel["quality"], rel_date, rel["magnet"], rel["link"]))
                        print(f"    └─ Новая раздача: [{rel['quality']}] {rel['full_title'][:50]}... ({rel_date})")
            
            conn.commit()
    print("\n=== Готово! ===")

if __name__ == "__main__":
    # Аргументы: [1] mode, [2] min_year, [3] max_year, [4] manual_min_date
    mode = sys.argv[1] if len(sys.argv) > 1 else "video"
    manual_min_date = None
    
    # Если переданы года аргументами - переопределяем те, что из .env
    if len(sys.argv) > 2:
        try:
            val = sys.argv[2]
            if '-' in val and len(val) > 5: # Похоже на дату YYYY-MM-DD
                manual_min_date = val
            else:
                MIN_YEAR = int(val)
                print(f"Переопределен MIN_YEAR: {MIN_YEAR}")
        except: pass
    if len(sys.argv) > 3:
        try:
            val = sys.argv[3]
            if '-' in val and len(val) > 5: # Похоже на дату YYYY-MM-DD
                manual_min_date = val
            else:
                MAX_YEAR = int(val)
                print(f"Переопределен MAX_YEAR: {MAX_YEAR}")
        except: pass
    
    if len(sys.argv) > 4:
        manual_min_date = sys.argv[4]

    if manual_min_date:
        print(f"Используется ручная дата начала: {manual_min_date}")

    run_sync(mode, manual_min_date)
