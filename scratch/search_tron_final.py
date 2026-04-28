import sqlite3
import csv
import sys

if sys.stdout.encoding != 'utf-8':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

def search_tron():
    search_term = 'Трон'
    search_term_en = 'Tron'
    
    print(f"--- Searching for '{search_term}' in kinopoiskocenki.csv (UTF-16, Tab) ---")
    try:
        with open('kinopoiskocenki.csv', mode='r', encoding='utf-16') as f:
            reader = csv.DictReader(f, delimiter='\t')
            for row in reader:
                # Clean quotes from keys if any
                row = {k.strip('"'): v.strip('"') for k, v in row.items()}
                title = row.get('Title') or ""
                orig_title = row.get('Original Title') or ""
                if search_term.lower() in title.lower() or search_term_en.lower() in orig_title.lower():
                    print(row)
    except Exception as e:
        print(f"Error reading Kinopoisk CSV: {e}")

if __name__ == "__main__":
    search_tron()
