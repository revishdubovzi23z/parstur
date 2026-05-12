import logging
import os
import re
import sqlite3
from contextlib import contextmanager
from functools import lru_cache

from app_core import RUTOR_CATEGORIES, VIDEO_CATEGORY_IDS

logger = logging.getLogger('parsclode.db')

logger = logging.getLogger("parsclode.db")

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

_METADATA_CHECK_COLS = frozenset(
    {"checked_tech", "checked_uz", "checked_poiskkino", "checked_rezka"}
)

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


class DbCore:
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

            migrations_dir = _os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "migrations")
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
                    logger.info(f"[DB] applied migration {fname} (user_version -> {target})")
                    current_version = target

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

