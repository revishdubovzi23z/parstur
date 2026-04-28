import sqlite3

def cleanup_old_items():
    conn = sqlite3.connect('app_data.db')
    cursor = conn.cursor()
    
    # Сначала найдем ID всех карточек, которые старше 2022 года
    cursor.execute("SELECT id, title, year FROM items WHERE year < 2022 AND year != 0")
    to_delete = cursor.fetchall()
    
    if not to_delete:
        print("Ничего не найдено для удаления (все карточки 2022+ или без года).")
        conn.close()
        return

    print(f"Найдено {len(to_delete)} карточек старее 2022 года.")
    
    ids = [row[0] for row in to_delete]
    placeholders = ",".join(["?"] * len(ids))
    
    # Удаляем раздачи
    cursor.execute(f"DELETE FROM releases WHERE item_id IN ({placeholders})", ids)
    print(f"Удалено {cursor.rowcount} раздач.")
    
    # Удаляем сами карточки
    cursor.execute(f"DELETE FROM items WHERE id IN ({placeholders})", ids)
    print(f"Удалено {cursor.rowcount} карточек.")
    
    conn.commit()
    conn.close()
    print("Очистка завершена.")

if __name__ == "__main__":
    cleanup_old_items()
