import sqlite3

def update_island():
    conn = sqlite3.connect('app_data.db')
    cursor = conn.cursor()
    cursor.execute('UPDATE items SET imdb_id = NULL, imdb_rating = 0.0, is_metadata_fixed = 1 WHERE id = 4362')
    conn.commit()
    print("Updated ID 4362")
    conn.close()

if __name__ == "__main__":
    update_island()
