import sqlite3
import sys

# Set encoding for output
if sys.stdout.encoding != 'utf-8':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

def get_item_score(item):
    """Оценивает 'качество' карточки. Чем больше данных, тем выше балл."""
    score = 0
    if item['poster_url']: score += 10
    if item['description']: score += 5
    if item['kp_rating'] and item['kp_rating'] > 0: score += 5
    if item['imdb_rating'] and item['imdb_rating'] > 0: score += 5
    if item['year'] and item['year'] > 0: score += 3
    if item['kp_id']: score += 10 # ВАЖНО: наличие ID - сильный признак
    if item['imdb_id']: score += 10
    return score

def merge_by_ids_and_url():
    conn = sqlite3.connect("app_data.db")
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM items")
    all_items = [dict(row) for row in cursor.fetchall()]
    
    # Группировка по Rezka URL
    rezka_groups = {}
    for it in all_items:
        url = it['rezka_url']
        if url and url.strip():
            if url not in rezka_groups: rezka_groups[url] = []
            rezka_groups[url].append(it)
            
    merged_ids = set()
    merged_count = 0
    
    def do_merge(items_to_merge):
        nonlocal merged_count
        if len(items_to_merge) < 2: return
        
        items_to_merge.sort(key=get_item_score, reverse=True)
        master = items_to_merge[0]
        master_id = master['id']
        
        for i in range(1, len(items_to_merge)):
            dup = items_to_merge[i]
            dup_id = dup['id']
            if dup_id == master_id or dup_id in merged_ids: continue
            
            print(f"Merging: '{dup['title']}' ({dup['year']}, ID:{dup_id}) -> '{master['title']}' ({master['year']}, ID:{master_id})")
            
            # Перенос данных
            cursor.execute("UPDATE releases SET item_id = ? WHERE item_id = ?", (master_id, dup_id))
            cursor.execute("INSERT OR IGNORE INTO collection_items (collection_id, item_id) SELECT collection_id, ? FROM collection_items WHERE item_id = ?", (master_id, dup_id))
            cursor.execute("DELETE FROM collection_items WHERE item_id = ?", (dup_id,))
            
            # Если у дубликата есть ID, которых нет у мастера - переносим (хотя в теории они должны быть одинаковые если мы по ним мержим)
            if dup['kp_id'] and not master['kp_id']:
                cursor.execute("UPDATE items SET kp_id = ? WHERE id = ?", (dup['kp_id'], master_id))
            if dup['imdb_id'] and not master['imdb_id']:
                cursor.execute("UPDATE items SET imdb_id = ? WHERE id = ?", (dup['imdb_id'], master_id))
            
            if dup['is_ignored']:
                cursor.execute("UPDATE items SET is_ignored = 1 WHERE id = ?", (master_id,))
                
            cursor.execute("DELETE FROM items WHERE id = ?", (dup_id,))
            merged_ids.add(dup_id)
            merged_count += 1

    print("--- Merging by Rezka URL ---")
    for url, items in rezka_groups.items():
        if len(items) > 1:
            do_merge(items)
            
    conn.commit()
    conn.close()
    print(f"Done. Merged {merged_count} items.")

if __name__ == "__main__":
    merge_by_ids_and_url()
