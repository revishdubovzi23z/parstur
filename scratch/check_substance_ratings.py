import sqlite3

def check_substance():
    conn = sqlite3.connect('app_data.db')
    cursor = conn.cursor()
    cursor.execute("SELECT kp_rating, imdb_rating, rezka_url FROM items WHERE id = 3467")
    row = cursor.fetchone()
    print(f"KP: {row[0]}, IMDb: {row[1]}, URL: {row[2]}")
    conn.close()

if __name__ == "__main__":
    check_substance()
