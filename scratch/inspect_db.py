import sqlite3

def inspect():
    conn = sqlite3.connect('app_data.db')
    c = conn.cursor()
    
    print("=== Table: user_ratings ===")
    c.execute("PRAGMA table_info(user_ratings)")
    for col in c.fetchall():
        print(col)
        
    print("\n=== Indexes: user_ratings ===")
    c.execute("PRAGMA index_list(user_ratings)")
    for idx in c.fetchall():
        print(idx)
        c.execute(f"PRAGMA index_info({idx[1]})")
        print(f"  Columns: {c.fetchall()}")

    print("\n=== Indexes: items ===")
    c.execute("PRAGMA index_list(items)")
    for idx in c.fetchall():
        print(idx)
        c.execute(f"PRAGMA index_info({idx[1]})")
        print(f"  Columns: {c.fetchall()}")
        
    print("\n=== Sample user_ratings (first 5) ===")
    c.execute("SELECT * FROM user_ratings LIMIT 5")
    for row in c.fetchall():
        print(row)
        
    print("\n=== Sample user_ratings for 'Пацаны' or 'The Boys' ===")
    c.execute("SELECT * FROM user_ratings WHERE item_title LIKE '%Пацаны%' OR item_title LIKE '%The Boys%' OR original_title LIKE '%The Boys%'")
    for row in c.fetchall():
        print(row)

    print("\n=== Check 'Пацаны' in items table ===")
    c.execute("SELECT id, title, year, kp_id, imdb_id FROM items WHERE title LIKE '%Пацаны%'")
    for row in c.fetchall():
        print(row)

    conn.close()

if __name__ == "__main__":
    inspect()
