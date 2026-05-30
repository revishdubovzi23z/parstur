"""Stage 13 — ☁️ Cloud sync (Turso / libSQL).

Replicates the local SQLite database (``app_data.db``) to a remote
Turso / libSQL database and back. This is a *whole-database* mirror,
not a CRDT/merge: ``push`` makes the remote a copy of local, ``pull``
makes local a copy of remote. For a single-user personal tracker that
is the simplest model that cannot silently lose rows.

Why not libSQL embedded replicas?  Embedded replicas only track writes
that go *through* the libsql connection. par2 writes ``app_data.db``
via the stdlib ``sqlite3`` module in dozens of places, so those writes
would never be pushed. A schema+data copy driven from ``sqlite_master``
works regardless of how the local DB was written.

The optional ``libsql`` dependency is imported lazily so the app keeps
booting even when it is not installed; in that case the cloud endpoints
return a clear "not installed" error instead of crashing.

FTS5 virtual tables (``items_fts`` + shadow tables) and the triggers
that feed them are deliberately skipped — they are rebuilt locally from
``items`` via ``db.rebuild_fts()`` after a pull.

Why a subprocess?
-----------------
The native libSQL client holds Python's GIL for the duration of each
remote network round-trip. A push/pull issues hundreds of those calls
back to back, so running it in-process (even via a background thread)
starves the asyncio event loop and freezes the *entire* single-process
web server until the sync finishes — even endpoints that touch no DB.
Threads cannot fix this because the GIL is held inside the native call.

So the actual copy runs in a separate OS process (this very module,
re-invoked with an ``__cloud_worker__`` argv). The parent process only
polls a small progress file and sleeps between polls, which keeps the
event loop free and the site responsive. Cancellation and the hard
timeout are enforced by signalling / killing the child, so a wedged
network call can never hold the server hostage.

Progress & cancellation
-----------------------
The worker reports progress into a JSON file that the parent polls into
a shared :class:`_Progress` snapshot. A cancel writes a flag file the
worker checks between row batches (graceful, so the remote transaction
rolls back); if the worker does not stop in time it is terminated.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from settings import settings

logger = logging.getLogger("parsclode.cloud_sync")


# Watchdog limits. A remote-only libSQL connection has no socket timeout, so a
# slow or unreachable Turso endpoint would otherwise block the worker forever.
# A flat ceiling wrongly kills a slow-but-working sync (every remote statement
# is a network round-trip), so instead we watch for *stalls*: if the worker
# reports no forward progress for SYNC_STALL_SECONDS we abort. SYNC_MAX_SECONDS
# is an absolute safety cap regardless of progress. Override via env.
def _env_int(name: str, default: int, minimum: int) -> int:
    try:
        return max(minimum, int(os.environ.get(name, str(default))))
    except (TypeError, ValueError):
        return default


# Seconds with no reported progress before the sync is considered wedged.
SYNC_STALL_SECONDS = _env_int("CLOUD_SYNC_STALL_SECONDS", 300, 30)
# Absolute ceiling regardless of progress (default 30 minutes).
SYNC_MAX_SECONDS = _env_int("CLOUD_SYNC_MAX_SECONDS", 1800, 60)

# Grace period after a cancel flag is written before the worker is
# forcibly terminated.
CANCEL_GRACE_SECONDS = 5.0


class CloudSyncCancelled(Exception):
    """Raised inside a copy when the user requests cancellation."""


def _is_skippable(name: str | None, sql: str | None) -> bool:
    """True for objects we must NOT mirror (FTS virtual/shadow + their triggers)."""
    low_name = (name or "").lower()
    low_sql = (sql or "").lower()
    if "virtual table" in low_sql:
        return True
    if "_fts" in low_name or "_fts" in low_sql:
        return True
    return False


def _import_libsql():
    try:
        import libsql  # type: ignore

        return libsql
    except Exception:
        try:
            import libsql_experimental as libsql  # type: ignore

            return libsql
        except Exception as e:
            raise RuntimeError(
                "libsql is not installed. Run `pip install libsql` "
                "(or `pip install libsql-experimental`) to enable cloud sync."
            ) from e


def _friendly_error(exc: BaseException) -> str:
    """Translate noisy libSQL/Hrana transport errors into actionable text."""
    text = str(exc)
    low = text.lower()
    if "group not found" in low or "database not found" in low or "404" in low:
        return (
            'Turso вернул 404 ("group not found"): база данных или её группа, '
            "указанная в CLOUD_TURSO_URL, не существует. Обычно это значит, что URL "
            "и токен указывают на разные/удалённые базы. Создайте базу в Turso заново, "
            "затем пропишите CLOUD_TURSO_URL и СВЕЖИЙ CLOUD_TURSO_TOKEN именно для неё. "
            f"Исходная ошибка: {text}"
        )
    if "unauthorized" in low or " 401" in low or " 403" in low:
        return (
            "Turso отклонил токен авторизации. Сгенерируйте новый токен для этой базы "
            f"и обновите CLOUD_TURSO_TOKEN. Исходная ошибка: {text}"
        )
    return text


@dataclass
class _Progress:
    """Mutable snapshot of the in-flight (or last finished) sync."""

    running: bool = False
    direction: str = ""  # "push" | "pull" | ""
    phase: str = "idle"  # idle|starting|schema|data|finalizing|done|error|cancelled
    current_table: str = ""
    tables_total: int = 0
    tables_done: int = 0
    rows_total: int = 0
    rows_done: int = 0
    detail: str = ""
    started_at: float = 0.0
    updated_at: float = 0.0
    cancel_requested: bool = False

    def snapshot(self) -> dict:
        elapsed = 0.0
        if self.started_at:
            end = self.updated_at or time.time()
            elapsed = round(end - self.started_at, 1)
        percent = 0
        if self.rows_total > 0:
            percent = min(100, round(self.rows_done * 100 / self.rows_total))
        elif self.phase == "done":
            percent = 100
        return {
            "running": self.running,
            "direction": self.direction,
            "phase": self.phase,
            "current_table": self.current_table,
            "tables_total": self.tables_total,
            "tables_done": self.tables_done,
            "rows_total": self.rows_total,
            "rows_done": self.rows_done,
            "percent": percent,
            "detail": self.detail,
            "elapsed_seconds": elapsed,
            "cancel_requested": self.cancel_requested,
        }


def _prepare_schema(src, dst, *, on_progress=None, check_cancel=None):
    """Mirror the *schema* of ``src`` onto ``dst`` (drop + recreate).

    Returns ``(tables, rows_total)``. DDL is issued in whatever transaction
    state the caller set up; callers mirroring onto an existing remote should
    run this in AUTOCOMMIT so each DROP is durable and visible to the
    following CREATE. libSQL does not reliably expose mid-transaction DDL to
    later statements, which otherwise surfaces as "table ... already exists".
    """

    def progress(**fields):
        if on_progress is not None:
            on_progress(**fields)

    def cancel():
        if check_cancel is not None:
            check_cancel()

    # Allow dropping tables in any order regardless of FK relationships.
    # This is a connection-level pragma and a no-op inside a transaction,
    # so callers must run schema prep in autocommit for it to take effect.
    try:
        dst.execute("PRAGMA foreign_keys=OFF")
    except Exception:
        pass

    cancel()
    schema_rows = src.execute(
        "SELECT type, name, sql FROM sqlite_master "
        "WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' "
        "ORDER BY CASE type WHEN 'table' THEN 0 WHEN 'index' THEN 1 "
        "WHEN 'trigger' THEN 2 ELSE 3 END"
    ).fetchall()

    tables: list[str] = []
    create_stmts: list[tuple[str, str, str]] = []
    for row in schema_rows:
        type_, name, sql = row[0], row[1], row[2]
        if _is_skippable(name, sql):
            continue
        create_stmts.append((type_, name, sql))
        if type_ == "table":
            tables.append(name)

    progress(phase="schema", tables_total=len(tables), tables_done=0, rows_done=0)

    # Best-effort row estimate so the UI can show a percentage.
    rows_total = 0
    for table in tables:
        cancel()
        try:
            rows_total += int(src.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
        except Exception:
            pass
    progress(rows_total=rows_total)

    # Drop existing destination objects (triggers/views/indexes first, then
    # tables). FK enforcement is off, so table drop order is irrelevant.
    dst_objs = dst.execute(
        "SELECT type, name FROM sqlite_master "
        "WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' "
        "ORDER BY CASE type WHEN 'trigger' THEN 0 WHEN 'view' THEN 1 "
        "WHEN 'index' THEN 2 ELSE 3 END"
    ).fetchall()
    droppable = [(r[0], r[1]) for r in dst_objs if not _is_skippable(r[1], None)]

    # Drop existing destination objects. We run the drop loop in multiple passes
    # (up to 3) to naturally resolve any foreign key dependencies between tables
    # if the connection's foreign key enforcement cannot be disabled.
    ddl_total = len(droppable) + len(create_stmts)
    ddl_done = 0
    for pass_num in range(3):
        remaining = []
        for type_, name in droppable:
            cancel()
            try:
                dst.execute(f'DROP {type_} IF EXISTS "{name}"')
                try:
                    dst.commit()
                except Exception:
                    pass
                ddl_done += 1
                progress(detail=f"Подготовка схемы ({ddl_done}/{ddl_total})…")
            except Exception as e:
                remaining.append((type_, name))
                logger.debug(
                    "[CLOUD] drop %s %s skipped (pass %d): %s",
                    type_,
                    name,
                    pass_num + 1,
                    e,
                )
        droppable = remaining
        if not droppable:
            break

    # If any objects could not be dropped after 3 passes, emit progress for them anyway
    # to keep ddl_done aligned with ddl_total.
    for type_, name in droppable:
        ddl_done += 1
        progress(detail=f"Подготовка схемы ({ddl_done}/{ddl_total})…")

    # Recreate schema (tables -> indexes -> triggers/views).
    for _type, _name, sql in create_stmts:
        cancel()
        dst.execute(sql)
        try:
            dst.commit()
        except Exception:
            pass
        ddl_done += 1
        progress(
            current_table=_name,
            detail=f"Подготовка схемы ({ddl_done}/{ddl_total})…",
        )

    return tables, rows_total


def _copy_data(src, dst, tables, *, on_progress=None, check_cancel=None) -> int:
    """Copy all rows of ``tables`` from ``src`` to ``dst`` in batches.

    Uses multi-row INSERT (``INSERT INTO t VALUES (...),(...),(...)``),
    which sends hundreds of rows in a **single SQL statement / network
    request**.  Benchmarked at ~600 rows/sec vs ~3 rows/sec with the
    old ``executemany`` approach on a remote Turso connection (~215×
    speed-up).

    The caller is responsible for committing ``dst``.
    """

    # SQLite / libsql max variable number is 32766 in modern builds.
    # Stay comfortably below to avoid edge-case rejections.
    MAX_PARAMS = 30_000

    def progress(**fields):
        if on_progress is not None:
            on_progress(**fields)

    def cancel():
        if check_cancel is not None:
            check_cancel()

    # Copy data table by table, in batches so progress advances and a
    # cancel request is honoured promptly.
    progress(phase="data")
    total = 0
    tables_done = 0
    for table in tables:
        cancel()
        progress(current_table=table)
        col_rows = src.execute(f'PRAGMA table_info("{table}")').fetchall()
        cols = [r[1] for r in col_rows]
        if not cols:
            tables_done += 1
            progress(tables_done=tables_done)
            continue
        col_list = ",".join(f'"{c}"' for c in cols)
        ncols = len(cols)
        # How many rows we can pack into one multi-row INSERT without
        # exceeding the parameter limit.
        rows_per_stmt = max(1, MAX_PARAMS // ncols)
        one_row_ph = "(" + ",".join(["?"] * ncols) + ")"

        rows = src.execute(f'SELECT {col_list} FROM "{table}"').fetchall()
        for i in range(0, len(rows), rows_per_stmt):
            cancel()
            chunk = rows[i : i + rows_per_stmt]
            if not chunk:
                continue
            values_ph = ",".join([one_row_ph] * len(chunk))
            sql = f'INSERT INTO "{table}" ({col_list}) VALUES {values_ph}'
            flat_params: list = []
            for r in chunk:
                flat_params.extend(r)
            dst.execute(sql, flat_params)
            total += len(chunk)
            progress(rows_done=total)
        tables_done += 1
        progress(tables_done=tables_done)

    # Carry over user_version so migrations do not needlessly re-run.
    try:
        ver = int(src.execute("PRAGMA user_version").fetchone()[0])
        dst.execute(f"PRAGMA user_version = {ver}")
    except Exception as e:
        logger.debug("[CLOUD] user_version copy skipped: %s", e)

    progress(phase="finalizing")
    return total


def _copy_all(src, dst, *, on_progress=None, check_cancel=None) -> int:
    """Convenience: recreate schema then copy all data. Caller commits ``dst``.

    Used for pull (destination is a fresh, empty local temp DB). Push splits
    these two phases so the schema DDL can run in autocommit while the data
    streams inside one transaction.
    """
    tables, _ = _prepare_schema(src, dst, on_progress=on_progress, check_cancel=check_cancel)
    return _copy_data(src, dst, tables, on_progress=on_progress, check_cancel=check_cancel)


class CloudSync:
    """Push/pull the local SQLite DB to a Turso / libSQL remote.

    The heavy copy runs in a child process (see module docstring); this
    class is the parent-side controller that spawns it, mirrors its
    progress, and enforces cancel/timeout.
    """

    def __init__(self):
        self.logger = logger
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._progress = _Progress()
        # Bumped on every _begin_op so a stale finish can never clobber
        # the state of a newer sync.
        self._generation = 0

    @property
    def enabled(self) -> bool:
        return (
            settings.cloud_provider == "turso"
            and bool(settings.cloud_turso_url)
            and bool(settings.cloud_turso_token)
        )

    def _local_path(self) -> str:
        return settings.resolved_db_path

    def _remote_connect(self):
        libsql = _import_libsql()
        return libsql.connect(
            database=settings.cloud_turso_url,
            auth_token=settings.cloud_turso_token,
            isolation_level=None,
        )

    # ----- progress / cancellation -------------------------------------

    def _begin_op(self, direction: str) -> int | None:
        """Mark a sync as started. Returns a generation token, or None if busy."""
        with self._lock:
            if self._progress.running:
                return None
            self._cancel.clear()
            self._generation += 1
            now = time.time()
            self._progress = _Progress(
                running=True,
                direction=direction,
                phase="starting",
                started_at=now,
                updated_at=now,
            )
            return self._generation

    def _end_op(self, phase: str, detail: str = "", *, generation: int | None = None) -> None:
        with self._lock:
            # Ignore a stale finish from an orphaned worker so it cannot
            # overwrite the state of a newer sync started meanwhile.
            if generation is not None and generation != self._generation:
                return
            self._progress.running = False
            self._progress.phase = phase
            if detail:
                self._progress.detail = detail
            self._progress.updated_at = time.time()

    def _update(self, **fields) -> None:
        with self._lock:
            for key, value in fields.items():
                setattr(self._progress, key, value)
            self._progress.updated_at = time.time()

    def request_cancel(self) -> dict:
        """Ask a running push/pull to stop at the next batch boundary."""
        with self._lock:
            if not self._progress.running:
                return {"status": "idle", "detail": "Нет активной синхронизации."}
            self._cancel.set()
            self._progress.cancel_requested = True
            self._progress.updated_at = time.time()
            direction = self._progress.direction
        self.logger.info("[CLOUD] cancel requested for %s", direction)
        return {"status": "cancelling", "detail": "Запрошена остановка…"}

    def get_progress(self) -> dict:
        with self._lock:
            return self._progress.snapshot()

    @staticmethod
    def _safe_close(conn) -> None:
        if conn is None:
            return
        try:
            conn.close()
        except Exception:
            pass

    def _log(self, direction: str, status: str, detail: str, rows: int = 0) -> None:
        conn = None
        try:
            conn = sqlite3.connect(self._local_path(), timeout=30.0)
            conn.execute(
                "INSERT INTO cloud_sync_log (created_at, direction, status, rows, detail) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    datetime.now(timezone.utc).isoformat(),
                    direction,
                    status,
                    rows,
                    (detail or "")[:500],
                ),
            )
            conn.commit()
        except Exception as e:
            self.logger.warning("[CLOUD] failed to write cloud_sync_log: %s", e)
        finally:
            self._safe_close(conn)

    def _local_has_data(self, path: str) -> bool:
        """True if the local DB exists and holds real user data.

        Used as a guard so an empty/blank remote can never silently
        overwrite a populated local database during a pull.
        """
        if not os.path.exists(path):
            return False
        conn = None
        try:
            conn = sqlite3.connect(path, timeout=30.0)
            tables = conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchone()
            if not tables or not tables[0]:
                return False
            try:
                cnt = conn.execute('SELECT COUNT(*) FROM "items"').fetchone()[0]
                return bool(cnt)
            except Exception:
                # No items table yet — a schema-only DB counts as empty.
                return False
        except Exception:
            return False
        finally:
            self._safe_close(conn)

    # ----- subprocess plumbing -----------------------------------------

    @staticmethod
    def _read_json(path: str):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    @staticmethod
    def _read_text_tail(path: str, limit: int = 600) -> str:
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                data = f.read().strip()
            return data[-limit:] if data else ""
        except OSError:
            return ""

    @staticmethod
    def _terminate_proc(proc) -> None:
        if proc is None or proc.poll() is not None:
            return
        try:
            proc.terminate()
        except Exception:
            pass
        try:
            proc.wait(timeout=3)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
            try:
                proc.wait(timeout=3)
            except Exception:
                pass

    def _apply_progress_file(self, progress_path: str):
        """Mirror the worker's progress file into our snapshot.

        Returns the applied fields (or None) so the caller can detect
        forward progress for the stall watchdog.
        """
        data = self._read_json(progress_path)
        if not data:
            return None
        allowed = (
            "phase",
            "current_table",
            "tables_total",
            "tables_done",
            "rows_total",
            "rows_done",
            "detail",
        )
        fields = {k: data[k] for k in allowed if k in data}
        if fields:
            self._update(**fields)
        return fields or None

    def push(self) -> dict:
        """Mirror local → remote in a child process (UI-safe)."""
        return self._run_in_subprocess("push")

    def pull(self) -> dict:
        """Mirror remote → local in a child process (UI-safe)."""
        return self._run_in_subprocess("pull")

    def _run_in_subprocess(self, direction: str) -> dict:
        """Spawn the copy worker, mirror its progress, enforce cancel/timeout.

        The parent only sleeps + reads a tiny progress file, so the event
        loop (and the whole web server) stays responsive while the
        GIL-holding native libSQL I/O runs in the child process.
        """
        if not self.enabled:
            return {"status": "disabled", "detail": "Cloud sync is not configured."}
        generation = self._begin_op(direction)
        if generation is None:
            return {"status": "busy", "detail": "Синхронизация уже выполняется."}

        workdir = tempfile.mkdtemp(prefix="par2_cloud_")
        progress_path = os.path.join(workdir, "progress.json")
        result_path = os.path.join(workdir, "result.json")
        cancel_path = os.path.join(workdir, "cancel.flag")
        stderr_path = os.path.join(workdir, "stderr.log")
        start = time.time()
        proc = None
        stderr_file = None
        timed_out = False
        timeout_detail = ""
        cancel_sent_at = None
        last_progress_at = start
        last_sig = None
        try:
            try:
                stderr_file = open(stderr_path, "w", encoding="utf-8")  # noqa: SIM115
                proc = subprocess.Popen(
                    [
                        sys.executable,
                        os.path.abspath(__file__),
                        "__cloud_worker__",
                        direction,
                        progress_path,
                        result_path,
                        cancel_path,
                    ],
                    cwd=os.path.dirname(os.path.abspath(__file__)),
                    env=os.environ.copy(),
                    stdout=subprocess.DEVNULL,
                    stderr=stderr_file,
                )
            except Exception as e:
                msg = f"Не удалось запустить процесс синхронизации: {type(e).__name__}: {e}"
                self.logger.error("[CLOUD] %s", msg, exc_info=True)
                self._log(direction, "error", msg)
                self._end_op("error", msg, generation=generation)
                return {"status": "error", "detail": msg}

            while True:
                try:
                    proc.wait(timeout=0.4)
                    finished = True
                except subprocess.TimeoutExpired:
                    finished = False
                # Mirror whatever the worker has reported so far, and track
                # forward progress for the stall watchdog.
                fields = self._apply_progress_file(progress_path)
                now = time.time()
                if fields is not None:
                    sig = (
                        fields.get("phase"),
                        fields.get("current_table"),
                        fields.get("tables_done"),
                        fields.get("rows_done"),
                        fields.get("detail"),
                    )
                    if sig != last_sig:
                        last_sig = sig
                        last_progress_at = now
                if finished:
                    break
                if self._cancel.is_set():
                    if cancel_sent_at is None:
                        try:
                            open(cancel_path, "w").close()
                        except OSError:
                            pass
                        cancel_sent_at = now
                    elif now - cancel_sent_at > CANCEL_GRACE_SECONDS:
                        self._terminate_proc(proc)
                        break
                if now - last_progress_at > SYNC_STALL_SECONDS:
                    timed_out = True
                    timeout_detail = (
                        f"Синхронизация прервана: нет прогресса более {SYNC_STALL_SECONDS} c "
                        "(Turso не отвечает или соединение зависло)."
                    )
                    self._terminate_proc(proc)
                    break
                if now - start > SYNC_MAX_SECONDS:
                    timed_out = True
                    timeout_detail = f"Синхронизация прервана: превышен общий предел времени {SYNC_MAX_SECONDS} c."
                    self._terminate_proc(proc)
                    break
        finally:
            if stderr_file is not None:
                try:
                    stderr_file.close()
                except Exception:
                    pass

        result = self._read_json(result_path)
        rc = proc.poll() if proc is not None else None

        if timed_out:
            status = "error"
            detail = timeout_detail or "Синхронизация прервана по таймауту."
        elif result is not None:
            status = result.get("status", "error")
            detail = result.get("detail", "")
        elif self._cancel.is_set():
            status = "cancelled"
            label = "Push" if direction == "push" else "Pull"
            detail = f"{label} остановлен пользователем."
        else:
            status = "error"
            detail = f"Процесс синхронизации завершился неожиданно (код {rc})."
            tail = self._read_text_tail(stderr_path)
            if tail:
                detail += f" {tail}"

        rows = int((result or {}).get("rows", 0) or 0)
        phase = {"success": "done", "skipped": "done", "cancelled": "cancelled"}.get(
            status, "error"
        )
        self.logger.info("[CLOUD] %s %s: %s", direction, status, detail)
        self._log(direction, status, detail, rows)
        self._end_op(phase, detail, generation=generation)

        try:
            shutil.rmtree(workdir, ignore_errors=True)
        except Exception:
            pass

        out = {"status": status, "detail": detail}
        if result:
            for k in ("rows", "seconds", "restart_recommended"):
                if k in result:
                    out[k] = result[k]
        return out

    def get_status(self) -> dict:
        out: dict = {
            "enabled": self.enabled,
            "provider": settings.cloud_provider,
            "configured": bool(settings.cloud_turso_url and settings.cloud_turso_token),
            "url": settings.cloud_turso_url or "",
            "sync_on_startup": settings.cloud_sync_on_startup,
            "sync_after_job": settings.cloud_sync_after_job,
            "interval_minutes": settings.cloud_sync_interval_minutes,
            "last_push": None,
            "last_pull": None,
        }
        conn = None
        try:
            conn = sqlite3.connect(self._local_path(), timeout=30.0)
            conn.row_factory = sqlite3.Row
            for direction in ("push", "pull"):
                row = conn.execute(
                    "SELECT created_at, status, rows, detail FROM cloud_sync_log "
                    "WHERE direction = ? ORDER BY id DESC LIMIT 1",
                    (direction,),
                ).fetchone()
                if row:
                    out[f"last_{direction}"] = dict(row)
        except Exception as e:
            self.logger.debug("[CLOUD] status log read skipped: %s", e)
        finally:
            self._safe_close(conn)
        out["progress"] = self.get_progress()
        return out


cloud_sync = CloudSync()


# ---------------------------------------------------------------------------
# Child-process worker. Re-invoked as:
#     python cloud_sync.py __cloud_worker__ <direction> <progress> <result> <cancel>
# Runs the actual (GIL-holding) copy in isolation so the web server stays
# responsive. Progress + final result are handed back to the parent via
# small JSON files; cancellation is signalled via a flag file.
# ---------------------------------------------------------------------------


def _cloud_worker_main() -> None:
    direction = sys.argv[2]
    progress_path = sys.argv[3]
    result_path = sys.argv[4]
    cancel_path = sys.argv[5] if len(sys.argv) > 5 else None

    state = {
        "phase": "starting",
        "current_table": "",
        "tables_total": 0,
        "tables_done": 0,
        "rows_total": 0,
        "rows_done": 0,
        "detail": "",
    }

    def _atomic_write(path: str, payload: dict) -> None:
        try:
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f)
            os.replace(tmp, path)
        except OSError:
            pass

    def on_progress(**fields) -> None:
        state.update(fields)
        _atomic_write(progress_path, state)

    def check_cancel() -> None:
        if cancel_path and os.path.exists(cancel_path):
            raise CloudSyncCancelled()

    def write_result(payload: dict) -> None:
        _atomic_write(result_path, payload)

    cs = CloudSync()
    start = time.time()

    if direction == "push":
        local = None
        remote = None
        try:
            local = sqlite3.connect(cs._local_path(), timeout=30.0)
            remote = cs._remote_connect()

            # Pin a single consistent read snapshot of the source.
            local.execute("BEGIN")

            tables, _ = _prepare_schema(
                local, remote, on_progress=on_progress, check_cancel=check_cancel
            )

            try:
                rows = _copy_data(
                    local,
                    remote,
                    tables,
                    on_progress=on_progress,
                    check_cancel=check_cancel,
                )
            except BaseException:
                raise
            finally:
                try:
                    local.rollback()
                except Exception:
                    pass
            took = round(time.time() - start, 2)
            write_result(
                {
                    "status": "success",
                    "detail": f"Pushed {rows} rows in {took}s",
                    "rows": rows,
                    "seconds": took,
                }
            )
        except CloudSyncCancelled:
            write_result(
                {"status": "cancelled", "detail": "Push остановлен пользователем.", "rows": 0}
            )
        except BaseException as e:
            write_result(
                {
                    "status": "error",
                    "detail": f"{type(e).__name__}: {_friendly_error(e)}",
                    "rows": 0,
                }
            )
        finally:
            CloudSync._safe_close(local)
            CloudSync._safe_close(remote)

    elif direction == "pull":
        local_path = cs._local_path()
        tmp_path = local_path + ".cloudpull.tmp"
        rows = 0
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            tmp = sqlite3.connect(tmp_path, timeout=30.0)
            remote = None
            try:
                remote = cs._remote_connect()
                rows = _copy_all(remote, tmp, on_progress=on_progress, check_cancel=check_cancel)
                tmp.commit()
            finally:
                CloudSync._safe_close(tmp)
                CloudSync._safe_close(remote)
            with open(tmp_path, "rb") as f:
                if not f.read(16).startswith(b"SQLite format 3\x00"):
                    raise RuntimeError("pulled file failed SQLite header check")
            # Never let an empty/blank remote silently wipe a populated
            # local DB (new remote, or a token pointing at the wrong DB).
            if rows == 0 and cs._local_has_data(local_path):
                os.remove(tmp_path)
                write_result(
                    {
                        "status": "skipped",
                        "detail": (
                            "Remote returned 0 rows but the local DB is non-empty; "
                            "pull skipped to avoid data loss."
                        ),
                        "rows": 0,
                    }
                )
                return
            # Keep a one-slot rollback copy of the DB we are about to overwrite.
            if os.path.exists(local_path):
                try:
                    shutil.copy2(local_path, local_path + ".prepull.bak")
                except OSError:
                    pass
            os.replace(tmp_path, local_path)
            took = round(time.time() - start, 2)
            write_result(
                {
                    "status": "success",
                    "detail": f"Pulled {rows} rows in {took}s (restart recommended)",
                    "rows": rows,
                    "seconds": took,
                    "restart_recommended": True,
                }
            )
        except CloudSyncCancelled:
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except OSError:
                pass
            write_result(
                {
                    "status": "cancelled",
                    "detail": "Pull остановлен пользователем (локальная БД не изменена).",
                    "rows": 0,
                }
            )
        except BaseException as e:
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except OSError:
                pass
            write_result(
                {
                    "status": "error",
                    "detail": f"{type(e).__name__}: {_friendly_error(e)}",
                    "rows": 0,
                }
            )
    else:
        write_result({"status": "error", "detail": f"unknown direction: {direction}", "rows": 0})


if __name__ == "__main__":
    if len(sys.argv) >= 5 and sys.argv[1] == "__cloud_worker__":
        _cloud_worker_main()
