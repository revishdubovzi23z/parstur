import sqlite3
import sys

# Настройка кодировки для вывода в консоль Windows
if sys.stdout.encoding != 'utf-8':
    import codecs
    sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')

def check_item(title_part):
    conn = sqlite3.connect('app_data.db')
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    print(f"--- Проверка '{title_part}' ---")
    cursor.execute("SELECT id, rutor_id, link, torrent_title FROM releases WHERE torrent_title LIKE ?", (f"%{title_part}%",))
    rels = cursor.fetchall()
    
    for r in rels:
        print(f"ID: {r['id']}")
        print(f"  rutor_id: {repr(r['rutor_id'])}")
        print(f"  link: {r['link']}")
        print(f"  title: {r['torrent_title']}")
            
    conn.close()

if __name__ == "__main__":
    check_item("Новичок")
