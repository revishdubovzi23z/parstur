import sqlite3

def update_schema():
    conn = sqlite3.connect('app_data.db')
    cursor = conn.cursor()
    
    try:
        cursor.execute("ALTER TABLE items ADD COLUMN rezka_url TEXT")
        print("Added rezka_url column.")
    except Exception as e:
        print(f"rezka_url column error: {e}")
        
    try:
        cursor.execute("ALTER TABLE items ADD COLUMN checked_rezka INTEGER DEFAULT 0")
        print("Added checked_rezka column.")
    except Exception as e:
        print(f"checked_rezka column error: {e}")
        
    conn.commit()
    conn.close()

if __name__ == '__main__':
    update_schema()
