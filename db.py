import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from functools import lru_cache

from app_core import RUTOR_CATEGORIES, VIDEO_CATEGORY_IDS

# 8.5 — fields a filter_rule may target.
FILTER_RULE_FIELDS: tuple[str, ...] = ("title", "original_title", "description")
FILTER_RULE_ACTIONS: tuple[str, ...] = ("hide", "highlight")


@lru_cache(maxsize=256)
def _compile_filter_pattern(pattern: str) -> re.Pattern:
    """Compile a user-supplied regex once and cache it.

    Why LRU: rules are applied per-feed-page, so a hot rule would
    otherwise pay re.compile cost on every request. The cache is
    keyed on the pattern string, so editing a rule produces a new
    cache entry without touching old ones.
    """
    return re.compile(pattern, re.IGNORECASE | re.UNICODE)


def _sqlite_regexp(pattern: str | None, value: str | None) -> int:
    """REGEXP function bound onto every connection in get_connection.

    Returns 1 if `value` matches `pattern` anywhere (case-insensitive,
    unicode), 0 otherwise. Bad regex returns 0 silently — a malformed
    rule shouldn't 500 the whole feed; the rule editor validates on
    create/update so persistent garbage is rare.
    """
    if value is None or pattern is None:
        return 0
    try:
        return 1 if _compile_filter_pattern(pattern).search(str(value)) else 0
    except re.error:
        return 0


def _placeholders(values) -> str:
    """Return a comma-joined string of '?' placeholders for an IN clause.

    Always pair the result with extending the SQL params list with the same
    iterable, in the same order.
    """
    n = len(values) if hasattr(values, "__len__") else sum(1 for _ in values)
    if n == 0:
        # SQLite rejects "IN ()", and we never want to match anything anyway.
        return "NULL"
    return ",".join(["?"] * n)


# Column names accepted by get_items_needing_metadata. Column names can't be
# bound as parameters, so we whitelist the few we actually use to make sure a
# malformed call can't slip arbitrary SQL into the query.
_METADATA_CHECK_COLS = frozenset(
    {"checked_tech", "checked_uz", "checked_poiskkino", "checked_rezka"}
)


# When a list of ids would otherwise be plumbed through as 5 000+ '?'
# placeholders, materialize it into a per-connection TEMP table instead.
# Reasons:
#   * SQLITE_MAX_VARIABLE_NUMBER is 999 on older builds and 32 766 on
#     newer ones — a heavily-rated catalog can exceed even the new bound.
#   * The query plan with NOT IN (SELECT id FROM temp.X) remains stable
#     and lets SQLite use an index on the temp table.
_LARGE_ID_LIST_THRESHOLD = 256


def _materialize_id_list(c, ids, name_hint: str) -> str:
    """Drop+create a TEMP table holding `ids` and return its name.

    Caller is responsible for dropping the table once the query has run
    (the helper does the drop for them so the connection state is clean
    even on long-lived shared connections).
    """
    table = f"_par2_{name_hint}"
    c.execute(f"DROP TABLE IF EXISTS temp.{table}")
    c.execute(f"CREATE TEMP TABLE {table} (id INTEGER PRIMARY KEY)")
    c.executemany(
        f"INSERT OR IGNORE INTO {table} (id) VALUES (?)",
        [(int(i),) for i in ids],
    )
    return table


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
        # 8.5 — register a Python REGEXP function so SQL can express
        # `column REGEXP ?`. SQLite's REGEXP operator is unbound by
        # default; without this every query that uses it would raise
        # `no such function: REGEXP`.
        conn.create_function("REGEXP", 2, _sqlite_regexp, deterministic=True)
        return conn

    def wal_checkpoint(self, mode: str = "TRUNCATE") -> tuple[int, int, int]:
        """Run PRAGMA wal_checkpoint(<mode>) and return its 3-tuple result.

        The WAL file grows whenever a writer is faster than the
        auto-checkpoint can drain it — typically during the long
        `sync` / `reprocess` runs. Without an explicit checkpoint
        the .db-wal sidecar can balloon to hundreds of MB and stay
        that big until the application restarts. TRUNCATE forces the
        WAL back to zero bytes so disk usage matches what shows up
        in `.db`.

        Returns: (busy, log_pages, checkpointed_pages).
            busy=1 means another writer prevented a full checkpoint;
            the call itself is non-fatal — caller can retry later.
        """
        if mode not in ("PASSIVE", "FULL", "RESTART", "TRUNCATE"):
            raise ValueError(f"invalid wal_checkpoint mode: {mode}")
        with self._conn() as c:
            row = c.execute(f"PRAGMA wal_checkpoint({mode})").fetchone()
        # SQLite returns (busy, log, checkpointed). Guard against an
        # empty row in case the build doesn't support the result form.
        if row is None:
            return (0, 0, 0)
        busy = int(row[0]) if row[0] is not None else 0
        log = int(row[1]) if row[1] is not None else 0
        ckpt = int(row[2]) if row[2] is not None else 0
        return (busy, log, ckpt)

    def backup_to(self, dest_path: str, *, pages: int = 100) -> int:
        """Write a consistent copy of the database to `dest_path`.

        Uses SQLite's online backup API (`Connection.backup`) which
        is the recommended way to back up a live database — safer
        than `cp app_data.db dest.db` because it cooperates with
        WAL mode and doesn't tear pages mid-commit. The DB stays
        readable and writable throughout the copy.

        Parameters:
          dest_path — absolute path where the backup is written.
                      An existing file at this path is overwritten
                      atomically (the page-by-page copy targets a
                      tempfile, then os.replace swaps it in).
          pages     — pages copied per backup step. The default 100
                      keeps each step short enough that concurrent
                      writers don't see noticeable latency. -1 would
                      copy in one shot but blocks writers for the
                      duration.

        Returns the size of the backup file in bytes.
        """
        # Target a tempfile alongside dest_path so the swap is atomic
        # within the same filesystem (os.replace is atomic on the
        # same FS only, which the caller is responsible for).
        import os
        import tempfile

        dest_dir = os.path.dirname(os.path.abspath(dest_path)) or "."
        os.makedirs(dest_dir, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(prefix=".par2-backup-", suffix=".db", dir=dest_dir)
        os.close(fd)
        try:
            src = sqlite3.connect(self.path, timeout=30.0)
            try:
                dst = sqlite3.connect(tmp_path, timeout=30.0)
                try:
                    src.backup(dst, pages=pages)
                finally:
                    dst.close()
            finally:
                src.close()
            os.replace(tmp_path, dest_path)
            return os.path.getsize(dest_path)
        except Exception:
            # Don't leak the tempfile on partial failure.
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

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
                    tmdb_id TEXT,
                    UNIQUE(title, year, category_id)
                )
            """)
            # tmdb_id was added in migration 0003; idempotently extend
            # an installation that only has the original baseline.
            try:
                cur.execute("ALTER TABLE items ADD COLUMN tmdb_id TEXT")
            except sqlite3.OperationalError:
                pass

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

            # 8.5 — user-defined filter rules applied in get_feed.
            cur.execute("""
                CREATE TABLE IF NOT EXISTS filter_rules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    field TEXT NOT NULL,
                    pattern TEXT NOT NULL,
                    action TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # 8.18 — append-only audit log; supports undo via old_value replay.
            cur.execute("""
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    action TEXT NOT NULL,
                    item_id INTEGER,
                    field TEXT,
                    old_value TEXT,
                    new_value TEXT,
                    undone INTEGER NOT NULL DEFAULT 0
                )
            """)

            default_collections = [
                "говноозвучки",
                "на телефон просмотр",
                "детские",
                "в первую очередь",
                "Проходняк сериал завершенные",
                "Топ сериалы с завершённые",
                "Топ сериал с продолжением",
                "Проходняк сериал с продолжением",
                "тв шоу",
                "топ фильмы",
                "docum",
            ]
            for name in default_collections:
                cur.execute("INSERT OR IGNORE INTO collections (name) VALUES (?)", (name,))

            # tokenize: 'unicode61' with remove_diacritics=2 normalises
            # combining marks AND folds case. The previous string
            # ('unicode61 categories UnicodeL* L*') was malformed FTS5
            # syntax so init silently fell back to default tokenizer,
            # losing diacritic-insensitive search and 'x' <-> 'х'
            # latin/cyrillic homoglyph folding (the latter is handled
            # by storing title_norm — see app_core.normalize_title).
            try:
                cur.execute("""
                    CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5(
                        title, original_title, title_norm,
                        content=items, content_rowid=id,
                        tokenize='unicode61 remove_diacritics 2'
                    )
                """)
            except Exception:
                try:
                    cur.execute("""
                        CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5(
                            title, original_title, title_norm,
                            content=items, content_rowid=id,
                            tokenize='unicode61'
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
        # Run any pending SQL migrations (5.6) AFTER the
        # CREATE-TABLE-IF-NOT-EXISTS pass above. Migrations only
        # encode deltas relative to the baseline, so the order is:
        # baseline tables -> migration runner -> done.
        self._apply_migrations()

    def _ensure_indexes(self, cur):
        cur.execute("CREATE INDEX IF NOT EXISTS idx_ratings_title_norm ON user_ratings(title_norm)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_ratings_imdb_id ON user_ratings(imdb_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_ratings_kp_id ON user_ratings(kp_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_items_title_norm ON items(title_norm)")
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_search_names_item ON item_search_names(item_id)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_search_names_name ON item_search_names(name_norm)"
        )
        # Hot path: get_feed / get_categories_with_counts /
        # get_items_needing_metadata all filter by category_id and
        # is_ignored. Prior to this index every feed page hit was a
        # full scan of items. (4.3)
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_items_cat_ignored ON items(category_id, is_ignored)"
        )
        # find_existing_item / dedup look these up by external id and,
        # when category is known, the category-scoped variant. Plain
        # column indexes are enough for the lookups; the COALESCE
        # against '' keeps the index usable because SQLite stores the
        # NULL in the index but skips empty-string lookups.
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_items_kp_id "
            "ON items(kp_id) WHERE kp_id IS NOT NULL AND kp_id <> ''"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_items_imdb_id "
            "ON items(imdb_id) WHERE imdb_id IS NOT NULL AND imdb_id <> ''"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_items_rezka_url "
            "ON items(rezka_url) WHERE rezka_url IS NOT NULL AND rezka_url <> ''"
        )
        # get_feed batch-loads releases via WHERE item_id IN (...)
        # ORDER BY item_id, date_added DESC; the composite index
        # serves both the IN scan and the ordering. Foreign-key
        # joins from items to releases benefit equally. (4.3)
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_releases_item_date "
            "ON releases(item_id, date_added DESC)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_releases_rutor_id "
            "ON releases(rutor_id) WHERE rutor_id IS NOT NULL"
        )
        # collection_items already has a composite PK
        # (collection_id, item_id), but the reverse direction
        # (item_id -> collections) needs its own index for the
        # `WHERE item_id IN (...)` half of get_feed.
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_collection_items_item ON collection_items(item_id)"
        )

    def check_and_migrate_schema(self):
        with self._conn() as c:
            cur = c.cursor()
            cols = [col[1] for col in cur.execute("PRAGMA table_info(user_ratings)").fetchall()]
            if "original_title" not in cols:
                cur.execute("ALTER TABLE user_ratings ADD COLUMN original_title TEXT")
            if "title_norm" not in cols:
                cur.execute("ALTER TABLE user_ratings ADD COLUMN title_norm TEXT")
            if "original_title_norm" not in cols:
                cur.execute("ALTER TABLE user_ratings ADD COLUMN original_title_norm TEXT")
            if "imdb_id" not in cols:
                cur.execute("ALTER TABLE user_ratings ADD COLUMN imdb_id TEXT")
            if "kp_id" not in cols:
                cur.execute("ALTER TABLE user_ratings ADD COLUMN kp_id TEXT")

            items_cols = [col[1] for col in cur.execute("PRAGMA table_info(items)").fetchall()]
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
                col[1] for col in cur.execute("PRAGMA table_info(collection_items)").fetchall()
            ]
            if "added_at" not in ci_cols:
                cur.execute("ALTER TABLE collection_items ADD COLUMN added_at TEXT")

            items_cols = [col[1] for col in cur.execute("PRAGMA table_info(items)").fetchall()]
            if "latest_season" not in items_cols:
                cur.execute("ALTER TABLE items ADD COLUMN latest_season INTEGER DEFAULT 0")
            if "latest_episode" not in items_cols:
                cur.execute("ALTER TABLE items ADD COLUMN latest_episode INTEGER DEFAULT 0")
            if "tmdb_id" not in items_cols:
                cur.execute("ALTER TABLE items ADD COLUMN tmdb_id TEXT")

            cur.execute("""
                CREATE TABLE IF NOT EXISTS item_search_names (item_id INTEGER, name_norm TEXT)
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS app_state (key TEXT PRIMARY KEY, value TEXT)
            """)
            self._ensure_indexes(cur)
        # Existing installs that go through check_and_migrate_schema()
        # also benefit from the migration runner — keep the call here
        # so the runner has two entry points (init_schema for fresh
        # databases, this one for existing databases that pre-date
        # init_schema's migration call).
        self._apply_migrations()

    # ── Migrations ─────────────────────────────────────────────────

    def _apply_migrations(self) -> None:
        """Run pending migrations under PRAGMA user_version control.

        See migrations/README.md. Migration files live next to db.py
        in `migrations/NNNN_*.sql`; the 4-digit prefix doubles as
        the target user_version. Each file is executed as a single
        transaction; on failure the database is left at the
        previous user_version and the operator must fix the SQL
        before the next boot.
        """
        import os as _os
        import re as _re

        migrations_dir = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "migrations")
        if not _os.path.isdir(migrations_dir):
            return

        files = sorted(f for f in _os.listdir(migrations_dir) if _re.match(r"^\d{4}_.*\.sql$", f))
        if not files:
            return

        with self._conn() as c:
            current_version = int(c.execute("PRAGMA user_version").fetchone()[0])
            for fname in files:
                m = _re.match(r"^(\d{4})_", fname)
                if not m:
                    continue
                target = int(m.group(1))
                if target <= current_version:
                    continue
                sql_path = _os.path.join(migrations_dir, fname)
                with open(sql_path, encoding="utf-8") as f:
                    script = f.read()
                # executescript runs the whole file in implicit
                # transactions; wrap in BEGIN/COMMIT for atomicity
                # of the version bump too. SQLite's PRAGMA cannot
                # be parameterised, so the integer is f-stringed in
                # — safe because `target` is parsed from a fixed
                # filename regex and not user data.
                c.executescript(f"BEGIN;\n{script}\nPRAGMA user_version = {target};\nCOMMIT;")
                print(f"[DB] applied migration {fname} (user_version -> {target})")
                current_version = target

    # ── Items ──────────────────────────────────────────────────────

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
            row = c.execute("SELECT 1 FROM releases WHERE rutor_id = ?", (rutor_id,)).fetchone()
            return row is not None

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

    # ── Feed ────────────────────────────────────────────────────────

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

    # ── Categories ──────────────────────────────────────────────────

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
                "  SUM(CASE WHEN is_ignored = 0 AND (kp_id IS NULL OR kp_id = '') AND (imdb_id IS NULL OR imdb_id = '') THEN 1 ELSE 0 END) AS no_any_id "
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
                {"id": 0, "name": "Любая категория", "count": count_any},
                *cats,
                {"id": -2, "name": "🗑️ ИГНОРИРУЕМЫЕ", "count": count_ignored},
            ]
            if hide_temp_table:
                c.execute(f"DROP TABLE IF EXISTS temp.{hide_temp_table}")
            return result

    # ── Stats ───────────────────────────────────────────────────────

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

    def save_collections_order(self, order: list[int]) -> None:
        with self._conn() as c:
            for i, col_id in enumerate(order):
                c.execute("UPDATE collections SET sort_order = ? WHERE id = ?", (i, col_id))

    # ── Collections export/import (8.2) ─────────────────────────────
    #
    # Item rows are referenced by external identity (kp_id, imdb_id,
    # rezka_url, plus title+year as fallback) rather than internal
    # `id`, so an export from one DB can be re-imported into another
    # par2 instance whose autoincrement IDs differ. `added_at` is
    # preserved verbatim — useful for sort-by-recently-added.

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

    def _resolve_item_id(self, c, ref: dict) -> int | None:
        """Best-effort match of an exported item-ref to a local item.id.

        Tries kp_id first, then imdb_id, then rezka_url, then
        (normalized title + year). Returns None if nothing matches —
        caller decides whether to skip or report.
        """
        # Order matters: kp_id and imdb_id are the most reliable
        # external identifiers; rezka_url is durable but specific to
        # rezka mirror; title+year is a last-resort fuzzy match.
        for col, val in (("kp_id", ref.get("kp_id")), ("imdb_id", ref.get("imdb_id"))):
            if val:
                row = c.execute(f"SELECT id FROM items WHERE {col} = ? LIMIT 1", (val,)).fetchone()
                if row:
                    return int(row["id"])
        rezka_url = ref.get("rezka_url")
        if rezka_url:
            row = c.execute(
                "SELECT id FROM items WHERE rezka_url = ? LIMIT 1", (rezka_url,)
            ).fetchone()
            if row:
                return int(row["id"])
        title = (ref.get("title") or "").strip()
        year = ref.get("year")
        if title and year:
            row = c.execute(
                "SELECT id FROM items WHERE title = ? AND year = ? LIMIT 1",
                (title, year),
            ).fetchone()
            if row:
                return int(row["id"])
        return None

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

    def reassign_search_names(self, old_item_id: int, new_item_id: int, conn=None) -> None:
        with self._conn(conn) as c:
            c.execute(
                "UPDATE OR IGNORE item_search_names SET item_id = ? WHERE item_id = ?",
                (new_item_id, old_item_id),
            )

    def delete_search_names_by_item(self, item_id: int, conn=None) -> None:
        with self._conn(conn) as c:
            c.execute("DELETE FROM item_search_names WHERE item_id = ?", (item_id,))

    # ── Collection Items ────────────────────────────────────────────

    def merge_collection_items(self, old_item_id: int, new_item_id: int, conn=None) -> None:
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

    def get_last_visit(self) -> str | None:
        with self._conn() as c:
            row = c.execute("SELECT value FROM app_state WHERE key = 'last_visit'").fetchone()
            return row[0] if row else None

    # ── Export ──────────────────────────────────────────────────────

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

    # ── Item Stats (for fix_posters etc.) ───────────────────────────

    def get_items_needing_metadata(
        self, check_col: str, batch_size: int = 300, conn=None
    ) -> list[dict]:
        # check_col is interpolated as a column name (placeholders can't be
        # used for identifiers). Whitelist defends against accidental misuse.
        if check_col not in _METADATA_CHECK_COLS:
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

    def get_db_stats(self, conn=None) -> tuple:
        with self._conn(conn) as c:
            video_cats_ph = _placeholders(VIDEO_CATEGORY_IDS)
            video_params = list(VIDEO_CATEGORY_IDS)
            no_poster = c.execute(
                "SELECT COUNT(*) FROM items "
                f"WHERE category_id IN ({video_cats_ph}) "
                "AND (poster_url IS NULL OR poster_url = '')",
                video_params,
            ).fetchone()[0]
            no_ratings = c.execute(
                "SELECT COUNT(*) FROM items "
                f"WHERE category_id IN ({video_cats_ph}) "
                "AND (kp_rating = 0 OR kp_rating IS NULL OR "
                "imdb_rating = 0 OR imdb_rating IS NULL)",
                video_params,
            ).fetchone()[0]
            total = c.execute(
                f"SELECT COUNT(*) FROM items WHERE category_id IN ({video_cats_ph})",
                video_params,
            ).fetchone()[0]
            return total, no_poster, no_ratings

    # ── Filter rules (8.5) ──────────────────────────────────────────

    def list_filter_rules(self, *, only_enabled: bool = False, conn=None) -> list[dict]:
        with self._conn(conn) as c:
            sql = "SELECT * FROM filter_rules"
            if only_enabled:
                sql += " WHERE enabled = 1"
            sql += " ORDER BY id"
            return [dict(r) for r in c.execute(sql).fetchall()]

    def create_filter_rule(
        self,
        *,
        name: str,
        field: str,
        pattern: str,
        action: str,
        enabled: bool = True,
        conn=None,
    ) -> int:
        if field not in FILTER_RULE_FIELDS:
            raise ValueError(f"unsupported field: {field!r}")
        if action not in FILTER_RULE_ACTIONS:
            raise ValueError(f"unsupported action: {action!r}")
        # Validate the pattern up-front so the rule editor surfaces a
        # meaningful error instead of silently storing garbage.
        try:
            re.compile(pattern)
        except re.error as e:
            raise ValueError(f"invalid regex: {e}") from e
        with self._conn(conn) as c:
            cur = c.execute(
                "INSERT INTO filter_rules (name, field, pattern, action, enabled) "
                "VALUES (?, ?, ?, ?, ?)",
                (name.strip(), field, pattern, action, 1 if enabled else 0),
            )
            return int(cur.lastrowid)

    def update_filter_rule(
        self,
        rule_id: int,
        *,
        name: str | None = None,
        field: str | None = None,
        pattern: str | None = None,
        action: str | None = None,
        enabled: bool | None = None,
        conn=None,
    ) -> bool:
        sets: list[str] = []
        params: list = []
        if name is not None:
            sets.append("name = ?")
            params.append(name.strip())
        if field is not None:
            if field not in FILTER_RULE_FIELDS:
                raise ValueError(f"unsupported field: {field!r}")
            sets.append("field = ?")
            params.append(field)
        if pattern is not None:
            try:
                re.compile(pattern)
            except re.error as e:
                raise ValueError(f"invalid regex: {e}") from e
            sets.append("pattern = ?")
            params.append(pattern)
        if action is not None:
            if action not in FILTER_RULE_ACTIONS:
                raise ValueError(f"unsupported action: {action!r}")
            sets.append("action = ?")
            params.append(action)
        if enabled is not None:
            sets.append("enabled = ?")
            params.append(1 if enabled else 0)
        if not sets:
            return False
        sets.append("updated_at = CURRENT_TIMESTAMP")
        params.append(rule_id)
        with self._conn(conn) as c:
            cur = c.execute(
                f"UPDATE filter_rules SET {', '.join(sets)} WHERE id = ?",
                params,
            )
            return cur.rowcount > 0

    def delete_filter_rule(self, rule_id: int, conn=None) -> bool:
        with self._conn(conn) as c:
            cur = c.execute("DELETE FROM filter_rules WHERE id = ?", (rule_id,))
            return cur.rowcount > 0

    # ── Audit log (8.18) ────────────────────────────────────────────

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


db = Database()
