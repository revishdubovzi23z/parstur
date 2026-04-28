import sqlite3
import re
import sys
import os

# Исправляем кодировку вывода для Windows
if sys.stdout.encoding != 'utf-8':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

def clean_t(t):
    if not t: return ""
    # Убираем все после слеша
    t = t.split(' / ')[0].split('/')[0]
    # Убираем год в скобках (2024)
    t = re.sub(r'\(?\d{4}\)?', '', t)
    # Убираем мусор в скобках
    t = re.sub(r'\(.*?\)', '', t)
    t = re.sub(r'\[.*?\]', '', t)
    # Убираем теги качества
    t = re.sub(r'(?i)SATRip|Web-DL|BDRip|1080p|720p|4K|HDR|HEVC|AVC|MVO|DUB|L1|VO', '', t)
    # Чистим лишние пробелы и знаки
    t = t.replace('.', ' ').replace('_', ' ')
    # Заменяем латинскую 'x' на русскую 'х' для корректного поиска дубликатов
    t = t.replace('x', 'х').replace('X', 'Х')
    return t.strip().lower()

def get_item_score(item):
    """Оценивает 'качество' карточки. Чем больше данных, тем выше балл."""
    score = 0
    if item['poster_url']: score += 10
    if item['description']: score += 5
    if item['kp_rating'] and item['kp_rating'] > 0: score += 5
    if item['imdb_rating'] and item['imdb_rating'] > 0: score += 5
    if item['year'] and item['year'] > 0: score += 3
    if item['kp_id']: score += 2
    return score

def merge_duplicates():
    print("\n" + "="*50)
    print("=== ЗАПУСК ГЛУБОКОЙ ОЧИСТКИ ДУБЛИКАТОВ ===")
    print("="*50)
    
    conn = sqlite3.connect("app_data.db")
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM items")
    all_items = [dict(row) for row in cursor.fetchall()]
    
    # Группируем по нескольким признакам:
    # 1. (нормализованное_название, категория)
    name_groups = {}
    # 2. kp_id
    kp_groups = {}
    # 3. imdb_id
    imdb_groups = {}
    # 4. rezka_url
    rezka_groups = {}
    
    for it in all_items:
        # Группировка по названию
        t_clean = clean_t(it['title'])
        if t_clean:
            key = (t_clean, it['category_id'])
            if key not in name_groups: name_groups[key] = []
            name_groups[key].append(it)
            
        # Группировка по КП
        if it['kp_id']:
            k = it['kp_id']
            if k not in kp_groups: kp_groups[k] = []
            kp_groups[k].append(it)
            
        # Группировка по IMDb
        if it['imdb_id']:
            i = it['imdb_id']
            if i not in imdb_groups: imdb_groups[i] = []
            imdb_groups[i].append(it)
            
        # Группировка по Rezka URL
        if it['rezka_url']:
            r = it['rezka_url']
            if r not in rezka_groups: rezka_groups[r] = []
            rezka_groups[r].append(it)
    
    merged_ids = set() # Чтобы не обрабатывать один и тот же ID дважды
    merged_total = 0
    
    # --- Функция для слияния списка дубликатов ---
    def do_merge(items_to_merge, reason):
        nonlocal merged_total
        if len(items_to_merge) < 2: return
        
        # Сортируем: лучшие карточки вперед
        items_to_merge.sort(key=get_item_score, reverse=True)
        master = items_to_merge[0]
        master_id = master['id']
        
        if master_id in merged_ids: return # Уже был поглощен или стал мастером

        from difflib import SequenceMatcher
        
        for i in range(1, len(items_to_merge)):
            dup = items_to_merge[i]
            dup_id = dup['id']
            if dup_id == master_id or dup_id in merged_ids: continue
            
            # Проверка на схожесть названий (если слияние не по жестким ID)
            if reason not in ["Kinopoisk ID", "IMDb ID"]:
                t1 = clean_t(master['title'])
                t2 = clean_t(dup['title'])
                similarity = SequenceMatcher(None, t1, t2).ratio()
                if similarity < 0.6:
                    # Слишком разные названия, пропускаем
                    continue

            print(f"  [СЛИЯНИЕ ({reason})] '{dup['title']}' ({dup['year']}) -> '{master['title']}' ({master['year']})")
            
            # 1. Переносим раздачи
            cursor.execute("UPDATE releases SET item_id = ? WHERE item_id = ?", (master_id, dup_id))
            # 2. Переносим закладки
            cursor.execute("INSERT OR IGNORE INTO collection_items (collection_id, item_id) SELECT collection_id, ? FROM collection_items WHERE item_id = ?", (master_id, dup_id))
            cursor.execute("DELETE FROM collection_items WHERE item_id = ?", (dup_id,))
            # 3. Статус игнора
            if dup['is_ignored']:
                cursor.execute("UPDATE items SET is_ignored = 1 WHERE id = ?", (master_id,))
            
            # 4. Переносим поиск (item_search_names)
            cursor.execute("UPDATE OR IGNORE item_search_names SET item_id = ? WHERE item_id = ?", (master_id, dup_id))
            cursor.execute("DELETE FROM item_search_names WHERE item_id = ?", (dup_id,))

            # 5. Удаляем
            cursor.execute("DELETE FROM items WHERE id = ?", (dup_id,))
            merged_ids.add(dup_id)
            merged_total += 1

    # Сначала сливаем по жестким ID (это 100% дубликаты)
    print("--- Поиск по Kinopoisk ID ---")
    for kp, items in kp_groups.items():
        do_merge(items, "Kinopoisk ID")
        
    print("--- Поиск по IMDb ID ---")
    for imdb, items in imdb_groups.items():
        do_merge(items, "IMDb ID")
        
    print("--- Поиск по Rezka URL ---")
    for url, items in rezka_groups.items():
        do_merge(items, "Rezka URL")

    # Затем по названию и году (как раньше)
    print("--- Поиск по названию и году ---")
    for key, items in name_groups.items():
        # Тут логика сложнее - внутри группы могут быть разные года (разные фильмы)
        # Отфильтровываем тех, кто уже удален
        active_items = [it for it in items if it['id'] not in merged_ids]
        if len(active_items) < 2: continue
        
        active_items.sort(key=get_item_score, reverse=True)
        
        # Разделяем на подгруппы по годам (совпадают или 0)
        master_list = []
        for it in active_items:
            placed = False
            for master_bundle in master_list:
                master = master_bundle[0]
                y1 = it['year'] or 0
                y2 = master['year'] or 0
                if y1 == y2 or y1 == 0 or y2 == 0 or abs(y1 - y2) <= 1:
                    master_bundle[1].append(it)
                    placed = True
                    break
            if not placed:
                master_list.append((it, []))
        
        for master, duplicates in master_list:
            if duplicates:
                do_merge([master] + duplicates, "Название/Год")

    conn.commit()
    conn.close()
    print("="*50)
    print(f"=== ГОТОВО! Удалено {merged_total} дубликатов. ===")
    print("="*50)

if __name__ == "__main__":
    merge_duplicates()

