import sqlite3
import unicodedata
from contextlib import contextmanager
from datetime import datetime
from typing import Optional

from app_core import RUTOR_CATEGORIES, VIDEO_CATEGORY_IDS


class Database:
    _RATING_FIELDS = frozenset({"kp_rating", "imdb_rating", "year"})
    _EMPTY_TEXT_FIELDS = frozenset(
        {"poster_url", "description", "rezka_url", "original_title", "title"}
    )
    _COALESCE_FIELDS = frozenset({"kp_id", "imdb_id", "title_norm"})

    def __init__(self, path="app_data.db"):
        self.path = path

    @contextmanager
    def _conn(self, conn=None):
        own = conn is None
        if own:
            conn = self.get_connection()
        try:
            yield conn
        except BaseException:
            if own:
                conn.close()
            raise
        if own:
            conn.commit()
            conn.close()

    def get_connection(self):
        conn = sqlite3.connect(self.path, timeout=30.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.row_factory = sqlite3.Row

        def py_lower(x):
            if x is None:
                return None
            return (
                unicodedata.normalize("NFC", str(x)).lower().replace("x", "х").strip()
            )

        conn.create_function("py_lower", 1, py_lower)
        return conn

    # ── Schema ──────────────────────────────────────────────────────

    def init_schema(self):
        with self._conn() as c:
            cur = c.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS categories (
                    id INTEGER PRIMARY KEY,
                    name TEXT
                )
            """)
            for cat_id, cat_name in RUTOR_CATEGORIES.items():
                if cat_id <= 0:
                    continue
                cur.execute(
                    "INSERT OR REPLACE INTO categories (id, name) VALUES (?, ?)",
                    (cat_id, cat_name),
                )

            cur.execute("""
                CREATE TABLE IF NOT EXISTS items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    category_id INTEGER,
                    title TEXT,
                    year INTEGER,
                    kp_rating REAL DEFAULT 0,
                    imdb_rating REAL DEFAULT 0,
                    description TEXT,
                    poster_url TEXT,
                    is_ignored BOOLEAN DEFAULT 0,
                    is_metadata_fixed BOOLEAN DEFAULT 0,
                    user_rating INTEGER,
                    original_title TEXT,
                    imdb_id TEXT,
                    kinorium_id TEXT,
                    kp_id TEXT,
                    title_norm TEXT,
                    checked_tech INTEGER DEFAULT 0,
                    checked_uz INTEGER DEFAULT 0,
                    checked_poiskkino INTEGER DEFAULT 0,
                    rezka_url TEXT,
                    checked_rezka INTEGER DEFAULT 0,
                    ignored_at TEXT,
                    is_reprocessed INTEGER DEFAULT 0,
                    latest_season INTEGER DEFAULT 0,
                    latest_episode INTEGER DEFAULT 0,
                    UNIQUE(title, year, category_id)
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS job_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_type TEXT,
                    start_time TEXT,
                    end_time TEXT,
                    duration REAL,
                    items_processed INTEGER DEFAULT 0,
                    total_items INTEGER DEFAULT 0,
                    status TEXT
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS releases (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    item_id INTEGER,
                    rutor_id TEXT,
                    torrent_title TEXT,
                    quality TEXT,
                    size TEXT,
                    link TEXT,
                    date_added TEXT,
                    magnet TEXT,
                    FOREIGN KEY(item_id) REFERENCES items(id)
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS user_ratings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    item_title TEXT,
                    item_year INTEGER,
                    rating INTEGER,
                    service TEXT,
                    external_id TEXT,
                    original_title TEXT,
                    title_norm TEXT,
                    original_title_norm TEXT,
                    imdb_id TEXT,
                    kp_id TEXT,
                    UNIQUE(item_title, item_year)
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS collections (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE,
                    sort_order INTEGER DEFAULT 0
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS collection_items (
                    collection_id INTEGER,
                    item_id INTEGER,
                    added_at TEXT,
                    PRIMARY KEY (collection_id, item_id),
                    FOREIGN KEY(collection_id) REFERENCES collections(id) ON DELETE CASCADE,
                    FOREIGN KEY(item_id) REFERENCES items(id) ON DELETE CASCADE
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS item_search_names (
                    item_id INTEGER,
                    name_norm TEXT
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS app_state (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)

            default_collections = [
                "говноозвучки",
                "на телефон просмотр",
                "детские",
                "в первую очередь",
                "Проходняк сериал завершенные",
                "Топ сериалы с завершённые",
                "проходняк фильмы",
                "Топ сериал с продолжением",
                "Проходняк сериал с продолжением",
                "тв шоу",
                "топ фильмы",
                "docum",
            ]
            for name in default_collections:
                cur.execute(
                    "INSERT OR IGNORE INTO collections (name) VALUES (?)", (name,)
                )

            try:
                cur.execute("""
                    CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5(
                        title, original_title, title_norm,
                        content=items, content_rowid=id,
                        tokenize="unicode61 categories UnicodeL* L*"
                    )
                """)
            except Exception:
                try:
                    cur.execute("""
                        CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5(
                            title, original_title, title_norm,
                            content=items, content_rowid=id,
                            tokenize=unicode61
                        )
                    """)
                except Exception:
                    pass
            try:
                cur.execute(
                    "CREATE TRIGGER IF NOT EXISTS items_ai AFTER INSERT ON items BEGIN"
                    " INSERT INTO items_fts(rowid, title, original_title, title_norm)"
                    " VALUES (new.id, new.title, new.original_title, new.title_norm); END"
                )
                cur.execute(
                    "CREATE TRIGGER IF NOT EXISTS items_ad AFTER DELETE ON items BEGIN"
                    " INSERT INTO items_fts(items_fts, rowid, title, original_title, title_norm)"
                    ' VALUES ("delete", old.id, old.title, old.original_title, old.title_norm); END'
                )
                cur.execute(
                    "CREATE TRIGGER IF NOT EXISTS items_au AFTER UPDATE ON items BEGIN"
                    " INSERT INTO items_fts(items_fts, rowid, title, original_title, title_norm)"
                    ' VALUES ("delete", old.id, old.title, old.original_title, old.title_norm);'
                    " INSERT INTO items_fts(rowid, title, original_title, title_norm)"
                    " VALUES (new.id, new.title, new.original_title, new.title_norm); END"
                )
            except Exception:
                pass

            self._ensure_indexes(cur)

    def _ensure_indexes(self, cur):
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_ratings_title_norm ON user_ratings(title_norm)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_ratings_imdb_id ON user_ratings(imdb_id)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_ratings_kp_id ON user_ratings(kp_id)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_items_title_norm ON items(title_norm)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_search_names_item ON item_search_names(item_id)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_search_names_name ON item_search_names(name_norm)"
        )

    def check_and_migrate_schema(self):
        with self._conn() as c:
            cur = c.cursor()
            cols = [
                col[1]
                for col in cur.execute("PRAGMA table_info(user_ratings)").fetchall()
            ]
            if "original_title" not in cols:
                cur.execute("ALTER TABLE user_ratings ADD COLUMN original_title TEXT")
            if "title_norm" not in cols:
                cur.execute("ALTER TABLE user_ratings ADD COLUMN title_norm TEXT")
            if "original_title_norm" not in cols:
                cur.execute(
                    "ALTER TABLE user_ratings ADD COLUMN original_title_norm TEXT"
                )
            if "imdb_id" not in cols:
                cur.execute("ALTER TABLE user_ratings ADD COLUMN imdb_id TEXT")
            if "kp_id" not in cols:
                cur.execute("ALTER TABLE user_ratings ADD COLUMN kp_id TEXT")

            items_cols = [
                col[1] for col in cur.execute("PRAGMA table_info(items)").fetchall()
            ]
            if "title_norm" not in items_cols:
                cur.execute("ALTER TABLE items ADD COLUMN title_norm TEXT")

            releases_cols = [
                col[1] for col in cur.execute("PRAGMA table_info(releases)").fetchall()
            ]
            if "rutor_id" not in releases_cols:
                cur.execute("ALTER TABLE releases ADD COLUMN rutor_id TEXT")
            if "magnet" not in releases_cols:
                cur.execute("ALTER TABLE releases ADD COLUMN magnet TEXT")

            ci_cols = [
                col[1]
                for col in cur.execute("PRAGMA table_info(collection_items)").fetchall()
            ]
            if "added_at" not in ci_cols:
                cur.execute("ALTER TABLE collection_items ADD COLUMN added_at TEXT")

            items_cols = [
                col[1] for col in cur.execute("PRAGMA table_info(items)").fetchall()
            ]
            if "latest_season" not in items_cols:
                cur.execute(
                    "ALTER TABLE items ADD COLUMN latest_season INTEGER DEFAULT 0"
                )
            if "latest_episode" not in items_cols:
                cur.execute(
                    "ALTER TABLE items ADD COLUMN latest_episode INTEGER DEFAULT 0"
                )

            cur.execute("""
                CREATE TABLE IF NOT EXISTS item_search_names (item_id INTEGER, name_norm TEXT)
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS app_state (key TEXT PRIMARY KEY, value TEXT)
            """)
            self._ensure_indexes(cur)

    # ── Items ──────────────────────────────────────────────────────

    def get_item(self, item_id: int, conn=None) -> Optional[dict]:
        with self._conn(conn) as c:
            row = c.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
            return dict(row) if row else None

    def get_items(self, where_clause="1=1", params=(), conn=None) -> list[dict]:
        with self._conn(conn) as c:
            rows = c.execute(
                f"SELECT * FROM items WHERE {where_clause}", params
            ).fetchall()
            return [dict(r) for r in rows]

    def get_items_count(self, where_clause="1=1", params=(), conn=None) -> int:
        with self._conn(conn) as c:
            return c.execute(
                f"SELECT COUNT(*) FROM items WHERE {where_clause}", params
            ).fetchone()[0]

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
            if kp_id:
                row = c.execute(
                    "SELECT id FROM items WHERE kp_id = ? LIMIT 1", (str(kp_id),)
                ).fetchone()
                if row:
                    return row[0]
            if imdb_id:
                row = c.execute(
                    "SELECT id FROM items WHERE imdb_id = ? LIMIT 1", (str(imdb_id),)
                ).fetchone()
                if row:
                    return row[0]
            if rezka_url:
                row = c.execute(
                    "SELECT id FROM items WHERE rezka_url = ? LIMIT 1", (rezka_url,)
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
                            if (
                                iy == year
                                or iy == 0
                                or year == 0
                                or abs(iy - year) <= 1
                            ):
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
                                    iy == year
                                    or iy == 0
                                    or year == 0
                                    or abs(iy - year) <= 1
                                ):
                                    if not category_id or ic == category_id:
                                        return r[0]
            return None

    def insert_item(self, data: dict, conn=None) -> Optional[int]:
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
            c.execute("DELETE FROM items WHERE id = ?", (item_id,))

    def mark_checked(self, item_id: int, source: str, conn=None) -> None:
        col = f"checked_{source}"
        with self._conn(conn) as c:
            c.execute(f"UPDATE items SET {col} = 1 WHERE id = ?", (item_id,))

    def toggle_ignore(self, item_id: int, conn=None) -> int:
        with self._conn(conn) as c:
            row = c.execute(
                "SELECT is_ignored FROM items WHERE id = ?", (item_id,)
            ).fetchone()
            if not row:
                return -1
            new_state = 1 - row["is_ignored"]
            ignored_at = (
                datetime.now().strftime("%Y-%m-%d %H:%M:%S") if new_state == 1 else None
            )
            c.execute(
                "UPDATE items SET is_ignored = ?, ignored_at = ? WHERE id = ?",
                (new_state, ignored_at, item_id),
            )
            return new_state

    def reset_item(self, item_id: int, fields: list[str], conn=None) -> None:
        field_map = {
            "poster": "poster_url = NULL",
            "description": "description = NULL",
            "kp_id": "kp_id = NULL",
            "imdb_id": "imdb_id = NULL",
            "rezka_url": "rezka_url = NULL",
            "ratings": "kp_rating = 0, imdb_rating = 0",
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
            c.execute(f"UPDATE items SET {', '.join(updates)} WHERE id = ?", (item_id,))

    def set_ids(
        self,
        item_id: int,
        kp_id: Optional[str] = None,
        imdb_id: Optional[str] = None,
        conn=None,
    ) -> None:
        updates = []
        params = []
        if kp_id is not None:
            updates.append("kp_id = ?")
            params.append(kp_id)
            updates.extend(
                ["checked_poiskkino = 0", "checked_tech = 0", "checked_rezka = 0"]
            )
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

    def find_items_by_year_category(
        self, year: int, category_id: int, conn=None
    ) -> list[dict]:
        with self._conn(conn) as c:
            rows = c.execute(
                "SELECT id, title FROM items WHERE year = ? AND category_id = ?",
                (year, category_id),
            ).fetchall()
            return [dict(r) for r in rows]

    def find_item_id_by_title_year_category(
        self, title: str, year: int, category_id: int, conn=None
    ) -> Optional[int]:
        with self._conn(conn) as c:
            row = c.execute(
                "SELECT id FROM items WHERE title = ? AND year = ? AND category_id = ?",
                (title, year, category_id),
            ).fetchone()
            return row[0] if row else None

    def find_duplicate_item_id(
        self, title: str, year: int, category_id: int, exclude_id: int, conn=None
    ) -> Optional[int]:
        with self._conn(conn) as c:
            row = c.execute(
                "SELECT id FROM items WHERE title = ? AND year = ? AND category_id = ? AND id != ?",
                (title, year, category_id, exclude_id),
            ).fetchone()
            return row[0] if row else None

    # ── Releases ────────────────────────────────────────────────────

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
            row = c.execute(
                "SELECT 1 FROM releases WHERE rutor_id = ?", (rutor_id,)
            ).fetchone()
            return row is not None

    def get_last_release_date(
        self, category_id: Optional[int] = None, conn=None
    ) -> Optional[str]:
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

    def get_release_torrent_title(self, item_id: int, conn=None) -> Optional[str]:
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

    # ── Feed ────────────────────────────────────────────────────────

    def get_feed(
        self,
        category_id: int = -1,
        collection_id: Optional[int] = None,
        search: Optional[str] = None,
        min_kp: float = 0.0,
        max_kp: float = 10.0,
        min_imdb: float = 0.0,
        max_imdb: float = 10.0,
        min_year: Optional[int] = None,
        max_year: Optional[int] = None,
        min_date: Optional[str] = None,
        max_date: Optional[str] = None,
        hide_ignored: bool = True,
        hide_rated: bool = False,
        hide_collected: bool = False,
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
                ids_str = ",".join(map(str, VIDEO_CATEGORY_IDS))
                if category_id == -1:
                    where_clauses.append(f"items.category_id IN ({ids_str})")
                elif category_id == -100:
                    where_clauses.append(
                        f"items.category_id IN ({ids_str}) AND (items.poster_url IS NULL OR items.poster_url = '')"
                    )
                elif category_id == -101:
                    where_clauses.append(
                        f"items.category_id IN ({ids_str}) AND (items.kp_rating = 0 OR items.kp_rating IS NULL OR items.imdb_rating = 0 OR items.imdb_rating IS NULL)"
                    )
                elif category_id == -102:
                    where_clauses.append(
                        f"items.category_id IN ({ids_str}) AND (items.kp_id IS NULL OR items.kp_id = '')"
                    )
                elif category_id == -103:
                    where_clauses.append(
                        f"items.category_id IN ({ids_str}) AND (items.imdb_id IS NULL OR items.imdb_id = '')"
                    )
                elif category_id == -104:
                    where_clauses.append(
                        f"items.category_id IN ({ids_str}) AND (items.kp_id IS NULL OR items.kp_id = '') AND (items.imdb_id IS NULL OR items.imdb_id = '')"
                    )
                elif category_id != 0:
                    where_clauses.append("items.category_id = ?")
                    params.append(category_id)
                if hide_ignored and category_id != -2:
                    where_clauses.append("items.is_ignored = 0")

            if search:
                search_val = f"%{search.lower()}%"
                fts_available = False
                try:
                    c.execute("SELECT count(*) FROM items_fts LIMIT 1")
                    fts_available = True
                except Exception:
                    pass
                if fts_available:
                    fts_query = " OR ".join(search.strip().split())
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
                        where_clauses.append(
                            f"items.id NOT IN ({','.join(map(str, hide_ids))})"
                        )

            if hide_collected and not collection_id:
                where_clauses.append(
                    "items.id NOT IN (SELECT item_id FROM collection_items)"
                )

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
            join = (
                f"JOIN collection_items ci ON items.id = ci.item_id AND ci.collection_id = {collection_id}"
                if collection_id
                else ""
            )
            query = f"SELECT items.*, (SELECT MAX(date_added) FROM releases WHERE item_id = items.id) as latest_release FROM items {join} WHERE {where_sql} ORDER BY {order_by} LIMIT ? OFFSET ?"
            params.extend([limit, (page - 1) * limit])

            items = [dict(r) for r in c.execute(query, params).fetchall()]
            for item in items:
                rows = c.execute(
                    "SELECT * FROM releases WHERE item_id = ? ORDER BY date_added DESC",
                    (item["id"],),
                ).fetchall()
                item["releases"] = [dict(r) for r in rows]

            if collection_id:
                for item in items:
                    item["has_new_release"] = False
                    if (
                        item.get("latest_season", 0) > 0
                        and item.get("latest_episode", 0) > 0
                    ):
                        ci_row = c.execute(
                            "SELECT added_at FROM collection_items WHERE collection_id = ? AND item_id = ?",
                            (collection_id, item["id"]),
                        ).fetchone()
                        if ci_row and ci_row[0]:
                            try:
                                from datetime import datetime

                                added = datetime.strptime(
                                    ci_row[0][:19], "%Y-%m-%d %H:%M:%S"
                                )
                                season_key = (
                                    f"s{item['latest_season']}e{item['latest_episode']}"
                                )
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

                                added = datetime.strptime(
                                    ci_row[0][:19], "%Y-%m-%d %H:%M:%S"
                                )
                                latest = datetime.strptime(
                                    item["latest_release"][:19], "%Y-%m-%d %H:%M:%S"
                                )
                                if latest > added:
                                    item["has_new_release"] = True
                            except Exception:
                                pass
                        else:
                            item["has_new_release"] = True

            return {"items": items, "totalPages": total_pages}

    # ── Categories ──────────────────────────────────────────────────

    def get_categories_with_counts(
        self, hide_rated: bool = False, hide_collected: bool = False
    ) -> list[dict]:
        with self._conn() as c:
            watched_ids = self.get_watched_item_ids(conn=c) if hide_rated else set()

            def make_filters(alias="i"):
                clauses = []
                if watched_ids:
                    collected_ids = set(
                        r[0]
                        for r in c.execute(
                            "SELECT DISTINCT item_id FROM collection_items"
                        ).fetchall()
                    )
                    hide_ids = watched_ids - collected_ids
                    if hide_ids:
                        ids_str = ",".join(map(str, hide_ids))
                        clauses.append(f"{alias}.id NOT IN ({ids_str})")
                if hide_collected:
                    clauses.append(
                        f"{alias}.id NOT IN (SELECT item_id FROM collection_items)"
                    )
                return " AND " + " AND ".join(clauses) if clauses else ""

            not_in = make_filters("i")
            cats = [
                dict(r)
                for r in c.execute(
                    f"SELECT c.id, c.name, (SELECT COUNT(*) FROM items i WHERE i.category_id = c.id AND i.is_ignored = 0 {not_in}) as count FROM categories c ORDER BY c.name"
                ).fetchall()
            ]

            ids_str_cats = ",".join(map(str, VIDEO_CATEGORY_IDS))
            count_video = c.execute(
                f"SELECT COUNT(*) FROM items i WHERE category_id IN ({ids_str_cats}) AND is_ignored = 0 {not_in}"
            ).fetchone()[0]
            count_any = c.execute(
                f"SELECT COUNT(*) FROM items i WHERE is_ignored = 0 {not_in}"
            ).fetchone()[0]
            count_ignored = c.execute(
                f"SELECT COUNT(*) FROM items i WHERE is_ignored = 1 {not_in}"
            ).fetchone()[0]
            no_poster_count = c.execute(
                f"SELECT COUNT(*) FROM items i WHERE i.category_id IN ({ids_str_cats}) AND i.is_ignored = 0 AND (i.poster_url IS NULL OR i.poster_url = '') {not_in}"
            ).fetchone()[0]
            no_ratings_count = c.execute(
                f"SELECT COUNT(*) FROM items i WHERE i.category_id IN ({ids_str_cats}) AND i.is_ignored = 0 AND (i.kp_rating = 0 OR i.kp_rating IS NULL OR i.imdb_rating = 0 OR i.imdb_rating IS NULL) {not_in}"
            ).fetchone()[0]
            no_kp_id_count = c.execute(
                f"SELECT COUNT(*) FROM items i WHERE i.category_id IN ({ids_str_cats}) AND i.is_ignored = 0 AND (i.kp_id IS NULL OR i.kp_id = '') {not_in}"
            ).fetchone()[0]
            no_imdb_id_count = c.execute(
                f"SELECT COUNT(*) FROM items i WHERE i.category_id IN ({ids_str_cats}) AND i.is_ignored = 0 AND (i.imdb_id IS NULL OR i.imdb_id = '') {not_in}"
            ).fetchone()[0]
            no_any_id_count = c.execute(
                f"SELECT COUNT(*) FROM items i WHERE i.category_id IN ({ids_str_cats}) AND i.is_ignored = 0 AND (i.kp_id IS NULL OR i.kp_id = '') AND (i.imdb_id IS NULL OR i.imdb_id = '') {not_in}"
            ).fetchone()[0]

            return [
                {"id": -1, "name": "Все видео", "count": count_video},
                {"id": -100, "name": "🖼️ БЕЗ ПОСТЕРОВ", "count": no_poster_count},
                {"id": -101, "name": "📊 БЕЗ ОЦЕНОК", "count": no_ratings_count},
                {"id": -102, "name": "🆔 БЕЗ КП ID", "count": no_kp_id_count},
                {"id": -103, "name": "🆔 БЕЗ IMDb ID", "count": no_imdb_id_count},
                {"id": -104, "name": "🚫 БЕЗ ID ВООБЩЕ", "count": no_any_id_count},
                {"id": 0, "name": "Любая категория", "count": count_any},
                *cats,
                {"id": -2, "name": "🗑️ ИГНОРИРУЕМЫЕ", "count": count_ignored},
            ]

    # ── Stats ───────────────────────────────────────────────────────

    def get_stats(self) -> dict:
        with self._conn() as c:
            ids_str = ",".join(map(str, VIDEO_CATEGORY_IDS))
            total_video = c.execute(
                f"SELECT COUNT(*) FROM items WHERE category_id IN ({ids_str}) AND is_ignored = 0"
            ).fetchone()[0]
            no_poster = c.execute(
                f"SELECT COUNT(*) FROM items WHERE category_id IN ({ids_str}) AND is_ignored = 0 AND (poster_url IS NULL OR poster_url = '')"
            ).fetchone()[0]
            no_ratings = c.execute(
                f"SELECT COUNT(*) FROM items WHERE category_id IN ({ids_str}) AND is_ignored = 0 AND (kp_rating = 0 OR kp_rating IS NULL OR imdb_rating = 0 OR imdb_rating IS NULL)"
            ).fetchone()[0]
            no_rezka = c.execute(
                f"SELECT COUNT(*) FROM items WHERE category_id IN ({ids_str}) AND is_ignored = 0 AND (rezka_url IS NULL OR rezka_url = '')"
            ).fetchone()[0]
            no_ids = c.execute(
                f"SELECT COUNT(*) FROM items WHERE category_id IN ({ids_str}) AND is_ignored = 0 AND (kp_id IS NULL OR kp_id = '') AND (imdb_id IS NULL OR imdb_id = '')"
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

    # ── FTS ─────────────────────────────────────────────────────────

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
            print(f"[FTS5] Index init skipped: {e}")

    # ── Job History ─────────────────────────────────────────────────

    def insert_job_history(
        self,
        job_type: str,
        start_time: str,
        end_time: str,
        duration: float,
        items_processed: int,
        total_items: int,
        status: str,
        conn=None,
    ) -> None:
        with self._conn(conn) as c:
            c.execute(
                "INSERT INTO job_history (job_type, start_time, end_time, duration, items_processed, total_items, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    job_type,
                    start_time,
                    end_time,
                    duration,
                    items_processed,
                    total_items,
                    status,
                ),
            )

    def get_job_history(self, limit: int = 20) -> list[dict]:
        with self._conn() as c:
            return [
                dict(r)
                for r in c.execute(
                    "SELECT * FROM job_history ORDER BY start_time DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            ]

    def get_last_runs(self) -> dict:
        with self._conn() as c:
            return {
                r["job_type"]: r["last_run"]
                for r in c.execute(
                    "SELECT job_type, MAX(end_time) as last_run FROM job_history GROUP BY job_type"
                ).fetchall()
            }

    # ── Collections ─────────────────────────────────────────────────

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

    def save_collections_order(self, order: list[int]) -> None:
        with self._conn() as c:
            for i, col_id in enumerate(order):
                c.execute(
                    "UPDATE collections SET sort_order = ? WHERE id = ?", (i, col_id)
                )

    # ── User Ratings / Watched ──────────────────────────────────────

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

    # ── Item Search Names ───────────────────────────────────────────

    def insert_search_name(self, item_id: int, name_norm: str, conn=None) -> None:
        with self._conn(conn) as c:
            c.execute(
                "INSERT INTO item_search_names (item_id, name_norm) VALUES (?, ?)",
                (item_id, name_norm),
            )

    def reassign_search_names(
        self, old_item_id: int, new_item_id: int, conn=None
    ) -> None:
        with self._conn(conn) as c:
            c.execute(
                "UPDATE OR IGNORE item_search_names SET item_id = ? WHERE item_id = ?",
                (new_item_id, old_item_id),
            )

    def delete_search_names_by_item(self, item_id: int, conn=None) -> None:
        with self._conn(conn) as c:
            c.execute("DELETE FROM item_search_names WHERE item_id = ?", (item_id,))

    # ── Collection Items ────────────────────────────────────────────

    def merge_collection_items(
        self, old_item_id: int, new_item_id: int, conn=None
    ) -> None:
        with self._conn(conn) as c:
            c.execute(
                "INSERT OR IGNORE INTO collection_items (collection_id, item_id) SELECT collection_id, ? FROM collection_items WHERE item_id = ?",
                (new_item_id, old_item_id),
            )

    def delete_collection_items_by_item(self, item_id: int, conn=None) -> None:
        with self._conn(conn) as c:
            c.execute("DELETE FROM collection_items WHERE item_id = ?", (item_id,))

    # ── App State ───────────────────────────────────────────────────

    def mark_visited(self) -> str:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO app_state (key, value) VALUES ('last_visit', ?)",
                (now,),
            )
        return now

    def get_last_visit(self) -> Optional[str]:
        with self._conn() as c:
            row = c.execute(
                "SELECT value FROM app_state WHERE key = 'last_visit'"
            ).fetchone()
            return row[0] if row else None

    # ── Export ──────────────────────────────────────────────────────

    def export_items(self, category_id: int = -1) -> list[dict]:
        with self._conn() as c:
            ids_str = ",".join(map(str, VIDEO_CATEGORY_IDS))
            where_clauses = ["1=1"]
            params = []
            if category_id == -1:
                where_clauses.append(f"items.category_id IN ({ids_str})")
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

    # ── Item Stats (for fix_posters etc.) ───────────────────────────

    def get_items_needing_metadata(
        self, check_col: str, batch_size: int = 300, extra_where: str = "", conn=None
    ) -> list[dict]:
        with self._conn(conn) as c:
            cats_str = "(1, 4, 5, 16, 7)"
            rows = c.execute(f"""
                SELECT id, title, year, poster_url, kp_rating, imdb_rating, kp_id, imdb_id, description
                FROM items
                WHERE category_id IN {cats_str}
                AND (poster_url IS NULL OR poster_url = '' OR kp_rating = 0 OR kp_rating IS NULL OR imdb_rating = 0 OR imdb_rating IS NULL)
                AND is_ignored = 0
                AND is_metadata_fixed = 0
                AND {check_col} = 0
                {extra_where}
                ORDER BY id DESC
                LIMIT {batch_size}
            """).fetchall()
            return [dict(r) for r in rows]

    def get_db_stats(self, conn=None) -> tuple:
        with self._conn(conn) as c:
            cats_str = "(1, 4, 5, 16, 7)"
            no_poster = c.execute(
                f"SELECT COUNT(*) FROM items WHERE category_id IN {cats_str} AND (poster_url IS NULL OR poster_url = '')"
            ).fetchone()[0]
            no_ratings = c.execute(
                f"SELECT COUNT(*) FROM items WHERE category_id IN {cats_str} AND (kp_rating = 0 OR kp_rating IS NULL OR imdb_rating = 0 OR imdb_rating IS NULL)"
            ).fetchone()[0]
            total = c.execute(
                f"SELECT COUNT(*) FROM items WHERE category_id IN {cats_str}"
            ).fetchone()[0]
            return total, no_poster, no_ratings


db = Database()
