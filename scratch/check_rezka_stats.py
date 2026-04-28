import sqlite3

def get_stats():
    conn = sqlite3.connect('app_data.db')
    cursor = conn.cursor()
    
    video_cats = "(1, 4, 5, 16, 7)"
    
    cursor.execute(f"SELECT COUNT(*) FROM items WHERE category_id IN {video_cats} AND checked_rezka = 0")
    not_checked_rezka = cursor.fetchone()[0]
    
    cursor.execute(f"SELECT COUNT(*) FROM items WHERE category_id IN {video_cats} AND checked_rezka = 1")
    checked_rezka = cursor.fetchone()[0]
    
    conn.close()
    
    print(f"Не проверено на Rezka: {not_checked_rezka}")
    print(f"Проверено на Rezka: {checked_rezka}")

if __name__ == '__main__':
    get_stats()
