import sqlite3

def upgrade_db():
    conn = sqlite3.connect('app_data.db')
    cursor = conn.cursor()
    
    # Добавляем колонку для личной оценки, если её нет
    try:
        cursor.execute('ALTER TABLE items ADD COLUMN user_rating INTEGER')
        print("Добавлена колонка user_rating")
    except:
        print("Колонка user_rating уже существует")

    # Добавляем колонку rutor_id в таблицу releases для дедубликации
    try:
        cursor.execute('ALTER TABLE releases ADD COLUMN rutor_id TEXT')
        print("Добавлена колонка rutor_id в таблицу releases")
    except:
        print("Колонка rutor_id уже существует")

    # Добавляем колонку magnet в таблицу releases
    try:
        cursor.execute('ALTER TABLE releases ADD COLUMN magnet TEXT')
        print("Добавлена колонка magnet в таблицу releases")
    except:
        print("Колонка magnet уже существует")
        
    conn.commit()
    conn.close()

if __name__ == "__main__":
    upgrade_db()
