import logging
from datetime import datetime

logger = logging.getLogger('parsclode.db')


class DbCollectionsMixin:
        def get_collections(self) -> list[dict]:
            with self._conn() as c:
                return [
                    dict(r)
                    for r in c.execute(
                        "SELECT c.*, COUNT(ci.item_id) as count FROM collections c LEFT JOIN collection_items ci ON c.id = ci.collection_id GROUP BY c.id ORDER BY c.sort_order ASC, c.name ASC"
                    ).fetchall()
                ]

        def create_collection(self, name: str) -> None:
            with self._conn() as c:
                c.execute("INSERT INTO collections (name) VALUES (?)", (name,))

        def delete_collection(self, id: int) -> None:
            with self._conn() as c:
                c.execute("DELETE FROM collections WHERE id = ?", (id,))

        def rename_collection(self, id: int, name: str) -> None:
            with self._conn() as c:
                c.execute("UPDATE collections SET name = ? WHERE id = ?", (name, id))

        def toggle_collection_item(self, collection_id: int, item_id: int) -> str:
            with self._conn() as c:
                row = c.execute(
                    "SELECT 1 FROM collection_items WHERE collection_id = ? AND item_id = ?",
                    (collection_id, item_id),
                ).fetchone()
                if row:
                    c.execute(
                        "DELETE FROM collection_items WHERE collection_id = ? AND item_id = ?",
                        (collection_id, item_id),
                    )
                    return "removed"
                c.execute(
                    "INSERT INTO collection_items (collection_id, item_id, added_at) VALUES (?, ?, ?)",
                    (collection_id, item_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
                )
                return "added"

        def get_item_collections(self, item_id: int) -> list[int]:
            with self._conn() as c:
                return [
                    r[0]
                    for r in c.execute(
                        "SELECT collection_id FROM collection_items WHERE item_id = ?",
                        (item_id,),
                    ).fetchall()
                ]

        def get_item_collections_batch(self, item_ids: list[int]) -> dict[int, list[int]]:
            if not item_ids:
                return {}
            with self._conn() as c:
                placeholders = ",".join(["?"] * len(item_ids))
                rows = c.execute(
                    f"SELECT item_id, collection_id FROM collection_items WHERE item_id IN ({placeholders})",
                    item_ids,
                ).fetchall()
                result: dict[int, list[int]] = {i: [] for i in item_ids}
                for row in rows:
                    result[row["item_id"]].append(row["collection_id"])
                return result

        def save_collections_order(self, order: list[int]) -> None:
            with self._conn() as c:
                for i, col_id in enumerate(order):
                    c.execute("UPDATE collections SET sort_order = ? WHERE id = ?", (i, col_id))

        def export_collections(self) -> list[dict]:
            """Return a portable list of {name, sort_order, items[...]}.

            Each item dict carries enough identity (kp_id, imdb_id,
            rezka_url, title, original_title, year) for `import_collections`
            to find the same row on a different DB. Empty collections
            are included so re-importing recreates them too.
            """
            with self._conn() as c:
                cols = c.execute(
                    "SELECT id, name, sort_order FROM collections ORDER BY sort_order ASC, name ASC"
                ).fetchall()
                out: list[dict] = []
                for col in cols:
                    rows = c.execute(
                        """
                        SELECT i.kp_id, i.imdb_id, i.rezka_url, i.title,
                               i.original_title, i.year, ci.added_at
                        FROM collection_items ci
                        JOIN items i ON i.id = ci.item_id
                        WHERE ci.collection_id = ?
                        ORDER BY ci.added_at ASC, i.title ASC
                        """,
                        (col["id"],),
                    ).fetchall()
                    out.append(
                        {
                            "name": col["name"],
                            "sort_order": col["sort_order"],
                            "items": [dict(r) for r in rows],
                        }
                    )
            return out

        def import_collections(
            self,
            payload: list[dict],
            *,
            replace: bool = False,
        ) -> dict:
            """Apply an export back to this DB.

            payload — list of {name, sort_order?, items: [{kp_id?, imdb_id?,
                      rezka_url?, title?, year?, added_at?}, ...]}.
            replace — if True, every collection in `payload` is wiped
                      before re-adding its items (membership rewrite).
                      If False, items are merged in (INSERT OR IGNORE).
                      In neither case are local-only collections dropped.

            Returns {created_collections, updated_collections,
                     added_items, missing_items} for caller to report.
            """
            created = updated = added_items = missing_items = 0
            with self._conn() as c:
                for col in payload:
                    name = (col.get("name") or "").strip()
                    if not name:
                        continue
                    row = c.execute("SELECT id FROM collections WHERE name = ?", (name,)).fetchone()
                    if row is None:
                        sort_order = int(col.get("sort_order") or 0)
                        cur = c.execute(
                            "INSERT INTO collections (name, sort_order) VALUES (?, ?)",
                            (name, sort_order),
                        )
                        coll_id = int(cur.lastrowid)
                        created += 1
                    else:
                        coll_id = int(row["id"])
                        updated += 1
                    if replace:
                        c.execute(
                            "DELETE FROM collection_items WHERE collection_id = ?",
                            (coll_id,),
                        )
                    for ref in col.get("items") or []:
                        item_id = self._resolve_item_id(c, ref)
                        if item_id is None:
                            missing_items += 1
                            continue
                        added_at = ref.get("added_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        cur = c.execute(
                            "INSERT OR IGNORE INTO collection_items "
                            "(collection_id, item_id, added_at) VALUES (?, ?, ?)",
                            (coll_id, item_id, added_at),
                        )
                        if cur.rowcount:
                            added_items += 1
            return {
                "created_collections": created,
                "updated_collections": updated,
                "added_items": added_items,
                "missing_items": missing_items,
            }

        def merge_collection_items(self, old_item_id: int, new_item_id: int, conn=None) -> None:
            with self._conn(conn) as c:
                c.execute(
                    "INSERT OR IGNORE INTO collection_items (collection_id, item_id) SELECT collection_id, ? FROM collection_items WHERE item_id = ?",
                    (new_item_id, old_item_id),
                )

        def delete_collection_items_by_item(self, item_id: int, conn=None) -> None:
            with self._conn(conn) as c:
                c.execute("DELETE FROM collection_items WHERE item_id = ?", (item_id,))

