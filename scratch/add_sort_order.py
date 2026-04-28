import sqlite3

def update_db():
    conn = sqlite3.connect('app_data.db')
    cursor = conn.cursor()
    try:
        cursor.execute("ALTER TABLE collections ADD COLUMN sort_order INTEGER DEFAULT 0")
        conn.commit()
        print("Column sort_order added successfully.")
    except sqlite3.OperationalError:
        print("Column sort_order already exists.")
    
    # Инициализируем порядок, если он нулевой
    cursor.execute("SELECT id FROM collections ORDER BY id")
    rows = cursor.fetchall()
    for i, (col_id,) in enumerate(rows):
        cursor.execute("UPDATE collections SET sort_order = ? WHERE id = ?", (i, col_id))
    
    conn.commit()
    conn.close()

if __name__ == "__main__":
    update_db()
