import sqlite3

def restore_ignored_data():
    conn = sqlite3.connect("app_data.db")
    cursor = conn.cursor()
    
    category_id = 16
    
    print(f"Attempting to restore data for IGNORED items in Category {category_id}...")
    
    # Get ignored items
    cursor.execute("SELECT id, title, year FROM items WHERE category_id = ? AND is_ignored = 1", (category_id,))
    ignored_items = cursor.fetchall()
    print(f"Found {len(ignored_items)} ignored items.")
    
    restored_count = 0
    for item_id, title, year in ignored_items:
        # Try to find in user_ratings
        # We need a normalized title match or exact match
        cursor.execute("""
            SELECT kp_id, imdb_id, rating 
            FROM user_ratings 
            WHERE (item_title = ? OR original_title = ?) AND item_year = ?
            LIMIT 1
        """, (title, title, year))
        
        rating_row = cursor.fetchone()
        if rating_row:
            kp_id, imdb_id, rating = rating_row
            cursor.execute("""
                UPDATE items 
                SET kp_id = ?, imdb_id = ?, kp_rating = ?, is_metadata_fixed = 1
                WHERE id = ?
            """, (kp_id, imdb_id, rating, item_id))
            restored_count += 1
            
    conn.commit()
    print(f"Successfully restored data for {restored_count} ignored items from user_ratings.")
    conn.close()

if __name__ == "__main__":
    restore_ignored_data()
