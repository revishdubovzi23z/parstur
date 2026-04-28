import sqlite3
import sys

# Set encoding for output
if sys.stdout.encoding != 'utf-8':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

def find_items():
    conn = sqlite3.connect('app_data.db')
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # 1. Search by Rezka URL for the first item
    rezka_url = 'https://rezka.ag/series/drama/87986-veruyuschie-zhenschiny-biblii-2026.html'
    print(f"Searching for Rezka URL: {rezka_url}")
    cursor.execute("SELECT * FROM items WHERE rezka_url = ?", (rezka_url,))
    items = cursor.fetchall()
    for it in items:
        print(f"Found ID: {it['id']}, Title: {it['title']}, IMDb ID: {it['imdb_id']}, IMDb Rating: {it['imdb_rating']}")
        
    # 2. Search for 'Самая, самая'
    print("\nSearching for 'Самая, самая'")
    cursor.execute("SELECT * FROM items WHERE title LIKE ?", ('%Самая, самая%',))
    items = cursor.fetchall()
    for it in items:
        print(f"Found ID: {it['id']}, Title: {it['title']}, IMDb ID: {it['imdb_id']}, IMDb Rating: {it['imdb_rating']}, Rezka: {it['rezka_url']}")

    conn.close()

if __name__ == "__main__":
    find_items()
