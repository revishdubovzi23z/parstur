import sqlite3

def check_db():
    conn = sqlite3.connect('app_data.db')
    cursor = conn.cursor()
    
    print("Schema for items:")
    cursor.execute("PRAGMA table_info(items);")
    for col in cursor.fetchall():
        print(col)
        
    print("\nCategories:")
    cursor.execute("SELECT * FROM categories;")
    for row in cursor.fetchall():
        print(row)
        
    # Search for 'домочатцы' case-insensitive
    print("\nSearching for 'домочатцы' (case-insensitive) in items:")
    cursor.execute("SELECT id, title, original_title, year, kp_id, imdb_id, status FROM items WHERE title LIKE '%домочатцы%' OR original_title LIKE '%домочатцы%'")
    results = cursor.fetchall()
    for res in results:
        print(res)
        
    # Also search for 'Housemates' just in case
    print("\nSearching for 'Housemates' (case-insensitive) in items:")
    cursor.execute("SELECT id, title, original_title, year, kp_id, imdb_id, status FROM items WHERE title LIKE '%Housemates%' OR original_title LIKE '%Housemates%'")
    results = cursor.fetchall()
    for res in results:
        print(res)

    conn.close()

if __name__ == "__main__":
    check_db()
