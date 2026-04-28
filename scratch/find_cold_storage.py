import sqlite3
import sys

if sys.stdout.encoding != 'utf-8':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

def find_cold_storage():
    conn = sqlite3.connect('app_data.db')
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    search_term = '%Cold Storage%'
    print(f"Searching for: {search_term} (2026)")
    cursor.execute("SELECT id, title, year, kp_rating, imdb_rating, rezka_url FROM items WHERE title LIKE ? AND year = 2026", (search_term,))
    items = cursor.fetchall()
    for it in items:
        print(dict(it))
        
    conn.close()

if __name__ == "__main__":
    find_cold_storage()
