import os
import sys

sys.path.append(os.path.abspath("."))
from db import Database

db = Database()
conn = db.get_connection()
cursor = conn.cursor()

print("Searching for items...")
# Find items
items1 = db.get_items(where_clause="title LIKE ?", params=("%Бухта вдов%",), conn=conn)
items2 = db.get_items(where_clause="title LIKE ?", params=("%Уидоус-Бэй%",), conn=conn)

print(f"Found with 'Бухта вдов': {len(items1)}")
for it in items1:
    print(f"  ID: {it['id']}, Title: {it['title']}, Year: {it['year']}, KP: {it['kp_id']}")

print(f"Found with 'Уидоус-Бэй': {len(items2)}")
for it in items2:
    print(f"  ID: {it['id']}, Title: {it['title']}, Year: {it['year']}, KP: {it['kp_id']}")

if items1 and items2:
    master = items1[0]
    dup = items2[0]

    # Let's make sure they are different items
    if master["id"] != dup["id"]:
        print(f"\nMerging {dup['id']} ({dup['title']}) -> {master['id']} ({master['title']})")

        # Follow the logic from cleanup_duplicates.py
        db.reassign_releases(dup["id"], master["id"], conn=conn)
        db.merge_collection_items(dup["id"], master["id"], conn=conn)
        db.delete_collection_items_by_item(dup["id"], conn=conn)

        if dup["is_ignored"]:
            db.update_item(master["id"], conn=conn, is_ignored=1)

        db.reassign_search_names(dup["id"], master["id"], conn=conn)
        db.delete_search_names_by_item(dup["id"], conn=conn)
        db.delete_item(dup["id"], conn=conn)

        conn.commit()
        print("Successfully merged!")
    else:
        print("Both searches returned the same item!")
else:
    print("Could not find both items to merge.")

conn.close()
