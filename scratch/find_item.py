import sqlite3

def find_goszashita():
    conn = sqlite3.connect('app_data.db')
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM items WHERE title LIKE 'Госзащита%' AND year = 2025")
    row = cursor.fetchone()
    if row:
        for k in row.keys():
            print(f"{k}: {row[k]}")
    else:
        print("Not found")
    conn.close()

if __name__ == "__main__":
    find_goszashita()
