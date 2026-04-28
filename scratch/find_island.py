import sqlite3
import sys

if sys.stdout.encoding != 'utf-8':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

def find_island():
    conn = sqlite3.connect('app_data.db')
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    search_term = '%Забытый остров%'
    print(f"Searching for: {search_term}")
    cursor.execute("SELECT id, title, year, imdb_id FROM items WHERE title LIKE ?", (search_term,))
    items = cursor.fetchall()
    for it in items:
        print(dict(it))
        
    conn.close()

if __name__ == "__main__":
    find_island()
