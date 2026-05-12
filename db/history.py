import logging
from datetime import datetime

logger = logging.getLogger('parsclode.db')


class DbHistoryMixin:
        def get_watched_item_ids(self, conn=None) -> set[int]:
            with self._conn(conn) as c:
                rows = c.execute(
                    "SELECT imdb_id, kp_id, title_norm, original_title_norm, item_year FROM user_ratings"
                ).fetchall()
                if not rows:
                    return set()
                rated_imdb_ids = set()
                rated_kp_ids = set()
                rated_names = {}
                for imdb_id, kp_id, title_norm, orig_norm, item_year in rows:
                    if imdb_id:
                        rated_imdb_ids.add(imdb_id)
                    if kp_id:
                        rated_kp_ids.add(kp_id)
                    for name in [title_norm, orig_norm]:
                        if name:
                            rated_names.setdefault(name, []).append(item_year)

                watched_ids = set()
                if rated_imdb_ids:
                    placeholders = ",".join("?" * len(rated_imdb_ids))
                    for r in c.execute(
                        f"SELECT id FROM items WHERE imdb_id IN ({placeholders})",
                        list(rated_imdb_ids),
                    ).fetchall():
                        watched_ids.add(r[0])
                if rated_kp_ids:
                    placeholders = ",".join("?" * len(rated_kp_ids))
                    for r in c.execute(
                        f"SELECT id FROM items WHERE kp_id IN ({placeholders})",
                        list(rated_kp_ids),
                    ).fetchall():
                        watched_ids.add(r[0])
                if rated_names:
                    name_list = list(rated_names.keys())
                    chunk_size = 900
                    for i in range(0, len(name_list), chunk_size):
                        chunk = name_list[i : i + chunk_size]
                        placeholders = ",".join("?" * len(chunk))
                        for r in c.execute(
                            f"SELECT sn.item_id FROM item_search_names sn WHERE sn.name_norm IN ({placeholders})",
                            chunk,
                        ).fetchall():
                            watched_ids.add(r[0])
                return watched_ids

        def get_user_ratings_count(self) -> int:
            with self._conn() as c:
                return c.execute("SELECT COUNT(*) FROM user_ratings").fetchone()[0]

        def upsert_user_rating(self, item: dict, conn=None) -> None:
            with self._conn(conn) as c:
                row = c.execute(
                    "SELECT rowid FROM user_ratings WHERE (imdb_id IS NOT NULL AND imdb_id = ?) OR (kp_id IS NOT NULL AND kp_id = ?) OR (title_norm = ? AND item_year = ?) OR (original_title_norm = ? AND item_year = ?)",
                    (
                        item["imdb_id"],
                        item["kp_id"],
                        item["title_norm"],
                        item["year"],
                        item["orig_norm"],
                        item["year"],
                    ),
                ).fetchone()
                if row:
                    c.execute(
                        "UPDATE user_ratings SET imdb_id = COALESCE(imdb_id, ?), kp_id = COALESCE(kp_id, ?), rating = ?, original_title = COALESCE(original_title, ?), original_title_norm = COALESCE(original_title_norm, ?) WHERE rowid = ?",
                        (
                            item["imdb_id"],
                            item["kp_id"],
                            item["rating"],
                            item["orig_title"],
                            item["orig_norm"],
                            row[0],
                        ),
                    )
                else:
                    c.execute(
                        "INSERT INTO user_ratings (item_title, original_title, item_year, rating, service, imdb_id, kp_id, title_norm, original_title_norm) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            item["title"],
                            item["orig_title"],
                            item["year"],
                            item["rating"],
                            "merged",
                            item["imdb_id"],
                            item["kp_id"],
                            item["title_norm"],
                            item["orig_norm"],
                        ),
                    )

        def mark_visited(self) -> str:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with self._conn() as c:
                c.execute(
                    "INSERT OR REPLACE INTO app_state (key, value) VALUES ('last_visit', ?)",
                    (now,),
                )
            return now

        def get_last_visit(self) -> str | None:
            with self._conn() as c:
                row = c.execute("SELECT value FROM app_state WHERE key = 'last_visit'").fetchone()
                return row[0] if row else None

        def append_audit(
            self,
            *,
            action: str,
            item_id: int | None = None,
            field: str | None = None,
            old_value: str | None = None,
            new_value: str | None = None,
            conn=None,
        ) -> int:
            with self._conn(conn) as c:
                cur = c.execute(
                    "INSERT INTO audit_log (action, item_id, field, old_value, new_value) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (action, item_id, field, old_value, new_value),
                )
                return int(cur.lastrowid)

        def list_audit(self, *, limit: int = 50, item_id: int | None = None, conn=None) -> list[dict]:
            limit = max(1, min(int(limit), 500))
            with self._conn(conn) as c:
                if item_id is not None:
                    rows = c.execute(
                        "SELECT * FROM audit_log WHERE item_id = ? ORDER BY id DESC LIMIT ?",
                        (item_id, limit),
                    ).fetchall()
                else:
                    rows = c.execute(
                        "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?",
                        (limit,),
                    ).fetchall()
                return [dict(r) for r in rows]

        def mark_audit_undone(self, audit_id: int, conn=None) -> bool:
            with self._conn(conn) as c:
                cur = c.execute(
                    "UPDATE audit_log SET undone = 1 WHERE id = ? AND undone = 0",
                    (audit_id,),
                )
                return cur.rowcount > 0

