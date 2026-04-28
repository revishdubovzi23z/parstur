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
    try:
        with open('kinopoiskocenki.csv', mode='r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f, delimiter=';')
            for row in reader:
                # Some CSVs use different column names, let's check
                title = row.get('русское название') or row.get('title') or ""
                orig_title = row.get('оригинальное название') or row.get('original_title') or ""
                if search_term.lower() in title.lower() or search_term_en.lower() in orig_title.lower():
                    print(row)
    except Exception as e:
        print(f"Error reading Kinopoisk CSV: {e}")

    print(f"\n--- Searching for '{search_term_en}' in IMDBOCENKI.csv ---")
    try:
        with open('IMDBOCENKI.csv', mode='r', encoding='utf-8') as f:
            # IMDb CSV usually has 'Title' column
            reader = csv.DictReader(f)
            for row in reader:
                title = row.get('Title') or ""
                if search_term_en.lower() in title.lower():
                    print(row)
    except Exception as e:
        print(f"Error reading IMDb CSV: {e}")

if __name__ == "__main__":
    search_tron()
