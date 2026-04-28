import sqlite3

def move_gold():
    conn = sqlite3.connect('app_data.db')
    cursor = conn.cursor()
    # Create category if not exists
    cursor.execute('INSERT OR IGNORE INTO categories (id, name) VALUES (100, "Моя коллекция")')
    # Move item 1620
    cursor.execute('UPDATE items SET category_id = 100 WHERE id = 1620')
    conn.commit()
    print("Moved Золотое дно to category 100 (Моя коллекция)")
    conn.close()

if __name__ == "__main__":
    move_gold()
