import sqlite3

def check_rezka_duplicates():
    conn = sqlite3.connect('app_data.db')
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT rezka_url, COUNT(*) as count 
        FROM items 
        WHERE rezka_url IS NOT NULL AND rezka_url != '' 
        GROUP BY rezka_url 
        HAVING count > 1
    """)
    duplicates = cursor.fetchall()
    
    print(f"Found {len(duplicates)} rezka_url duplicates.")
    for url, count in duplicates:
        print(f"\nURL: {url} (Count: {count})")
        cursor.execute("SELECT id, title, year, kp_id, imdb_id, category_id, is_ignored FROM items WHERE rezka_url = ?", (url,))
        items = cursor.fetchall()
        for item in items:
            print(f"  ID: {item[0]}, Title: {item[1]}, Year: {item[2]}, KP: {item[3]}, IMDB: {item[4]}, Cat: {item[5]}, Ignored: {item[6]}")
            
    conn.close()

if __name__ == "__main__":
    check_rezka_duplicates()
