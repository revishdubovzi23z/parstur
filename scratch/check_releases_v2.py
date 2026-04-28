import sqlite3
conn = sqlite3.connect('app_data.db')
cursor = conn.cursor()
cursor.execute("SELECT rutor_title, rutor_id FROM releases WHERE item_id=3612")
for row in cursor.fetchall():
    print(row)
conn.close()
