import sqlite3
import sys

# Set encoding for output
if sys.platform == 'win32':
    import codecs
    sys.stdout = codecs.getwriter('utf-8')(sys.stdout.detach())

def check_db():
    conn = sqlite3.connect('app_data.db')
    cursor = conn.cursor()
    
    # Get all tables
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = [t[0] for t in cursor.fetchall()]
    
    for table in tables:
        print(f"\n--- Table: {table} ---")
        cursor.execute(f"PRAGMA table_info({table});")
        cols = cursor.fetchall()
        for col in cols:
            print(col)
            
    # Search for 'домочатцы'
    print("\nSearching for 'домочатцы' in all tables...")
    for table in tables:
        cursor.execute(f"PRAGMA table_info({table});")
        columns = [col[1] for col in cursor.fetchall()]
        text_cols = [c for c in columns if 'title' in c or 'name' in c]
        
        for col in text_cols:
            try:
                cursor.execute(f"SELECT * FROM {table} WHERE {col} LIKE ?", ('%домочатцы%',))
                res = cursor.fetchall()
                if res:
                    print(f"Found in {table}.{col}:")
                    for row in res:
                        print(row)
            except Exception as e:
                pass

    conn.close()

if __name__ == "__main__":
    check_db()
