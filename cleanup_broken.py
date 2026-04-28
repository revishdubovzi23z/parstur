import sqlite3

conn = sqlite3.connect('app_data.db')
cursor = conn.cursor()

# Релизы без ссылок
cursor.execute("SELECT COUNT(*) FROM releases WHERE link IS NULL OR link = ''")
count = cursor.fetchone()[0]
print(f'Релизов без ссылки: {count}')

# Удаляем item-ы у которых все релизы без ссылок (старые битые записи)
cursor.execute("""
    DELETE FROM items 
    WHERE category_id IN (1,4,5,16,6,7,15)
    AND id NOT IN (SELECT DISTINCT item_id FROM releases WHERE link IS NOT NULL AND link != '')
    AND is_metadata_fixed = 0
""")
deleted_items = conn.total_changes
print(f'Удалено битых карточек без релизов: {deleted_items}')

conn.commit()
conn.close()
print('Готово!')
