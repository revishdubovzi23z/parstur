import sqlite3
import csv
import sys

if sys.stdout.encoding != 'utf-8':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

def search_tron():
    search_term = 'Трон'
    search_term_en = 'Tron'
    
    print(f"--- Searching for '{search_term}' in user_ratings table ---")
    conn = sqlite3.connect('app_data.db')
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM user_ratings WHERE item_title LIKE ? OR original_title LIKE ?", (f'%{search_term}%', f'%{search_term_en}%'))
    rows = cursor.fetchall()
    for row in rows:
        print(dict(row))
    conn.close()

    print(f"\n--- Searching for '{search_term}' in kinopoiskocenki.csv ---")
    # Try different encodings for Kinopoisk CSV
    encodings = ['utf-8-sig', 'cp1251', 'utf-16']
    for enc in encodings:
        try:
            with open('kinopoiskocenki.csv', mode='r', encoding=enc) as f:
                content = f.read(100)
                # If first 2 bytes are 0xff 0xfe or 0xfe 0xff, it's UTF-16
                f.seek(0)
                reader = csv.DictReader(f, delimiter=';')
                found = False
                for row in reader:
                    title = row.get('русское название') or row.get('title') or ""
                    orig_title = row.get('оригинальное название') or row.get('original_title') or ""
                    if search_term.lower() in title.lower() or search_term_en.lower() in orig_title.lower():
                        print(row)
                        found = True
                if found: break # Stop if found with this encoding
        except Exception as e:
            continue

if __name__ == "__main__":
    search_tron()
