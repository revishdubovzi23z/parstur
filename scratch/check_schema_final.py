import sqlite3

def check_schema():
    conn = sqlite3.connect('app_data.db')
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(items);")
    cols = cursor.fetchall()
    for col in cols:
        print(f"{col[0]}: {col[1]} ({col[2]})")
    conn.close()

if __name__ == "__main__":
    check_schema()
