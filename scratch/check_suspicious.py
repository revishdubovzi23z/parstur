import sqlite3
conn = sqlite3.connect('app_data.db')
cursor = conn.cursor()
titles = ['Лило и Стич', 'Общий сын', 'А как иначе', 'Пьетро Бонго', 'Холостяк в Италии', 'Solo Mio']
for t in titles:
    cursor.execute("SELECT id, title, imdb_id, kp_id FROM items WHERE title LIKE ?", (f"%{t}%",))
    rows = cursor.fetchall()
    print(f"Search for '{t}':", rows)
conn.close()
