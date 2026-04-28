import sqlite3

def thorough_reset():
    conn = sqlite3.connect("app_data.db")
    cursor = conn.cursor()
    
    items = [
        {"id": 5034, "reset_imdb": True, "reset_kp": False}, # Прозрение
        {"id": 5033, "reset_imdb": False, "reset_kp": False}, # Большой человек
        {"id": 1597, "reset_imdb": True, "reset_kp": True},   # Сестра
        {"id": 1456, "reset_imdb": True, "reset_kp": False}, # Райский (Keep KP ID)
        {"id": 1430, "reset_imdb": False, "reset_kp": False} # Отпечатки
    ]
    
    for item in items:
        item_id = item["id"]
        
        # Base reset
        sql = "UPDATE items SET rezka_url = NULL, kp_rating = 0, imdb_rating = 0, checked_rezka = 0, is_metadata_fixed = 0"
        
        if item["reset_imdb"]:
            sql += ", imdb_id = NULL"
        if item["reset_kp"]:
            sql += ", kp_id = NULL"
            
        sql += " WHERE id = ?"
        
        cursor.execute(sql, (item_id,))
        print(f"Thoroughly reset item ID {item_id}")

    conn.commit()
    conn.close()

if __name__ == "__main__":
    thorough_reset()
