import sqlite3

def reset_prozrenie():
    conn = sqlite3.connect("app_data.db")
    cursor = conn.cursor()
    
    title = "Прозрение"
    year = 2026
    
    print(f"Searching for '{title}' ({year})...")
    
    cursor.execute("SELECT id, title, year, imdb_id, rezka_url FROM items WHERE title LIKE ? AND year = ?", (f"%{title}%", year))
    rows = cursor.fetchall()
    
    if not rows:
        print("Item not found.")
        conn.close()
        return
        
    for row in rows:
        item_id, found_title, found_year, imdb_id, rezka_url = row
        print(f"Found: ID={item_id}, Title='{found_title}', Year={found_year}")
        print(f"Current IMDb ID: {imdb_id}")
        print(f"Current Rezka URL: {rezka_url}")
        
        cursor.execute("""
            UPDATE items 
            SET imdb_id = NULL, 
                rezka_url = NULL,
                checked_rezka = 0,
                is_metadata_fixed = 0
            WHERE id = ?
        """, (item_id,))
        print(f"Successfully reset data for ID {item_id}.")
        
    conn.commit()
    conn.close()

if __name__ == "__main__":
    reset_prozrenie()
