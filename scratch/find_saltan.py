import sqlite3
import sys

if sys.stdout.encoding != 'utf-8':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

def find_saltan():
    conn = sqlite3.connect('app_data.db')
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    search_term = '%Сказка о царе Салтане%'
    print(f"Searching for: {search_term} (2025)")
    cursor.execute("SELECT id, title, year, rezka_url, kp_id, kp_rating, imdb_rating FROM items WHERE title LIKE ? AND year = 2025", (search_term,))
    items = cursor.fetchall()
    for it in items:
        print(dict(it))
        
    conn.close()

if __name__ == "__main__":
    find_saltan()
