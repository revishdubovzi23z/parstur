import sqlite3

def check_db():
    conn = sqlite3.connect('app_data.db')
    cursor = conn.cursor()
    
    # Check tables
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = cursor.fetchall()
    print("Tables:", tables)
    
    for table in tables:
        table_name = table[0]
        print(f"\nSchema for {table_name}:")
        cursor.execute(f"PRAGMA table_info({table_name});")
        columns = cursor.fetchall()
        for col in columns:
            print(col)
            
    # Search for 'домочатцы'
    # Try to find which table might contain it. Likely 'media_items' or similar.
    # I'll search across all tables that have a 'title' column.
    
    for table in tables:
        table_name = table[0]
        cursor.execute(f"PRAGMA table_info({table_name});")
        columns = [col[1] for col in cursor.fetchall()]
        if 'title' in columns or 'original_title' in columns:
            search_col = 'title' if 'title' in columns else 'original_title'
            print(f"\nSearching in {table_name}...")
            cursor.execute(f"SELECT * FROM {table_name} WHERE {search_col} LIKE '%домочатцы%'")
            results = cursor.fetchall()
            if results:
                for res in results:
                    print(res)
            else:
                print("No results.")

    conn.close()

if __name__ == "__main__":
    check_db()
