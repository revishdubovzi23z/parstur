import sqlite3
import re

def clean_t(t):
    if not t: return ""
    t = t.split(' / ')[0].split('/')[0]
    t = re.sub(r'\(?\d{4}\)?', '', t)
    t = re.sub(r'\(.*?\)', '', t)
    t = re.sub(r'\[.*?\]', '', t)
    t = re.sub(r'(?i)SATRip|Web-DL|BDRip|1080p|720p|4K|HDR|HEVC|AVC|MVO|DUB|L1|VO', '', t)
    t = t.replace('.', ' ').replace('_', ' ')
    t = t.replace('x', 'х').replace('X', 'Х')
    return t.strip().lower()

def check_loose_duplicates():
    conn = sqlite3.connect('app_data.db')
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM items")
    items = [dict(row) for row in cursor.fetchall()]
    
    groups = {}
    for it in items:
        t_clean = clean_t(it['title'])
        key = (t_clean, it['category_id'])
        if key not in groups: groups[key] = []
        groups[key].append(it)
        
    found = 0
    for key, group in groups.items():
        if len(group) < 2: continue
        
        # Sort by year
        group.sort(key=lambda x: x['year'] or 0)
        
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                item1 = group[i]
                item2 = group[j]
                y1 = item1['year'] or 0
                y2 = item2['year'] or 0
                
                # Check if years are close (+/- 1 year)
                if abs(y1 - y2) <= 1:
                    print(f"\nPotential duplicate: '{item1['title']}' ({y1}) and '{item2['title']}' ({y2})")
                    print(f"  ID1: {item1['id']}, ID2: {item2['id']}")
                    print(f"  KP1: {item1['kp_id']}, KP2: {item2['kp_id']}")
                    found += 1
                    
    print(f"\nFound {found} potential loose duplicates.")
    conn.close()

if __name__ == "__main__":
    check_loose_duplicates()
