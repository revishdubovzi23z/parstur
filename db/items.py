import logging
import re
from datetime import datetime

import db.core
from app_core import VIDEO_CATEGORY_IDS
from db.core import _LARGE_ID_LIST_THRESHOLD, FILTER_RULE_FIELDS, _compile_filter_pattern

logger = logging.getLogger("parsclode.db")

from db.core import _materialize_id_list, _placeholders


class DbItemsMixin:
    def get_item(self, item_id: int, conn=None) -> dict | None:
        with self._conn(conn) as c:
            row = c.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
            return dict(row) if row else None

    def get_items(self, where_clause="1=1", params=(), conn=None) -> list[dict]:
        with self._conn(conn) as c:
            rows = c.execute(f"SELECT * FROM items WHERE {where_clause}", params).fetchall()
            return [dict(r) for r in rows]

    def get_items_count(self, where_clause="1=1", params=(), conn=None) -> int:
        with self._conn(conn) as c:
            return c.execute(f"SELECT COUNT(*) FROM items WHERE {where_clause}", params).fetchone()[
                0
            ]

    def find_existing_item(
        self,
        kp_id=None,
        imdb_id=None,
        title_norm=None,
        year=None,
        category_id=None,
        rezka_url=None,
        conn=None,
    ):
        with self._conn(conn) as c:
            # When the caller knows the category we're matching against (e.g.
            # sync_job processing a torrent from a specific Rutor category),
            # we must require the existing item to be in the same category.
            # The same external id (kp_id / imdb_id) can legitimately appear
            # in multiple of our categories — e.g. a feature film tied to a
            # series, an anime that is also classified as a cartoon — and
            # silently merging them would corrupt category filters in the UI.
            if kp_id:
                if category_id:
                    row = c.execute(
                        "SELECT id FROM items WHERE kp_id = ? AND category_id = ? LIMIT 1",
                        (str(kp_id), category_id),
                    ).fetchone()
                else:
                    row = c.execute(
                        "SELECT id FROM items WHERE kp_id = ? LIMIT 1",
                        (str(kp_id),),
                    ).fetchone()
                if row:
                    return row[0]
            if imdb_id:
                if category_id:
                    row = c.execute(
                        "SELECT id FROM items WHERE imdb_id = ? AND category_id = ? LIMIT 1",
                        (str(imdb_id), category_id),
                    ).fetchone()
                else:
                    row = c.execute(
                        "SELECT id FROM items WHERE imdb_id = ? LIMIT 1",
                        (str(imdb_id),),
                    ).fetchone()
                if row:
                    return row[0]
            if rezka_url:
                if category_id:
                    row = c.execute(
                        "SELECT id FROM items WHERE rezka_url = ? AND category_id = ? LIMIT 1",
                        (rezka_url, category_id),
                    ).fetchone()
                else:
                    row = c.execute(
                        "SELECT id FROM items WHERE rezka_url = ? LIMIT 1",
                        (rezka_url,),
                    ).fetchone()
                if row:
                    return row[0]
            if title_norm:
                from app_core import normalize_title

                norm = normalize_title(title_norm)
                if norm:
                    if year and category_id:
                        rows = c.execute(
                            "SELECT id, year FROM items WHERE title_norm = ? AND category_id = ?",
                            (norm, category_id),
                        ).fetchall()
                        for r in rows:
                            iy = r[1] or 0
                            if iy == year or iy == 0 or year == 0 or abs(iy - year) <= 1:
                                return r[0]
                    if category_id:
                        row = c.execute(
                            "SELECT id FROM items WHERE title_norm = ? AND category_id = ? LIMIT 1",
                            (norm, category_id),
                        ).fetchone()
                        if row:
                            return row[0]
                    row = c.execute(
                        "SELECT id FROM items WHERE title_norm = ? LIMIT 1",
                        (norm,),
                    ).fetchone()
                    if row:
                        return row[0]
                    rows = c.execute(
                        "SELECT item_id FROM item_search_names WHERE name_norm = ? LIMIT 5",
                        (norm,),
                    ).fetchall()
                    if rows:
                        for r in rows:
                            item_row = c.execute(
                                "SELECT year, category_id FROM items WHERE id = ?",
                                (r[0],),
                            ).fetchone()
                            if item_row:
                                iy = item_row[0] or 0
                                ic = item_row[1] or 0
                                if year and (
                                    iy == year or iy == 0 or year == 0 or abs(iy - year) <= 1
                                ):
                                    if not category_id or ic == category_id:
                                        return r[0]
            return None

    def insert_item(self, data: dict, conn=None) -> int | None:
        with self._conn(conn) as c:
            cols = ", ".join(data.keys())
            placeholders = ", ".join(["?"] * len(data))
            cur = c.execute(
                f"INSERT OR IGNORE INTO items ({cols}) VALUES ({placeholders})",
                list(data.values()),
            )
            item_id = cur.lastrowid
            if not item_id:
                row = c.execute(
                    "SELECT id FROM items WHERE title = ? AND year = ? AND category_id = ?",
                    (data.get("title"), data.get("year"), data.get("category_id")),
                ).fetchone()
                item_id = row[0] if row else None
            return item_id

    def update_item(self, item_id: int, conn=None, **fields) -> None:
        with self._conn(conn) as c:
            sets = [f"{k} = ?" for k in fields]
            params = list(fields.values()) + [item_id]
            c.execute(f"UPDATE items SET {', '.join(sets)} WHERE id = ?", params)

    def fill_item_metadata(self, item_id: int, conn=None, **fields) -> None:
        with self._conn(conn) as c:
            sets = []
            params = []
            for field, value in fields.items():
                if field in self._RATING_FIELDS:
                    sets.append(
                        f"{field} = CASE WHEN {field} = 0 OR {field} IS NULL THEN ? ELSE {field} END"
                    )
                elif field in self._EMPTY_TEXT_FIELDS:
                    sets.append(
                        f"{field} = CASE WHEN {field} IS NULL OR {field} = '' THEN ? ELSE {field} END"
                    )
                elif field in self._COALESCE_FIELDS:
                    sets.append(f"{field} = COALESCE({field}, ?)")
                else:
                    sets.append(f"{field} = ?")
                params.append(value)
            params.append(item_id)
            c.execute(f"UPDATE items SET {', '.join(sets)} WHERE id = ?", params)

    def delete_item(self, item_id: int, conn=None) -> None:
        with self._conn(conn) as c:
            c.execute("DELETE FROM releases WHERE item_id = ?", (item_id,))
            c.execute("DELETE FROM collection_items WHERE item_id = ?", (item_id,))
            c.execute("DELETE FROM item_search_names WHERE item_id = ?", (item_id,))
            c.execute("DELETE FROM items WHERE id = ?", (item_id,))

    def mark_checked(self, item_id: int, source: str, conn=None) -> None:
        col = f"checked_{source}"
        with self._conn(conn) as c:
            c.execute(f"UPDATE items SET {col} = 1 WHERE id = ?", (item_id,))

    def toggle_ignore(self, item_id: int, conn=None) -> int:
        with self._conn(conn) as c:
            row = c.execute("SELECT is_ignored FROM items WHERE id = ?", (item_id,)).fetchone()
            if not row:
                return -1
            new_state = 1 - row["is_ignored"]
            ignored_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S") if new_state == 1 else None
            c.execute(
                "UPDATE items SET is_ignored = ?, ignored_at = ? WHERE id = ?",
                (new_state, ignored_at, item_id),
            )
            return new_state

    def reset_item(self, item_id: int, fields: list[str], conn=None) -> None:
        field_map = {
            "poster": "poster_url = NULL",
            "poster_url": "poster_url = NULL",
            "description": "description = NULL",
            "kp_id": "kp_id = NULL",
            "imdb_id": "imdb_id = NULL",
            "rezka_url": "rezka_url = NULL",
            "ratings": "kp_rating = 0, imdb_rating = 0",
            "kp_rating": "kp_rating = 0",
            "imdb_rating": "imdb_rating = 0",
            "kinopub_id": "kinopub_id = NULL, kinopub_url = NULL, kinopub_type = NULL, checked_kinopub = 0",
            "title_norm": "title_norm = NULL",
        }
        updates = [field_map[f] for f in fields if f in field_map]
        if not updates:
            return
        updates.append("is_reprocessed = 0")
        updates.append("is_metadata_fixed = 0")
        updates.append("checked_rezka = 0")
        if any(f in ["kp_id", "ratings"] for f in fields):
            updates.append("checked_poiskkino = 0")
            updates.append("checked_tech = 0")
        updates = list(set(updates))
        with self._conn(conn) as c:
            sql = f"UPDATE items SET {', '.join(updates)} WHERE id = ?"
            c.execute(sql, (item_id,))
            from logging_config import setup_logging
            from settings import settings

            logger = setup_logging("db.items", settings.log_file_path)
            logger.info(f"[DB] Reset item {item_id}: fields={fields}, sql={sql}")

    def set_ids(
        self,
        item_id: int,
        kp_id: str | None = None,
        imdb_id: str | None = None,
        conn=None,
    ) -> None:
        updates = []
        params = []
        if kp_id is not None:
            updates.append("kp_id = ?")
            params.append(kp_id)
            updates.extend(["checked_poiskkino = 0", "checked_tech = 0", "checked_rezka = 0"])
        if imdb_id is not None:
            updates.append("imdb_id = ?")
            params.append(imdb_id)
            updates.append("checked_rezka = 0")
        if not updates:
            return
        updates.extend(["is_metadata_fixed = 0", "is_reprocessed = 0"])
        params.append(item_id)
        with self._conn(conn) as c:
            c.execute(f"UPDATE items SET {', '.join(updates)} WHERE id = ?", params)

    def kinopub_bind(
        self,
        item_id: int,
        *,
        kinopub_id: int,
        kinopub_type: str | None = None,
        kinopub_url: str | None = None,
        conn=None,
    ) -> dict | None:
        """Attach a kino.pub identifier to an item.

        Returns a dict with `before` and `after` snapshots of the
        kinopub_* columns, or None when the item does not exist.
        `checked_kinopub` is forced to 1 so the future sync_kinopub
        matcher (PR 4) skips already-bound rows during its sweep.
        """
        with self._conn(conn) as c:
            row = c.execute(
                "SELECT kinopub_id, kinopub_type, kinopub_url, checked_kinopub "
                "FROM items WHERE id = ?",
                (item_id,),
            ).fetchone()
            if row is None:
                return None
            before = {
                "kinopub_id": row["kinopub_id"],
                "kinopub_type": row["kinopub_type"],
                "kinopub_url": row["kinopub_url"],
            }
            c.execute(
                "UPDATE items SET kinopub_id = ?, kinopub_type = ?, "
                "kinopub_url = ?, checked_kinopub = 1 WHERE id = ?",
                (int(kinopub_id), kinopub_type, kinopub_url, item_id),
            )
            after = {
                "kinopub_id": int(kinopub_id),
                "kinopub_type": kinopub_type,
                "kinopub_url": kinopub_url,
            }
            return {"before": before, "after": after}

    def kinopub_unbind(self, item_id: int, conn=None) -> dict | None:
        """Detach the kino.pub identifier from an item.

        Returns the same `before`/`after` shape as `kinopub_bind`, or
        None when the item does not exist. `checked_kinopub` is reset
        to 0 so the next sync run can attempt a fresh match (the
        operator likely unbound because the previous match was wrong).
        """
        with self._conn(conn) as c:
            row = c.execute(
                "SELECT kinopub_id, kinopub_type, kinopub_url FROM items WHERE id = ?",
                (item_id,),
            ).fetchone()
            if row is None:
                return None
            before = {
                "kinopub_id": row["kinopub_id"],
                "kinopub_type": row["kinopub_type"],
                "kinopub_url": row["kinopub_url"],
            }
            c.execute(
                "UPDATE items SET kinopub_id = NULL, kinopub_type = NULL, "
                "kinopub_url = NULL, checked_kinopub = 0 WHERE id = ?",
                (item_id,),
            )
            after = {
                "kinopub_id": None,
                "kinopub_type": None,
                "kinopub_url": None,
            }
            return {"before": before, "after": after}

    def find_items_by_year_category(self, year: int, category_id: int, conn=None) -> list[dict]:
        with self._conn(conn) as c:
            rows = c.execute(
                "SELECT id, title FROM items WHERE year = ? AND category_id = ?",
                (year, category_id),
            ).fetchall()
            return [dict(r) for r in rows]

    def find_item_id_by_title_year_category(
        self, title: str, year: int, category_id: int, conn=None
    ) -> int | None:
        with self._conn(conn) as c:
            row = c.execute(
                "SELECT id FROM items WHERE title = ? AND year = ? AND category_id = ?",
                (title, year, category_id),
            ).fetchone()
            return row[0] if row else None

    def find_duplicate_item_id(
        self, title: str, year: int, category_id: int, exclude_id: int, conn=None
    ) -> int | None:
        with self._conn(conn) as c:
            row = c.execute(
                "SELECT id FROM items WHERE title = ? AND year = ? AND category_id = ? AND id != ?",
                (title, year, category_id, exclude_id),
            ).fetchone()
            return row[0] if row else None

    def get_releases(self, item_id: int, conn=None) -> list[dict]:
        with self._conn(conn) as c:
            rows = c.execute(
                "SELECT * FROM releases WHERE item_id = ? ORDER BY date_added DESC",
                (item_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def insert_release(self, data: dict, conn=None) -> int:
        with self._conn(conn) as c:
            cols = ", ".join(data.keys())
            placeholders = ", ".join(["?"] * len(data))
            cur = c.execute(
                f"INSERT INTO releases ({cols}) VALUES ({placeholders})",
                list(data.values()),
            )
            return cur.lastrowid

    def release_exists_by_rutor_id(self, rutor_id, conn=None) -> bool:
        with self._conn(conn) as c:
            row = c.execute("SELECT 1 FROM releases WHERE rutor_id = ?", (rutor_id,)).fetchone()
            return row is not None

    def reassign_release_if_orphan(self, rutor_id, new_item_id, conn=None) -> bool:
        with self._conn(conn) as c:
            row = c.execute(
                "SELECT item_id FROM releases WHERE rutor_id = ?", (rutor_id,)
            ).fetchone()
            if not row:
                return False
            old_item_id = row["item_id"]
            if old_item_id == new_item_id:
                return False
            owner = c.execute("SELECT 1 FROM items WHERE id = ?", (old_item_id,)).fetchone()
            if not owner:
                c.execute(
                    "UPDATE releases SET item_id = ? WHERE rutor_id = ?", (new_item_id, rutor_id)
                )
                return True
            return False

    def reassign_release_to_item(self, rutor_id, new_item_id, conn=None) -> bool:
        with self._conn(conn) as c:
            row = c.execute(
                "SELECT item_id FROM releases WHERE rutor_id = ?", (rutor_id,)
            ).fetchone()
            if not row:
                return False
            old_item_id = row["item_id"]
            if old_item_id == new_item_id:
                return False
            c.execute("UPDATE releases SET item_id = ? WHERE rutor_id = ?", (new_item_id, rutor_id))
            return True

    def get_release_item_id(self, rutor_id, conn=None) -> int | None:
        with self._conn(conn) as c:
            row = c.execute(
                "SELECT item_id FROM releases WHERE rutor_id = ?", (rutor_id,)
            ).fetchone()
            return row["item_id"] if row else None

    def get_last_release_date(self, category_id: int | None = None, conn=None) -> str | None:
        with self._conn(conn) as c:
            if category_id:
                row = c.execute(
                    "SELECT MAX(r.date_added) FROM releases r JOIN items i ON r.item_id = i.id WHERE i.category_id = ?",
                    (category_id,),
                ).fetchone()
            else:
                row = c.execute("SELECT MAX(date_added) FROM releases").fetchone()
            return row[0] if row and row[0] else None

    def reassign_releases(self, old_item_id: int, new_item_id: int, conn=None) -> None:
        with self._conn(conn) as c:
            c.execute(
                "UPDATE releases SET item_id = ? WHERE item_id = ?",
                (new_item_id, old_item_id),
            )

    def get_release_torrent_title(self, item_id: int, conn=None) -> str | None:
        with self._conn(conn) as c:
            row = c.execute(
                "SELECT torrent_title FROM releases WHERE item_id = ? LIMIT 1",
                (item_id,),
            ).fetchone()
            return row[0] if row else None

    def get_rutor_ids_for_item(self, item_id: int, conn=None) -> list[str]:
        with self._conn(conn) as c:
            rows = c.execute(
                "SELECT rutor_id FROM releases WHERE item_id = ?", (item_id,)
            ).fetchall()
            return [r[0] for r in rows]

    def get_feed(
        self,
        category_id: int = -1,
        collection_id: int | None = None,
        search: str | None = None,
        min_kp: float = 0.0,
        max_kp: float = 10.0,
        min_imdb: float = 0.0,
        max_imdb: float = 10.0,
        min_year: int | None = None,
        max_year: int | None = None,
        min_date: str | None = None,
        max_date: str | None = None,
        hide_ignored: bool = True,
        hide_rated: bool = False,
        hide_collected: bool = False,
        sort_by: str = "date_desc",
        page: int = 1,
        limit: int = 20,
    ) -> dict:
        with self._conn() as c:
            where_clauses = ["1=1"]
            params = []

            if collection_id:
                where_clauses.append(
                    "items.id IN (SELECT item_id FROM collection_items WHERE collection_id = ?)"
                )
                params.append(collection_id)
            elif category_id == -2:
                where_clauses.append("items.is_ignored = 1")
            else:
                video_cats_ph = _placeholders(VIDEO_CATEGORY_IDS)
                if category_id == -1:
                    where_clauses.append(f"items.category_id IN ({video_cats_ph})")
                    params.extend(VIDEO_CATEGORY_IDS)
                elif category_id == -100:
                    where_clauses.append(
                        f"items.category_id IN ({video_cats_ph}) "
                        "AND (items.poster_url IS NULL OR items.poster_url = '')"
                    )
                    params.extend(VIDEO_CATEGORY_IDS)
                elif category_id == -101:
                    where_clauses.append(
                        f"items.category_id IN ({video_cats_ph}) AND "
                        "(items.kp_rating = 0 OR items.kp_rating IS NULL OR "
                        "items.imdb_rating = 0 OR items.imdb_rating IS NULL)"
                    )
                    params.extend(VIDEO_CATEGORY_IDS)
                elif category_id == -102:
                    where_clauses.append(
                        f"items.category_id IN ({video_cats_ph}) "
                        "AND (items.kp_id IS NULL OR items.kp_id = '')"
                    )
                    params.extend(VIDEO_CATEGORY_IDS)
                elif category_id == -103:
                    where_clauses.append(
                        f"items.category_id IN ({video_cats_ph}) "
                        "AND (items.imdb_id IS NULL OR items.imdb_id = '')"
                    )
                    params.extend(VIDEO_CATEGORY_IDS)
                elif category_id == -104:
                    where_clauses.append(
                        f"items.category_id IN ({video_cats_ph}) "
                        "AND (items.kp_id IS NULL OR items.kp_id = '') "
                        "AND (items.imdb_id IS NULL OR items.imdb_id = '')"
                    )
                    params.extend(VIDEO_CATEGORY_IDS)
                elif category_id == -105:
                    where_clauses.append(
                        f"items.category_id IN ({video_cats_ph}) "
                        "AND (items.kinopub_id IS NULL OR items.kinopub_id = '')"
                    )
                    params.extend(VIDEO_CATEGORY_IDS)
                elif category_id != 0:
                    where_clauses.append("items.category_id = ?")
                    params.append(category_id)
            if hide_ignored and category_id != -2:
                where_clauses.append("COALESCE(items.is_ignored, 0) = 0")

            if search:
                search_val = f"%{search.lower()}%"
                fts_available = False
                try:
                    c.execute("SELECT count(*) FROM items_fts LIMIT 1")
                    fts_available = True
                except Exception:
                    pass
                if fts_available:
                    # Quote terms to avoid FTS syntax errors with hyphens (e.g. "Уидоус-Бэй")
                    fts_query = " OR ".join(f'"{term}"' for term in search.strip().split() if term)
                    where_clauses.append(
                        "items.id IN (SELECT rowid FROM items_fts WHERE items_fts MATCH ?)"
                    )
                    params.append(fts_query)
                else:
                    where_clauses.append(
                        "(items.title LIKE ? OR items.title_norm LIKE ? OR EXISTS (SELECT 1 FROM item_search_names sn WHERE sn.item_id = items.id AND sn.name_norm LIKE ?))"
                    )
                    params.extend([f"%{search}%", search_val, search_val])

            if min_date:
                where_clauses.append(
                    "items.id IN (SELECT item_id FROM releases WHERE date_added >= ?)"
                )
                params.append(min_date)
            if max_date:
                where_clauses.append(
                    "items.id IN (SELECT item_id FROM releases WHERE date_added <= ?)"
                )
                params.append(max_date)

            hide_temp_table: str | None = None
            if hide_rated:
                watched_ids = self.get_watched_item_ids(conn=c)
                if watched_ids:
                    collected_ids = set(
                        r[0]
                        for r in c.execute(
                            "SELECT DISTINCT item_id FROM collection_items"
                        ).fetchall()
                    )
                    hide_ids = watched_ids - collected_ids
                    if hide_ids:
                        hide_ids_list = list(hide_ids)
                        if len(hide_ids_list) >= _LARGE_ID_LIST_THRESHOLD:
                            # Too many to bind as '?' params — materialize.
                            hide_temp_table = _materialize_id_list(
                                c, hide_ids_list, "feed_hide_ids"
                            )
                            where_clauses.append(
                                f"items.id NOT IN (SELECT id FROM temp.{hide_temp_table})"
                            )
                        else:
                            where_clauses.append(
                                f"items.id NOT IN ({_placeholders(hide_ids_list)})"
                            )
                            params.extend(hide_ids_list)

            if hide_collected and not collection_id:
                where_clauses.append("items.id NOT IN (SELECT item_id FROM collection_items)")

            if min_kp > 0:
                where_clauses.append("items.kp_rating >= ?")
                params.append(min_kp)
            if max_kp < 10:
                where_clauses.append("items.kp_rating <= ?")
                params.append(max_kp)
            if min_imdb > 0:
                where_clauses.append("items.imdb_rating >= ?")
                params.append(min_imdb)
            if max_imdb < 10:
                where_clauses.append("items.imdb_rating <= ?")
                params.append(max_imdb)
            if min_year:
                where_clauses.append("items.year >= ?")
                params.append(min_year)
            if max_year:
                where_clauses.append("items.year <= ?")
                params.append(max_year)

            # 8.5 — apply enabled hide-rules at SQL level so pagination
            # stays correct (filtering after pagination would yield
            # short pages). Highlight-rules run post-fetch in Python.
            active_rules = self.list_filter_rules(only_enabled=True, conn=c)
            hide_rules = [r for r in active_rules if r["action"] == "hide"]
            highlight_rules = [r for r in active_rules if r["action"] == "highlight"]
            for rule in hide_rules:
                col = rule["field"]
                if col not in FILTER_RULE_FIELDS:
                    continue  # paranoia; should never happen
                where_clauses.append(f"NOT (COALESCE(items.{col}, '') REGEXP ?)")
                params.append(rule["pattern"])

            where_sql = " AND ".join(where_clauses)
            total_count = c.execute(
                f"SELECT COUNT(DISTINCT items.id) FROM items WHERE {where_sql}", params
            ).fetchone()[0]
            total_pages = (total_count + limit - 1) // limit if total_count > 0 else 1

            order_by = (
                "items.ignored_at DESC, latest_release DESC"
                if category_id == -2
                else (
                    "ci.added_at DESC, latest_release DESC"
                    if collection_id
                    else "latest_release DESC NULLS LAST"
                )
            )

            if sort_by == "kp_desc":
                where_clauses.append("items.kp_rating > 0")
                order_by = "items.kp_rating DESC, latest_release DESC NULLS LAST"
            elif sort_by == "kp_asc":
                where_clauses.append("items.kp_rating > 0")
                order_by = "items.kp_rating ASC, latest_release DESC NULLS LAST"
            elif sort_by == "imdb_desc":
                where_clauses.append("items.imdb_rating > 0")
                order_by = "items.imdb_rating DESC, latest_release DESC NULLS LAST"
            elif sort_by == "imdb_asc":
                where_clauses.append("items.imdb_rating > 0")
                order_by = "items.imdb_rating ASC, latest_release DESC NULLS LAST"

            join = ""
            join_params: list = []
            if collection_id:
                join = "JOIN collection_items ci ON items.id = ci.item_id AND ci.collection_id = ?"
                join_params.append(collection_id)
            query = (
                "SELECT items.*, (SELECT MAX(date_added) FROM releases "
                f"WHERE item_id = items.id) as latest_release FROM items {join} "
                f"WHERE {where_sql} ORDER BY {order_by} LIMIT ? OFFSET ?"
            )
            # Order matters: JOIN placeholders, then WHERE placeholders, then
            # LIMIT/OFFSET. SQLite binds left-to-right against the SQL.
            full_params = [*join_params, *params, limit, (page - 1) * limit]

            items = [dict(r) for r in c.execute(query, full_params).fetchall()]

            # Batch-load releases instead of issuing one SELECT per item.
            # The previous N+1 made the feed scale linearly with `limit`
            # even though the feed page size is bounded — that's a lot
            # of extra round-trips on slow disks / WAL contention.
            if items:
                ids = [item["id"] for item in items]
                ids_ph = _placeholders(ids)
                rel_rows = c.execute(
                    f"SELECT * FROM releases WHERE item_id IN ({ids_ph}) "
                    "ORDER BY item_id, date_added DESC",
                    ids,
                ).fetchall()
                releases_by_item: dict[int, list] = {iid: [] for iid in ids}
                for r in rel_rows:
                    releases_by_item.setdefault(r["item_id"], []).append(dict(r))
                for item in items:
                    item["releases"] = releases_by_item.get(item["id"], [])

            if collection_id:
                for item in items:
                    item["has_new_release"] = False
                    if item.get("latest_season", 0) > 0 and item.get("latest_episode", 0) > 0:
                        ci_row = c.execute(
                            "SELECT added_at FROM collection_items WHERE collection_id = ? AND item_id = ?",
                            (collection_id, item["id"]),
                        ).fetchone()
                        if ci_row and ci_row[0]:
                            try:
                                from datetime import datetime

                                added = datetime.strptime(ci_row[0][:19], "%Y-%m-%d %H:%M:%S")
                                season_key = f"s{item['latest_season']}e{item['latest_episode']}"
                                cache_row = c.execute(
                                    "SELECT value FROM app_state WHERE key = ?",
                                    (f"rezka_seen_{item['id']}",),
                                ).fetchone()
                                if not cache_row or cache_row[0] != season_key:
                                    item["has_new_release"] = True
                            except Exception:
                                pass
                        else:
                            item["has_new_release"] = True
                    elif item.get("latest_release"):
                        ci_row = c.execute(
                            "SELECT added_at FROM collection_items WHERE collection_id = ? AND item_id = ?",
                            (collection_id, item["id"]),
                        ).fetchone()
                        if ci_row and ci_row[0]:
                            try:
                                from datetime import datetime

                                added = datetime.strptime(ci_row[0][:19], "%Y-%m-%d %H:%M:%S")
                                latest = datetime.strptime(
                                    item["latest_release"][:19], "%Y-%m-%d %H:%M:%S"
                                )
                                if latest > added:
                                    item["has_new_release"] = True
                            except Exception:
                                pass
                        else:
                            item["has_new_release"] = True

            # 8.5 — decorate items with rule names that flagged them.
            if highlight_rules and items:
                for item in items:
                    matched = []
                    for rule in highlight_rules:
                        col = rule["field"]
                        try:
                            pat = _compile_filter_pattern(rule["pattern"])
                        except re.error:
                            continue
                        if pat.search(str(item.get(col) or "")):
                            matched.append(rule["name"])
                    if matched:
                        item["matched_rules"] = matched

            if hide_temp_table:
                c.execute(f"DROP TABLE IF EXISTS temp.{hide_temp_table}")
            return {"items": items, "totalPages": total_pages}

    def get_categories_with_counts(
        self, hide_rated: bool = False, hide_collected: bool = False
    ) -> list[dict]:
        with self._conn() as c:
            watched_ids = self.get_watched_item_ids(conn=c) if hide_rated else set()

            hide_temp_table: str | None = None
            if watched_ids:
                collected_ids = set(
                    r[0]
                    for r in c.execute("SELECT DISTINCT item_id FROM collection_items").fetchall()
                )
                hide_ids_list = list(watched_ids - collected_ids)
                if hide_ids_list and len(hide_ids_list) >= _LARGE_ID_LIST_THRESHOLD:
                    hide_temp_table = _materialize_id_list(c, hide_ids_list, "cats_hide_ids")
            else:
                hide_ids_list = []

            # make_filters returns (sql_fragment, params) so callers append the
            # placeholder list to whatever query they're running.
            def make_filters(alias="i"):
                clauses: list[str] = []
                local_params: list = []
                if hide_ids_list:
                    if hide_temp_table:
                        clauses.append(f"{alias}.id NOT IN (SELECT id FROM temp.{hide_temp_table})")
                    else:
                        clauses.append(f"{alias}.id NOT IN ({_placeholders(hide_ids_list)})")
                        local_params.extend(hide_ids_list)
                if hide_collected:
                    clauses.append(f"{alias}.id NOT IN (SELECT item_id FROM collection_items)")
                if clauses:
                    return " AND " + " AND ".join(clauses), local_params
                return "", local_params

            not_in_sql, not_in_params = make_filters("i")

            # Per-category counts in one pass: LEFT JOIN with the hide_*
            # filters folded into the ON clause (NOT the WHERE clause —
            # that would turn this into an INNER JOIN and drop empty
            # categories from the result). GROUP BY category to count.
            cat_rows = c.execute(
                "SELECT c.id, c.name, "
                "COUNT(i.id) AS count "
                "FROM categories c "
                "LEFT JOIN items i ON i.category_id = c.id "
                f"AND i.is_ignored = 0 {not_in_sql} "
                "GROUP BY c.id, c.name ORDER BY c.name",
                not_in_params,
            ).fetchall()
            cats = [dict(r) for r in cat_rows]

            video_cats_ph = _placeholders(VIDEO_CATEGORY_IDS)
            video_params = list(VIDEO_CATEGORY_IDS)

            # All single-row video-scoped counts in one query: each
            # named bucket is a CASE in the SELECT list. Saves five
            # full table scans on a 100k-row catalog.
            video_row = c.execute(
                "SELECT "
                "  SUM(CASE WHEN is_ignored = 0 THEN 1 ELSE 0 END) AS count_video, "
                "  SUM(CASE WHEN is_ignored = 0 AND (poster_url IS NULL OR poster_url = '') THEN 1 ELSE 0 END) AS no_poster, "
                "  SUM(CASE WHEN is_ignored = 0 AND (kp_rating = 0 OR kp_rating IS NULL OR imdb_rating = 0 OR imdb_rating IS NULL) THEN 1 ELSE 0 END) AS no_ratings, "
                "  SUM(CASE WHEN is_ignored = 0 AND (kp_id IS NULL OR kp_id = '') THEN 1 ELSE 0 END) AS no_kp_id, "
                "  SUM(CASE WHEN is_ignored = 0 AND (imdb_id IS NULL OR imdb_id = '') THEN 1 ELSE 0 END) AS no_imdb_id, "
                "  SUM(CASE WHEN is_ignored = 0 AND (kp_id IS NULL OR kp_id = '') AND (imdb_id IS NULL OR imdb_id = '') THEN 1 ELSE 0 END) AS no_any_id, "
                "  SUM(CASE WHEN is_ignored = 0 AND (kinopub_id IS NULL OR kinopub_id = '') THEN 1 ELSE 0 END) AS no_kinopub "
                "FROM items i "
                f"WHERE category_id IN ({video_cats_ph}) {not_in_sql}",
                video_params + not_in_params,
            ).fetchone()
            count_video = video_row["count_video"] or 0
            no_poster_count = video_row["no_poster"] or 0
            no_ratings_count = video_row["no_ratings"] or 0
            no_kp_id_count = video_row["no_kp_id"] or 0
            no_imdb_id_count = video_row["no_imdb_id"] or 0
            no_any_id_count = video_row["no_any_id"] or 0
            no_kinopub_count = video_row["no_kinopub"] or 0

            # any-category + ignored together (one scan).
            any_row = c.execute(
                "SELECT "
                "  SUM(CASE WHEN is_ignored = 0 THEN 1 ELSE 0 END) AS count_any, "
                "  SUM(CASE WHEN is_ignored = 1 THEN 1 ELSE 0 END) AS count_ignored "
                f"FROM items i WHERE 1 = 1 {not_in_sql}",
                not_in_params,
            ).fetchone()
            count_any = any_row["count_any"] or 0
            count_ignored = any_row["count_ignored"] or 0

            result = [
                {"id": -1, "name": "Все видео", "count": count_video},
                {"id": -100, "name": "🖼️ БЕЗ ПОСТЕРОВ", "count": no_poster_count},
                {"id": -101, "name": "📊 БЕЗ ОЦЕНОК", "count": no_ratings_count},
                {"id": -102, "name": "🆔 БЕЗ КП ID", "count": no_kp_id_count},
                {"id": -103, "name": "🆔 БЕЗ IMDb ID", "count": no_imdb_id_count},
                {"id": -104, "name": "🚫 БЕЗ ID ВООБЩЕ", "count": no_any_id_count},
                {"id": -105, "name": "🚫 БЕЗ KINOPUB ID", "count": no_kinopub_count},
                {"id": 0, "name": "Любая категория", "count": count_any},
                *cats,
                {"id": -2, "name": "🗑️ ИГНОРИРУЕМЫЕ", "count": count_ignored},
            ]
            if hide_temp_table:
                c.execute(f"DROP TABLE IF EXISTS temp.{hide_temp_table}")
            return result

    def get_stats(self) -> dict:
        with self._conn() as c:
            video_cats_ph = _placeholders(VIDEO_CATEGORY_IDS)
            video_params = list(VIDEO_CATEGORY_IDS)
            total_video = c.execute(
                "SELECT COUNT(*) FROM items "
                f"WHERE category_id IN ({video_cats_ph}) AND is_ignored = 0",
                video_params,
            ).fetchone()[0]
            no_poster = c.execute(
                "SELECT COUNT(*) FROM items "
                f"WHERE category_id IN ({video_cats_ph}) AND is_ignored = 0 "
                "AND (poster_url IS NULL OR poster_url = '')",
                video_params,
            ).fetchone()[0]
            no_ratings = c.execute(
                "SELECT COUNT(*) FROM items "
                f"WHERE category_id IN ({video_cats_ph}) AND is_ignored = 0 "
                "AND (kp_rating = 0 OR kp_rating IS NULL OR "
                "imdb_rating = 0 OR imdb_rating IS NULL)",
                video_params,
            ).fetchone()[0]
            no_rezka = c.execute(
                "SELECT COUNT(*) FROM items "
                f"WHERE category_id IN ({video_cats_ph}) AND is_ignored = 0 "
                "AND (rezka_url IS NULL OR rezka_url = '')",
                video_params,
            ).fetchone()[0]
            no_ids = c.execute(
                "SELECT COUNT(*) FROM items "
                f"WHERE category_id IN ({video_cats_ph}) AND is_ignored = 0 "
                "AND (kp_id IS NULL OR kp_id = '') "
                "AND (imdb_id IS NULL OR imdb_id = '')",
                video_params,
            ).fetchone()[0]
            history = {
                r["job_type"]: r["last_run"]
                for r in c.execute(
                    "SELECT job_type, MAX(end_time) as last_run FROM job_history GROUP BY job_type"
                ).fetchall()
            }
            return {
                "no_poster": no_poster,
                "no_ratings": no_ratings,
                "no_rezka": no_rezka,
                "no_ids": no_ids,
                "total_video": total_video,
                "last_runs": history,
            }

    def rebuild_fts(self) -> int:
        with self._conn() as c:
            c.execute("DELETE FROM items_fts")
            c.execute(
                "INSERT INTO items_fts(rowid, title, original_title, title_norm) SELECT id, title, original_title, title_norm FROM items"
            )
            return c.execute("SELECT count(*) FROM items_fts").fetchone()[0]

    def get_fts_count(self, conn=None) -> int:
        with self._conn(conn) as c:
            return c.execute("SELECT count(*) FROM items_fts").fetchone()[0]

    def ensure_fts_indexed(self) -> None:
        try:
            with self._conn() as c:
                count = c.execute("SELECT count(*) FROM items_fts").fetchone()[0]
                if count == 0:
                    c.execute("DELETE FROM items_fts")
                    c.execute(
                        "INSERT INTO items_fts(rowid, title, original_title, title_norm) SELECT id, title, original_title, title_norm FROM items"
                    )
        except Exception as e:
            logger.warning(f"[FTS5] Index init skipped: {e}")

    def _resolve_item_id(self, c, ref: dict) -> int | None:
        """Best-effort match of an exported item-ref to a local item.id.

        Tries kp_id first, then imdb_id, then rezka_url (exact and domain-agnostic),
        then title+year (exact and normalized), then title-only (exact and normalized).
        Returns None if nothing matches.
        """
        # 1. External identifiers (kp_id and imdb_id) are the most reliable
        for col, val in (("kp_id", ref.get("kp_id")), ("imdb_id", ref.get("imdb_id"))):
            if val:
                row = c.execute(f"SELECT id FROM items WHERE {col} = ? LIMIT 1", (val,)).fetchone()
                if row:
                    return int(row["id"])

        # 2. Rezka URL (Exact & Domain-agnostic)
        rezka_url = ref.get("rezka_url")
        if rezka_url:
            # Exact match
            row = c.execute(
                "SELECT id FROM items WHERE rezka_url = ? LIMIT 1", (rezka_url,)
            ).fetchone()
            if row:
                return int(row["id"])

            # Domain-agnostic match: extract path from rezka_url (e.g. /films/adventure/...)
            from urllib.parse import urlparse

            try:
                parsed_ref = urlparse(rezka_url)
                ref_path = parsed_ref.path
                if ref_path and ref_path != "/":
                    row = c.execute(
                        "SELECT id FROM items WHERE rezka_url LIKE ? LIMIT 1",
                        (f"%{ref_path}",),
                    ).fetchone()
                    if row:
                        return int(row["id"])
            except Exception:
                pass

        # 3. Title & Year matching
        title = (ref.get("title") or "").strip()
        year = ref.get("year")
        if title:
            # Exact title with year
            if year:
                row = c.execute(
                    "SELECT id FROM items WHERE title = ? AND year = ? LIMIT 1",
                    (title, year),
                ).fetchone()
                if row:
                    return int(row["id"])

            # Exact title only
            row = c.execute(
                "SELECT id FROM items WHERE title = ? LIMIT 1",
                (title,),
            ).fetchone()
            if row:
                return int(row["id"])

            # Fallback to normalized title matching (ignores punctuation, case, parens)
            from app_core import normalize_title

            norm = normalize_title(title)
            if norm:
                # Normalized with year (+/- 1 year margin or 0)
                if year:
                    rows = c.execute(
                        "SELECT id, year FROM items WHERE title_norm = ?",
                        (norm,),
                    ).fetchall()
                    for r in rows:
                        iy = r["year"] or 0
                        if iy == year or iy == 0 or abs(iy - year) <= 1:
                            return int(r["id"])

                # Normalized title only
                row = c.execute(
                    "SELECT id FROM items WHERE title_norm = ? LIMIT 1",
                    (norm,),
                ).fetchone()
                if row:
                    return int(row["id"])

        return None

    def insert_search_name(self, item_id: int, name_norm: str, conn=None) -> None:
        with self._conn(conn) as c:
            c.execute(
                "INSERT INTO item_search_names (item_id, name_norm) VALUES (?, ?)",
                (item_id, name_norm),
            )

    def reassign_search_names(self, old_item_id: int, new_item_id: int, conn=None) -> None:
        with self._conn(conn) as c:
            c.execute(
                "UPDATE OR IGNORE item_search_names SET item_id = ? WHERE item_id = ?",
                (new_item_id, old_item_id),
            )

    def delete_search_names_by_item(self, item_id: int, conn=None) -> None:
        with self._conn(conn) as c:
            c.execute("DELETE FROM item_search_names WHERE item_id = ?", (item_id,))

    def export_items(self, category_id: int = -1) -> list[dict]:
        with self._conn() as c:
            video_cats_ph = _placeholders(VIDEO_CATEGORY_IDS)
            where_clauses = ["1=1"]
            params: list = []
            if category_id == -1:
                where_clauses.append(f"items.category_id IN ({video_cats_ph})")
                params.extend(VIDEO_CATEGORY_IDS)
            elif category_id == -2:
                where_clauses.append("items.is_ignored = 1")
            elif category_id > 0:
                where_clauses.append("items.category_id = ?")
                params.append(category_id)
            where_clauses.append("items.is_ignored = 0" if category_id != -2 else "1=1")

            where_sql = " AND ".join(where_clauses)
            rows = c.execute(
                f"""SELECT items.id, items.title, items.year, items.category_id, items.kp_rating, items.imdb_rating,
                               items.poster_url, items.description, items.imdb_id, items.kp_id, items.rezka_url,
                               items.original_title,
                               (SELECT MAX(date_added) FROM releases WHERE item_id = items.id) as latest_release
                        FROM items WHERE {where_sql}
                        ORDER BY latest_release DESC NULLS LAST""",
                params,
            ).fetchall()
            return [dict(r) for r in rows]

    def get_items_needing_metadata(
        self, check_col: str, batch_size: int = 300, conn=None
    ) -> list[dict]:
        # check_col is interpolated as a column name (placeholders can't be
        # used for identifiers). Whitelist defends against accidental misuse.
        if check_col not in db.core._METADATA_CHECK_COLS:
            raise ValueError(f"Unsupported check_col: {check_col!r}")
        with self._conn(conn) as c:
            video_cats_ph = _placeholders(VIDEO_CATEGORY_IDS)
            rows = c.execute(
                f"""
                    SELECT id, title, year, poster_url, kp_rating, imdb_rating,
                           kp_id, imdb_id, description
                    FROM items
                    WHERE category_id IN ({video_cats_ph})
                    AND (poster_url IS NULL OR poster_url = '' OR
                         kp_rating = 0 OR kp_rating IS NULL OR
                         imdb_rating = 0 OR imdb_rating IS NULL)
                    AND is_ignored = 0
                    AND is_metadata_fixed = 0
                    AND {check_col} = 0
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                [*VIDEO_CATEGORY_IDS, int(batch_size)],
            ).fetchall()
            return [dict(r) for r in rows]

    def rate_item(self, item_id: int, rating: int | None, conn=None) -> None:
        with self._conn(conn) as c:
            c.execute("UPDATE items SET user_rating = ? WHERE id = ?", (rating, item_id))
            item = c.execute(
                "SELECT title, original_title, year, imdb_id, kp_id, title_norm FROM items WHERE id = ?",
                (item_id,),
            ).fetchone()
            if item:
                if rating is None:
                    c.execute(
                        "DELETE FROM user_ratings WHERE (imdb_id IS NOT NULL AND imdb_id = ?) OR (kp_id IS NOT NULL AND kp_id = ?) OR (title_norm = ? AND item_year = ?)",
                        (item["imdb_id"], item["kp_id"], item["title_norm"], item["year"]),
                    )
                else:
                    from app_core import normalize_title

                    orig_norm = (
                        normalize_title(item["original_title"]) if item["original_title"] else None
                    )
                    rating_item = {
                        "title": item["title"],
                        "orig_title": item["original_title"],
                        "year": item["year"],
                        "rating": rating,
                        "imdb_id": item["imdb_id"],
                        "kp_id": item["kp_id"],
                        "title_norm": item["title_norm"],
                        "orig_norm": orig_norm,
                    }
                    self.upsert_user_rating(rating_item, conn=c)

    def mark_watched(self, item_id: int, watched: bool, conn=None) -> None:
        watched_val = 1 if watched else 0
        watched_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S") if watched else None
        with self._conn(conn) as c:
            c.execute(
                "UPDATE items SET is_watched = ?, watched_at = ? WHERE id = ?",
                (watched_val, watched_at, item_id),
            )
            sys_col = c.execute(
                "SELECT id FROM collections WHERE is_system = 1 AND name = 'Просмотренное'"
            ).fetchone()
            if not sys_col:
                c.execute(
                    "INSERT OR IGNORE INTO collections (name, sort_order, is_system) VALUES ('Просмотренное', 9999, 1)"
                )
                sys_col = c.execute(
                    "SELECT id FROM collections WHERE is_system = 1 AND name = 'Просмотренное'"
                ).fetchone()
            if sys_col:
                sys_col_id = sys_col[0]
                if watched:
                    c.execute(
                        "INSERT OR IGNORE INTO collection_items (collection_id, item_id, added_at) VALUES (?, ?, ?)",
                        (sys_col_id, item_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
                    )
                else:
                    c.execute(
                        "DELETE FROM collection_items WHERE collection_id = ? AND item_id = ?",
                        (sys_col_id, item_id),
                    )
