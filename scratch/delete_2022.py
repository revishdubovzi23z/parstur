import sqlite3

def delete_year_2022():
    conn = sqlite3.connect("app_data.db")
    cursor = conn.cursor()
    
    # Сначала найдем все ID предметов 2022 года
    cursor.execute("SELECT id FROM items WHERE year = 2022")
    ids = [row[0] for row in cursor.fetchall()]
    
    if not ids:
        print("Карточек 2022 года не найдено.")
        conn.close()
        return

    print(f"Найдено {len(ids)} карточек 2022 года. Удаляем...")
    
    # Удаляем из связанных таблиц
    placeholders = ",".join(["?"] * len(ids))
    
    # 1. Раздачи
    cursor.execute(f"DELETE FROM releases WHERE item_id IN ({placeholders})", ids)
    print(f"Удалено раздач: {cursor.rowcount}")
    
    # 2. Закладки
    cursor.execute(f"DELETE FROM collection_items WHERE item_id IN ({placeholders})", ids)
    print(f"Удалено из закладок: {cursor.rowcount}")
    
    # 3. Сами карточки
    cursor.execute(f"DELETE FROM items WHERE id IN ({placeholders})", ids)
    print(f"Удалено карточек: {cursor.rowcount}")
    
    conn.commit()
    conn.close()
    print("Удаление завершено.")

if __name__ == "__main__":
    delete_year_2022()
