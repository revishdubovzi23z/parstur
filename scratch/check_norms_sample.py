import sqlite3

def check_norms():
    conn = sqlite3.connect('app_data.db')
    cursor = conn.cursor()
    cursor.execute("SELECT title, title_norm FROM items LIMIT 20")
    for row in cursor.fetchall():
        print(f"Title: {row[0]} | Norm: {row[1]}")
    conn.close()

if __name__ == "__main__":
    check_norms()
