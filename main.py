import asyncio
import hashlib
import json
import os
import re
import sys
import time
import traceback
from contextlib import asynccontextmanager
from datetime import datetime

import uvicorn
from fastapi import (
    FastAPI,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from db import db
from logging_config import setup_logging
from routes import admin, auth, collections, feed, items, process, streams
from script_utils import clear_checkpoint, clear_stop_flag, load_config
from settings import settings

logger = setup_logging("parsclode.main", settings.log_file_path)

# load_dotenv() - redundant, settings.py handles this

# Принудительно устанавливаем ProactorEventLoop для Windows,
# так как только он поддерживает запуск подпроцессов в asyncio.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())


# Менеджер очереди задач
class TaskQueue:
    def __init__(self):
        self.queue = asyncio.Queue()
        self.worker_task = None

    async def add_task(self, coro_func, status_key, *args, **kwargs):
        global process_status
        process_status[status_key] = "queued"
        logger.info(f"[QUEUE] Task added: {status_key}")
        await self.queue.put((coro_func, status_key, args, kwargs))

    async def worker(self):
        logger.info("[WORKER] Started and waiting for tasks...")
        while True:
            coro_func, status_key, args, kwargs = await self.queue.get()
            try:
                logger.info(f"[WORKER] Executing task: {status_key} ({coro_func.__name__})")
                await coro_func(*args, **kwargs)
                logger.info(f"[WORKER] Task finished: {status_key}")
            except Exception as e:
                logger.error(f"[WORKER] Error in task {status_key}: {e}", exc_info=True)
            finally:
                self.queue.task_done()

    def start(self):
        logger.info("[QUEUE] Starting worker task...")
        self.worker_task = asyncio.create_task(self.worker())

    def stop(self):
        if self.worker_task:
            logger.info("[QUEUE] Stopping worker task...")
            self.worker_task.cancel()


task_queue = TaskQueue()

from routes.auth import (
    _auth_enabled,
    _check_auth,
    _check_token,
    _session_tokens,
    _ws_tickets,
)

# 6.3 — captured during the lifespan startup hook so worker threads
# (run_in_executor callbacks) can schedule coroutines back onto the
# main loop with asyncio.run_coroutine_threadsafe(coro, _main_loop).
# In a worker thread asyncio.get_event_loop() returns a *different*
# loop (or a new one in 3.12+), so it cannot be used for that.
_main_loop: asyncio.AbstractEventLoop | None = None


def _broadcast_threadsafe(message: dict) -> None:
    """Schedule ws_manager.broadcast on the main loop from any thread.

    Safe to call from worker threads spawned by `run_in_executor` —
    grabs `_main_loop` (captured in lifespan startup) and submits the
    coroutine cross-thread. If the loop hasn't been captured yet
    (e.g. broadcast attempted before startup completed), silently
    drops the message rather than raising.
    """
    if _main_loop is None or _main_loop.is_closed():
        return
    try:
        asyncio.run_coroutine_threadsafe(ws_manager.broadcast(message), _main_loop)
    except Exception as e:
        logger.error(f"[WS] threadsafe broadcast failed: {type(e).__name__}: {e}")


# 6.1 — replaces the deprecated @app.on_event("startup"/"shutdown")
# decorators with a single lifespan async context manager. The body
# above the `yield` is the startup phase; the body below `yield` is
# shutdown. The body references names defined later in the module
# (task_queue, _wal_checkpoint_task, running_processes, _init_rezka_session)
# — Python resolves these at call time (during ASGI startup), so the
# forward references are fine.
@asynccontextmanager
async def lifespan(_app):
    # ── startup ────────────────────────────────────────────────────
    logger.info("[SERVER] Startup event triggered")
    # 6.3 — capture the running loop so threads spawned via
    # run_in_executor can schedule coroutines back on it via
    # asyncio.run_coroutine_threadsafe.
    global _main_loop
    _main_loop = asyncio.get_running_loop()
    # Make sure the DB schema is up-to-date on every boot. Both
    # init_schema (CREATE TABLE IF NOT EXISTS) and check_and_migrate_schema
    # are idempotent; together they cover both fresh databases and
    # installs whose schema predates a recently-added migration
    # (e.g. filter_rules / audit_log added in 0002, tmdb_id in 0003).
    # Running schema setup inside the FastAPI lifespan instead of at
    # module import time means any DAO call that hits a new table
    # will see it on the very first request after upgrade.
    try:
        db.init_schema()
    except Exception as e:
        logger.error(f"[DB] schema init failed: {type(e).__name__}: {e}", exc_info=True)
    task_queue.start()
    try:
        db.ensure_fts_indexed()
    except Exception as e:
        logger.warning(f"[FTS5] Index init skipped: {e}")
    # Run one checkpoint at boot so the *previous* run's WAL is reaped
    # immediately rather than waiting WAL_CHECKPOINT_INTERVAL_SECONDS.
    try:
        await asyncio.to_thread(db.wal_checkpoint, "TRUNCATE")
    except Exception as e:
        logger.warning(f"[WAL] startup checkpoint skipped: {type(e).__name__}: {e}")
    global _wal_checkpoint_task, _session_gc_task, _rezka_retry_task
    _wal_checkpoint_task = asyncio.create_task(_wal_checkpoint_loop())
    _session_gc_task = asyncio.create_task(_session_gc_loop())
    _rezka_retry_task = asyncio.create_task(_rezka_session_retry_loop())

    yield

    # ── shutdown ───────────────────────────────────────────────────
    logger.info("[SERVER] Shutdown event triggered")
    if _wal_checkpoint_task is not None:
        _wal_checkpoint_task.cancel()
        try:
            await _wal_checkpoint_task
        except (asyncio.CancelledError, Exception):
            pass
        _wal_checkpoint_task = None

    if _session_gc_task is not None:
        _session_gc_task.cancel()
        try:
            await _session_gc_task
        except (asyncio.CancelledError, Exception):
            pass
        _session_gc_task = None

    if _rezka_retry_task is not None:
        _rezka_retry_task.cancel()
        try:
            await _rezka_retry_task
        except (asyncio.CancelledError, Exception):
            pass
        _rezka_retry_task = None
    config = load_config()
    graceful_timeout = config.get("shutdown", {}).get("graceful_timeout", 5)
    for key, proc in running_processes.items():
        if key in ("active_pipeline_proc", "active_pipeline_key"):
            continue
        if proc and proc.returncode is None:
            logger.info(f"[SHUTDOWN] Writing stop flag for: {key}")
            flag_path = os.path.join(settings.app_data_dir, f"stop_{key}.flag")
            with open(flag_path, "w") as f:
                f.write("stop")
    await asyncio.sleep(min(graceful_timeout, 2))
    for key, proc in running_processes.items():
        if key in ("active_pipeline_proc", "active_pipeline_key"):
            continue
        if proc and proc.returncode is None:
            logger.info(f"[SHUTDOWN] Force terminating: {key}")
            proc.terminate()
    task_queue.stop()


limiter = Limiter(key_func=get_remote_address)
app = FastAPI(title="Tracker Filter", lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.include_router(auth.router)
app.include_router(feed.router)
app.include_router(collections.router)
app.include_router(process.router)
app.include_router(streams.router)
app.include_router(admin.router)
app.include_router(items.router)


class ConnectionManager:
    def __init__(self):
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket):
        if ws in self.active:
            self.active.remove(ws)

    async def broadcast(self, message: dict):
        # 6.4 — fan out send_json calls in parallel via asyncio.gather.
        # Previously this was a sequential `for ws in self.active: await
        # ws.send_json(...)` loop; a slow/disconnected client would
        # block every other client behind it (the WS spec doesn't time
        # out send_json, the kernel buffers do, and a TCP send to a
        # half-closed socket can sit for ~minutes). Fanning out keeps
        # broadcast latency at max(slowest client) rather than
        # sum(all clients). return_exceptions=True so a single failing
        # client doesn't abort the whole gather.
        if not self.active:
            return
        sockets = list(self.active)
        results = await asyncio.gather(
            *(ws.send_json(message) for ws in sockets),
            return_exceptions=True,
        )
        for ws, result in zip(sockets, results, strict=True):
            if isinstance(result, BaseException):
                self.disconnect(ws)


ws_manager = ConnectionManager()

rezka_session = None
rezka_session_folders_cache = None
rezka_session_state = "down"  # down, connecting, up


def _init_rezka_session() -> bool:
    global rezka_session, rezka_session_folders_cache, rezka_session_state
    rezka_email = settings.rezka_email
    rezka_password = settings.rezka_password
    if not rezka_email or not rezka_password:
        logger.info("[REZKA] No credentials, session skipped")
        rezka_session_state = "down"
        return False

    rezka_session_state = "connecting"
    _broadcast_threadsafe({"type": "rezka_session", "state": "connecting"})

    try:
        from HdRezkaApi import HdRezkaSession as _Session

        rezka_session = _Session("https://rezka.ag/")
        rezka_session.login(rezka_email, rezka_password)
        _refresh_rezka_folders_cache()
        logger.info(f"[REZKA] Session initialized, cookies: {list(rezka_session.cookies.keys())}")
        rezka_session_state = "up"
        _broadcast_threadsafe({"type": "rezka_session", "state": "up"})
        return True
    except Exception as e:
        logger.error(f"[REZKA] Session init failed: {e}")
        rezka_session = None
        rezka_session_state = "down"
        _broadcast_threadsafe({"type": "rezka_session", "state": "down"})
        return False


# 6.7 — re-login detection.
#
# Rezka quietly invalidates the cookie when (a) the session expires
# from inactivity (~weeks) or (b) the same account logs in from
# another device. Symptoms: the GET / POST returns either 401, 302
# back to /login, or 200 with the public login-popup HTML. None of
# these raise a Python exception, so the previous code happily
# treated a logged-out session as "everything is fine" and silently
# dropped favorites.
#
# `_rezka_session_dead` sniffs a response and decides whether the
# cookie is still good; `_rezka_request` is a tiny wrapper around
# requests.get/post that, on detected logout, calls
# `_init_rezka_session()` once to re-login and retries the original
# request with the fresh cookies. Subsequent failures fall through
# to the caller — re-login should be cheap, but spinning on it is
# never useful.
_REZKA_LOGIN_MARKERS = (
    b"b-loginpopup",
    b"forgot_password",
    b'name="login_name"',
    b'id="login_email"',
)


def _rezka_session_dead(resp) -> bool:
    """Return True if `resp` looks like rezka has logged us out."""
    if resp is None:
        return False
    # 401/403 are the obvious "auth required" markers.
    if resp.status_code in (401, 403):
        return True
    # rezka.ag uses a 302 to /login.html for some auth-required
    # endpoints (e.g. /favorites/). Without follow_redirects the
    # status will be 302; with follow_redirects it'll be 200 but
    # the URL will end at /login.html — both checked.
    location = (resp.headers.get("Location") or "").lower()
    final_url = (getattr(resp, "url", "") or "").lower()
    if any(needle in location for needle in ("/login", "/auth")):
        return True
    if any(needle in final_url for needle in ("/login.html", "/auth")):
        return True
    # Content sniff: the public login popup ships these markers, the
    # logged-in HTML does not. Avoid false positives by checking the
    # body length is non-trivial first.
    body = resp.content or b""
    if len(body) >= 256 and any(m in body for m in _REZKA_LOGIN_MARKERS):
        # The favorites page itself doesn't ship these markers when
        # logged in; if we see them on /favorites/ the session is
        # gone.
        return True
    return False


def _rezka_request(method: str, url: str, **kwargs):
    """requests.request(...) with one transparent re-login retry.

    Caller should pass `cookies=rezka_session.cookies` themselves —
    the helper updates them in-place after a successful re-login.
    Returns the final response (whether or not retry happened) or
    None when no rezka_session is configured.
    """
    if rezka_session is None:
        return None
    import requests as _req

    resp = _req.request(method, url, **kwargs)
    if not _rezka_session_dead(resp):
        return resp
    logger.warning(f"[REZKA] session looks dead (status={resp.status_code}, url={url}); re-login")
    try:
        _init_rezka_session()
    except Exception as e:
        logger.error(f"[REZKA] re-login failed: {type(e).__name__}: {e}", exc_info=True)
        return resp
    if rezka_session is None:
        # Re-login itself failed (e.g. credentials wrong now).
        return resp
    # Refresh the caller's cookie jar reference so the retry has
    # the new auth cookie.
    kwargs["cookies"] = rezka_session.cookies
    return _req.request(method, url, **kwargs)


def _refresh_rezka_folders_cache():
    global rezka_session_folders_cache
    if not rezka_session:
        return
    try:
        import re as _re

        from bs4 import BeautifulSoup as _BS

        from app_core import normalize_title

        resp = _rezka_request(
            "GET",
            "https://rezka.ag/favorites/",
            headers={"User-Agent": "Mozilla/5.0"},
            cookies=rezka_session.cookies,
            timeout=15,
        )
        if resp is None:
            return
        soup = _BS(resp.content, "html.parser")
        sidebar = soup.find("div", class_="b-favorites_content__sidebarbar") or soup.find(
            "div", class_="b-favorites_content__sidebar"
        )
        folders = {}
        if sidebar:
            for a in sidebar.find_all("a", href=True):
                href = a.get("href", "")
                if "javascript" in href:
                    continue
                text = a.text.strip()
                name = _re.sub(r"\s*\(\d+\)", "", text).strip()
                m = _re.search(r"/favorites/(\d+)/", href)
                if m:
                    folders[normalize_title(name)] = m.group(1)
        rezka_session_folders_cache = folders
        logger.info(f"[REZKA] Folders cache refreshed: {len(folders)} folders")
    except Exception as e:
        logger.error(f"[REZKA] Folders cache refresh failed: {e}", exc_info=True)
        rezka_session_folders_cache = None


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket, token: str | None = None, ticket: str | None = None):
    # WebSocket upgrades bypass HTTP middleware, so we must enforce auth here
    # ourselves when it is enabled.
    # 5.10: Support both ?token=... (legacy) and ?ticket=... (preferred)
    # Tickets are one-time use and short-lived.
    is_authed = False
    if not _auth_enabled or (token and _check_token(token)):
        is_authed = True
    elif ticket:
        now = time.time()
        expiry = _ws_tickets.get(ticket)
        if expiry and now <= expiry:
            is_authed = True
            _ws_tickets.pop(ticket, None)  # One-time use

    if not is_authed:
        # 4401 is an application-defined close code in the 4000-4999 range
        # reserved for app use; we use it to mean "unauthenticated".
        await ws.close(code=4401)
        return
    await ws_manager.connect(ws)
    try:
        progress = {}
        for key in process_status.keys():
            progress[key] = _read_progress(key)
        await ws.send_json(
            {
                "type": "status",
                "statuses": dict(process_status),
                "progress": progress,
                "rezka_session": rezka_session_state,
            }
        )
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(ws)
    except Exception:
        ws_manager.disconnect(ws)


def _read_progress(key):
    if not _is_valid_status_key(key):
        return {"current": 0, "total": 0}
    p_file = os.path.join(settings.app_data_dir, f"progress_{key}.json")
    if os.path.exists(p_file):
        try:
            with open(p_file) as f:
                return json.load(f)
        except Exception:
            return {"current": 0, "total": 0}
    return {"current": 0, "total": 0}


# 6.5 — liveness + readiness probe.
#
# Designed for Docker HEALTHCHECK, k8s probes, and uptime monitors.
# Returns 200 with {ok: true, ...} when the process is alive AND the
# SQLite database is reachable; returns 503 with the failure reason
# when the DB ping fails. Intentionally exempt from auth (see
# auth_middleware) so probes work with or without `AUTH_USER` set.
#
# The DB ping is a parameter-less `SELECT 1` wrapped in
# `asyncio.to_thread` so we don't block the event loop on a slow
# disk. The check intentionally does NOT touch any user data — it
# only proves that the connection pool / WAL / schema are usable.
@app.get("/health")
async def health():
    db_ok = True
    db_error: str | None = None
    user_version: int | None = None
    try:

        def _ping() -> int:
            with db._conn() as c:
                _ = c.execute("SELECT 1").fetchone()
                return int(c.execute("PRAGMA user_version").fetchone()[0])

        user_version = await asyncio.to_thread(_ping)
    except Exception as e:
        db_ok = False
        db_error = f"{type(e).__name__}: {e}"

    payload: dict = {
        "ok": db_ok,
        "service": "par2",
        "db": {"ok": db_ok, "user_version": user_version},
        "queue": {
            "size": task_queue.queue.qsize() if task_queue.queue else 0,
            "worker_active": (
                task_queue.worker_task is not None and not task_queue.worker_task.done()
            ),
        },
    }
    if db_error:
        payload["db"]["error"] = db_error
    return JSONResponse(payload, status_code=200 if db_ok else 503)


# Single Content-Security-Policy and a small set of supporting headers.
# Notes on the policy:
#   * The frontend is a single index.html that pulls Vue 3, Tailwind Play
#     and SortableJS straight off public CDNs, so script-src has to allow
#     those origins and 'unsafe-eval' (Vue runtime templates + Tailwind
#     play compile JS at runtime) and 'unsafe-inline' (Tailwind injects
#     <style> tags).
#   * Posters come from arbitrary external hosts (TMDB, Kinopoisk, Rezka)
#     so img-src has to allow https: and data:.
#   * connect-src has to allow ws:/wss: for the /ws endpoint, plus any
#     same-origin XHRs.
#   * frame-ancestors 'none' replicates X-Frame-Options: DENY in modern
#     browsers; we send both for compatibility.
_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' 'unsafe-eval' "
    "https://unpkg.com https://cdn.tailwindcss.com https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com; "
    "img-src 'self' data: https:; "
    "media-src 'self' blob: https:; "
    "connect-src 'self' ws: wss: https:; "
    "frame-src 'self' https://www.youtube.com https://www.youtube-nocookie.com; "
    "worker-src 'self' blob:; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)
_SECURITY_HEADERS = {
    "Content-Security-Policy": _CSP,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Permissions-Policy": (
        "geolocation=(), camera=(), microphone=(), payment=(), usb=(), interest-cohort=()"
    ),
}


@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    for header, value in _SECURITY_HEADERS.items():
        # Don't clobber a header set explicitly by an endpoint.
        response.headers.setdefault(header, value)
    return response


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    if not _auth_enabled:
        return await call_next(request)
    path = request.url.path
    if path in (
        "/api/login",
        "/api/logout",
        "/api/auth_status",
        # 6.5 — /health is intentionally unauthenticated so that
        # liveness/readiness probes (Docker HEALTHCHECK, k8s, uptime
        # monitors) work even with auth turned on. The endpoint
        # discloses only whether the DB is reachable, no row data.
        "/health",
        "/",
        "/manifest.json",
        "/icon.png",
        "/sw.js",
    ):
        return await call_next(request)
    if path.startswith("/api/") or path.startswith("/assets/"):
        if not _check_auth(request):
            return HTMLResponse(content="Unauthorized", status_code=401)
    return await call_next(request)


# Periodic SQLite WAL checkpoint (4.1).
# In WAL mode, every committed write goes into <db>-wal first and is
# only folded back into the main file by the auto-checkpoint heuristic
# (default ~1000 frames, ~4 MB). Long sync / reprocess runs commit
# faster than auto-checkpoint can drain, so the WAL sidecar can grow
# to hundreds of MB and stay that big until restart. Forcing a
# TRUNCATE checkpoint at startup and on a slow timer pins the WAL to
# zero bytes between bursts.
WAL_CHECKPOINT_INTERVAL_SECONDS = 30 * 60  # 30 minutes
_wal_checkpoint_task: asyncio.Task | None = None

# 5.6 — background session GC.
SESSION_GC_INTERVAL_SECONDS = 60 * 60  # 1 hour
_session_gc_task: asyncio.Task | None = None

# 5.8 — Rezka retry loop
_rezka_retry_task: asyncio.Task | None = None


async def _rezka_session_retry_loop():
    """Background loop that retries rezka session initialization if it fails."""
    # First attempt
    success = await asyncio.to_thread(_init_rezka_session)
    if success:
        return

    wait = 300  # 5 minutes
    while True:
        try:
            await asyncio.sleep(wait)
            success = await asyncio.to_thread(_init_rezka_session)
            if success:
                logger.info("[REZKA] Session recovery successful")
                break
            # Exponential backoff up to 1 hour
            wait = min(wait * 2, 3600)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"[REZKA] Retry loop error: {e}")
            await asyncio.sleep(60)


async def _wal_checkpoint_loop() -> None:
    while True:
        await asyncio.sleep(WAL_CHECKPOINT_INTERVAL_SECONDS)
        try:
            busy, log_pages, ckpt_pages = await asyncio.to_thread(db.wal_checkpoint, "TRUNCATE")
            if busy:
                # A long-running reader/writer held the WAL; not fatal,
                # the next tick will retry. Worth logging once so the
                # operator knows why the sidecar didn't shrink.
                logger.warning(
                    f"[WAL] checkpoint busy (log_pages={log_pages}, checkpointed={ckpt_pages})"
                )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"[WAL] checkpoint error: {type(e).__name__}: {e}", exc_info=True)


async def _session_gc_loop() -> None:
    """Periodically removes expired tokens from the in-memory session store."""
    while True:
        await asyncio.sleep(SESSION_GC_INTERVAL_SECONDS)
        try:
            now = time.time()
            expired = [t for t, exp in _session_tokens.items() if now > exp]
            if expired:
                for t in expired:
                    _session_tokens.pop(t, None)
                logger.info(f"[AUTH] GC reaped {len(expired)} expired sessions")

            now = time.time()
            expired_tickets = [t for t, exp in _ws_tickets.items() if now > exp]
            if expired_tickets:
                for t in expired_tickets:
                    _ws_tickets.pop(t, None)
                logger.info(f"[AUTH] GC reaped {len(expired_tickets)} expired WS tickets")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"[AUTH] GC error: {e}")


@app.get("/api/debug/queue")
async def debug_queue():
    """Отладочный эндпоинт для проверки состояния очереди.

    Выдаёт состояние очереди задач и `process_status` — это потенциально
    чувствительная инфо (внутренние ключи, состояние worker-а), поэтому
    эндпоинт доступен только при включённой авторизации (HTTP-middleware
    в этом случае требует Bearer-токен; без auth эндпоинт не существует).
    """
    if not _auth_enabled:
        raise HTTPException(status_code=404)
    # 6.2 — inside a coroutine, asyncio.get_running_loop() is the
    # modern API; get_event_loop() is deprecated and on Python 3.12+
    # may create a brand-new loop if none is set.
    loop = asyncio.get_running_loop()
    return {
        "loop_type": str(type(loop)),
        "queue_size": task_queue.queue.qsize(),
        "worker_active": task_queue.worker_task is not None and not task_queue.worker_task.done(),
        "process_status": process_status,
    }


@app.post("/api/rebuild_fts")
def rebuild_fts():
    count = db.rebuild_fts()
    return {"status": "ok", "indexed": count}


@app.get("/manifest.json")
def get_manifest():
    return FileResponse("manifest.json")


def _sw_version() -> str:
    """Build a cache-bust token for the service worker.

    Hashes (mtime, size) of index.html + sw.js + manifest.json so any
    deploy-time change to those files yields a new SW body and the
    browser re-installs the worker (which clears prior caches in the
    activate handler — see sw.js:24).
    """
    parts: list[str] = []
    for fname in ("index.html", "sw.js", "manifest.json"):
        try:
            st = os.stat(fname)
            parts.append(f"{fname}:{int(st.st_mtime)}:{st.st_size}")
        except OSError:
            parts.append(f"{fname}:missing")
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:12]


@app.get("/sw.js")
def get_sw():
    # 7.1 — substitute __SW_VERSION__ at request time so each deploy
    # produces a different sw.js body (different bytes -> browser
    # reinstalls the worker -> activate handler purges old caches).
    try:
        with open("sw.js", encoding="utf-8") as f:
            body = f.read()
    except OSError:
        return JSONResponse({"error": "sw.js missing"}, status_code=500)
    body = body.replace("__SW_VERSION__", _sw_version())
    return HTMLResponse(
        content=body,
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache, max-age=0"},
    )


@app.get("/icon.png")
def get_icon():
    if os.path.exists("icon.png"):
        return FileResponse("icon.png")
    return FileResponse("static/icon.png")


# Single source of truth for every background-process key the app knows
# about. running_processes (real Popen objects) and process_status (idle /
# queued / running / done / error) are both derived from this tuple so the
# two dicts can never drift out of sync — previously running_processes was
# missing 'poiskkino' and 'rezka_collections' even though both endpoints
# wrote to it on start.
#
# active_pipeline_* are sentinel slots inside running_processes only; they
# don't carry a status and aren't a "process key".
PROCESS_KEYS: tuple[str, ...] = (
    "sync_video",
    "sync_other",
    "fix",
    "poiskkino",
    "reprocess",
    "user",
    "cleanup",
    "rezka",
    "rezka_collections",
    "full_pipeline",
    "single_update",
)

running_processes: dict = {key: None for key in PROCESS_KEYS}
running_processes["active_pipeline_proc"] = None
running_processes["active_pipeline_key"] = None

process_status: dict[str, str] = {key: "idle" for key in PROCESS_KEYS}

# Whitelist of valid status keys, used to guard endpoints/IO that build
# paths from a key (stop_<key>.flag, progress_<key>.json, etc.). Anything
# outside this set must be rejected to prevent path traversal via
# /api/stop/{key}.
VALID_STATUS_KEYS = frozenset(PROCESS_KEYS)
pipeline_stop_requested = False


def _is_valid_status_key(key: str) -> bool:
    return isinstance(key, str) and key in VALID_STATUS_KEYS


async def run_script(script_name, status_key):
    global process_status, running_processes
    process_status[status_key] = "running"
    await ws_manager.broadcast({"type": "status", "key": status_key, "value": "running"})

    progress_file = f"progress_{status_key}.json"
    if os.path.exists(progress_file):
        try:
            os.remove(progress_file)
        except Exception:
            pass

    script_path = os.path.abspath(script_name)
    if not os.path.exists(script_path):
        logger.error(f"[WORKER] Script not found: {script_path}")
        process_status[status_key] = "error"
        return

    env = {
        **os.environ,
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
        "PYTHONUNBUFFERED": "1",
        "STATUS_KEY": status_key,
    }
    try:
        proc = await asyncio.create_subprocess_exec(sys.executable, script_path, env=env)
        running_processes[status_key] = proc
        await proc.wait()
        process_status[status_key] = "completed" if proc.returncode == 0 else "stopped"
    except Exception as e:
        logger.error(f"Ошибка при запуске {script_name}: {e}", exc_info=True)
        process_status[status_key] = "error"
    finally:
        running_processes[status_key] = None
        clear_stop_flag(status_key)
        if process_status[status_key] == "completed":
            clear_checkpoint(status_key)
        await ws_manager.broadcast(
            {
                "type": "status",
                "key": status_key,
                "value": process_status[status_key],
            }
        )


def check_any_running():
    """Проверяет, запущен ли какой-либо фоновый процесс, чтобы избежать конфликтов в БД."""
    for key, status in process_status.items():
        if status in ["running", "queued"]:
            raise HTTPException(
                status_code=400,
                detail=f"Другой процесс ({key}) уже {'запущен' if status == 'running' else 'в очереди'}. Пожалуйста, дождитесь его завершения.",
            )


async def run_pipeline_task():
    """Последовательный запуск всех этапов синхронизации."""
    global process_status, pipeline_stop_requested
    process_status["full_pipeline"] = "running"
    pipeline_stop_requested = False

    steps = [
        ("sync_job.py", ["video", "0", "0"], "sync_video", "sync_video_log.txt"),
        ("reprocess_database.py", [], "reprocess", "reprocess_log.txt"),
        (
            "fix_posters.py",
            ["poiskkino", "fix_poiskkino_log.txt"],
            "poiskkino",
            "fix_poiskkino_log.txt",
        ),
        ("fix_posters.py", ["tech", "fix_tech_log.txt"], "fix", "fix_tech_log.txt"),
        ("rezka_sync.py", [], "rezka", "sync_rezka_log.txt"),
        ("cleanup_duplicates.py", [], "cleanup", "cleanup_log.txt"),
    ]

    try:
        for script, args, key, log in steps:
            if pipeline_stop_requested:
                logger.info("ПАЙПЛАЙН ОСТАНОВЛЕН ПОЛЬЗОВАТЕЛЕМ")
                break

            # Запускаем шаг и ждем его завершения
            await run_script_with_args(script, args, key, log, is_pipeline_step=True)

            # Если шаг был остановлен или упал с ошибкой - прерываем цепочку
            if process_status[key] in ["stopped", "error"]:
                logger.warning(
                    f"Шаг {key} завершился со статусом {process_status[key]}. Прерываю цикл."
                )
                break

        if pipeline_stop_requested:
            process_status["full_pipeline"] = "stopped"
        else:
            process_status["full_pipeline"] = "completed"

    except Exception as e:
        logger.error(f"ОШИБКА В ПАЙПЛАЙНЕ: {e}", exc_info=True)
        process_status["full_pipeline"] = "error"
    finally:
        pipeline_stop_requested = False


async def run_script_with_args(
    script_name, args, status_key, log_file=None, is_pipeline_step=False
):
    global process_status, running_processes
    process_status[status_key] = "running"
    await ws_manager.broadcast({"type": "status", "key": status_key, "value": "running"})

    start_time = datetime.now()
    last_progress_broadcast = 0

    # Очищаем старый файл прогресса
    progress_file = f"progress_{status_key}.json"
    if os.path.exists(progress_file):
        try:
            os.remove(progress_file)
        except Exception:
            pass

    # Используем абсолютный путь к скрипту
    script_path = os.path.abspath(script_name)
    if not os.path.exists(script_path):
        logger.error(f"[WORKER] Script not found: {script_path}")
        process_status[status_key] = "error"
        return

    env = {
        **os.environ,
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
        "PYTHONUNBUFFERED": "1",
        "STATUS_KEY": status_key,
    }
    status = "completed"

    try:
        logger.info(f"[WORKER] Starting process: {sys.executable} -u {script_path} {args}")

        # Запускаем через PIPE для надежного чтения вывода в asyncio
        # Флаг -u отключает буферизацию вывода в самом Python
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-u",
            script_path,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
        )

        logger.info(f"[WORKER] Process started with PID: {proc.pid}")
        running_processes[status_key] = proc
        if is_pipeline_step:
            running_processes["active_pipeline_proc"] = proc
            running_processes["active_pipeline_key"] = status_key

        # Читаем вывод в реальном времени и пишем в лог
        if log_file:
            with open(log_file, "a", encoding="utf-8") as f:
                if not is_pipeline_step:
                    f.write(
                        f"=== [WORKER] Запуск {script_name} ({datetime.now().strftime('%H:%M:%S')}) ===\n"
                    )
                    f.flush()

                while True:
                    chunk = await proc.stdout.read(1024)
                    if not chunk:
                        break

                    decoded_chunk = chunk.decode("utf-8", errors="replace")
                    f.write(decoded_chunk)
                    f.flush()

                    await ws_manager.broadcast(
                        {
                            "type": "log",
                            "key": status_key,
                            "data": decoded_chunk,
                        }
                    )

                    now = time.monotonic()
                    if now - last_progress_broadcast > 1.0:
                        last_progress_broadcast = now
                        prog = _read_progress(status_key)
                        await ws_manager.broadcast(
                            {
                                "type": "progress",
                                "key": status_key,
                                "current": prog.get("current", 0),
                                "total": prog.get("total", 0),
                            }
                        )
        else:
            await proc.wait()

        # Ждем завершения на случай если цикл чтения прервался раньше
        await proc.wait()

        if proc.returncode == 0:
            status = "completed"
        elif proc.returncode in [-15, 1, 15]:
            status = "stopped"
        else:
            status = "error"

    except Exception as e:
        logger.error(f"Ошибка при выполнении {script_name}: {e}", exc_info=True)
        if log_file:
            try:
                with open(log_file, "a", encoding="utf-8") as f:
                    f.write(f"\n[CRITICAL ERROR] Не удалось запустить процесс: {e}\n")
                    f.write(traceback.format_exc())
            except Exception:
                pass
        status = "error"
    finally:
        running_processes[status_key] = None
        if is_pipeline_step:
            running_processes["active_pipeline_proc"] = None
            running_processes["active_pipeline_key"] = None
        process_status[status_key] = status
        clear_stop_flag(status_key)
        if status == "completed":
            clear_checkpoint(status_key)

        await ws_manager.broadcast(
            {
                "type": "status",
                "key": status_key,
                "value": status,
            }
        )
        await ws_manager.broadcast(
            {
                "type": "progress",
                "key": status_key,
                "current": _read_progress(status_key).get("current", 0),
                "total": _read_progress(status_key).get("total", 0),
            }
        )

        # Записываем историю
        end_time = datetime.now()
        duration = (end_time - start_time).total_seconds()

        items_processed = 0
        total_items = 0
        if os.path.exists(progress_file):
            try:
                with open(progress_file) as f:
                    data = json.load(f)
                    items_processed = data.get("current", 0)
                    total_items = data.get("total", 0)
            except Exception:
                pass

        try:
            db.insert_job_history(
                status_key,
                start_time.strftime("%Y-%m-%d %H:%M:%S"),
                end_time.strftime("%Y-%m-%d %H:%M:%S"),
                duration,
                items_processed,
                total_items,
                status,
            )
        except Exception as e:
            logger.error(f"Ошибка записи истории: {e}", exc_info=True)


def _recover_rezka_url(item_id: int, old_url: str) -> str | None:
    from HdRezkaApi.search import HdRezkaSearch

    row = (
        db.get_connection()
        .cursor()
        .execute("SELECT title, year FROM items WHERE id = ?", (item_id,))
        .fetchone()
    )
    if not row:
        return None
    title = row["title"]
    year = row["year"]
    from app_core import clean_title_for_search

    clean = clean_title_for_search(title)
    if not clean:
        return None
    try:
        results = HdRezkaSearch("https://rezka.ag")(f"{clean} {year}")
    except Exception:
        return None
    old_num = re.search(r"/(\d+)-", old_url)
    if not old_num:
        return None
    old_id = old_num.group(1)
    for r in results:
        url = r.get("url", "")
        if not url or url == old_url:
            continue
        new_num = re.search(r"/(\d+)-", url)
        if new_num and new_num.group(1) == old_id:
            conn = db.get_connection()
            conn.execute("UPDATE items SET rezka_url = ? WHERE id = ?", (url, item_id))
            conn.commit()
            conn.close()
            logger.info(f"[REZKA] URL recovered for item {item_id}: {old_url} -> {url}")
            return url
    return None


def _get_rezka_obj(item_id: int, rezka_url: str):
    from HdRezkaApi import HdRezkaApi

    cookies = rezka_session.cookies if rezka_session else {"hdmbbs": "1"}
    try:
        rezka = HdRezkaApi(rezka_url, cookies=cookies)
        if rezka.ok:
            return rezka, rezka_url
    except Exception:
        pass
    new_url = _recover_rezka_url(item_id, rezka_url)
    if new_url:
        try:
            rezka = HdRezkaApi(new_url, cookies=cookies)
            if rezka.ok:
                return rezka, new_url
        except Exception:
            pass
    return None, rezka_url


@app.get("/", response_class=HTMLResponse)
def get_index():
    with open("index.html", encoding="utf-8") as f:
        return f.read()


_LOG_FILES = {
    "video": "sync_video_log.txt",
    "other": "sync_other_log.txt",
    "fix": "fix_tech_log.txt",
    "fix_poiskkino": "fix_poiskkino_log.txt",
    "user": "user_sync_log.txt",
    "reprocess": "reprocess_log.txt",
    "cleanup": "cleanup_log.txt",
    "rezka": "sync_rezka_log.txt",
    "rezka_collections": "rezka_collections_log.txt",
    "single_update": "single_update_log.txt",
}


# 6.6 — on-demand DB backup download.
#
# Generates a fresh consistent snapshot of the SQLite database and
# streams it back as a file attachment. Behind auth_middleware so
# only logged-in clients can pull it. The snapshot is written via
# `Database.backup_to()` (SQLite online backup API) so the live
# app keeps serving while the copy runs.
#
# The backup is staged in `backups/` (auto-created) and named
# `app_data-<UTC-timestamp>.db`; the same file is served back. The
# caller can also drive periodic snapshots from cron via
# `python backup_db.py --rotate N`.


# 8.17 — manual rebind of KP/IMDb/Rezka identifiers from the card UI.


# 8.5 — filter rules CRUD endpoints.
class FilterRuleCreate(BaseModel):
    name: str
    field: str
    pattern: str
    action: str
    enabled: bool = True


class FilterRuleUpdate(BaseModel):
    name: str | None = None
    field: str | None = None
    pattern: str | None = None
    action: str | None = None
    enabled: bool | None = None


@app.get("/api/filter_rules")
def filter_rules_list():
    return db.list_filter_rules()


@app.post("/api/filter_rules")
def filter_rules_create(data: FilterRuleCreate):
    try:
        rid = db.create_filter_rule(
            name=data.name,
            field=data.field,
            pattern=data.pattern,
            action=data.action,
            enabled=data.enabled,
        )
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return {"id": rid}


@app.put("/api/filter_rules/{rule_id}")
def filter_rules_update(rule_id: int, data: FilterRuleUpdate):
    try:
        ok = db.update_filter_rule(
            rule_id,
            name=data.name,
            field=data.field,
            pattern=data.pattern,
            action=data.action,
            enabled=data.enabled,
        )
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    if not ok:
        return JSONResponse({"error": "rule not found"}, status_code=404)
    return {"status": "success"}


@app.delete("/api/filter_rules/{rule_id}")
def filter_rules_delete(rule_id: int):
    ok = db.delete_filter_rule(rule_id)
    if not ok:
        return JSONResponse({"error": "rule not found"}, status_code=404)
    return {"status": "success"}


# 8.7 — TMDB trailers. Returns the highest-confidence YouTube key
# for the requested item, picking an official trailer when available.


# 8.12 — same lookup as /api/stream_m3u but returns the resolved
# direct URL as JSON so the frontend can render an embedded player
# (HTML5 <video> / hls.js) instead of triggering an m3u download.


# 8.12 — subtitle CORS proxy. Rezka serves VTT/SRT files without
# Access-Control-Allow-Origin, so the browser refuses to attach them
# to a <track> element. We re-fetch and stream the body back with
# permissive CORS so HTML5 captions render. Only forwards http(s)
# URLs whose host belongs to a small allow-list (rezka mirrors).
_SUBTITLE_HOST_ALLOWLIST = (
    ".rezka.ag",
    ".voidboost.net",
    ".videocdn.tv",
    "hdrezka.app",
    ".rezka.cdnstream.tv",
)


# 8.18 — audit log endpoints + undo.
@app.get("/api/audit_log")
def audit_log_list(limit: int = 50, item_id: int | None = None):
    return db.list_audit(limit=limit, item_id=item_id)


@app.post("/api/audit_log/{audit_id}/undo")
def audit_log_undo(audit_id: int):
    import json as _json

    with db._conn() as c:
        row = c.execute("SELECT * FROM audit_log WHERE id = ?", (audit_id,)).fetchone()
        if row is None:
            return JSONResponse({"error": "audit entry not found"}, status_code=404)
        if row["undone"]:
            return JSONResponse({"error": "already undone"}, status_code=400)

        action = row["action"]
        if action == "rebind":
            try:
                before = _json.loads(row["old_value"] or "{}")
            except Exception:
                return JSONResponse({"error": "corrupt audit row"}, status_code=500)
            sets: list[str] = []
            params: list = []
            for col in ("kp_id", "imdb_id", "rezka_url"):
                if col in before:
                    sets.append(f"{col} = ?")
                    params.append(before[col])
            if sets:
                params.append(row["item_id"])
                c.execute(
                    f"UPDATE items SET {', '.join(sets)} WHERE id = ?",
                    params,
                )
        else:
            return JSONResponse(
                {"error": f"undo not supported for action: {action}"},
                status_code=400,
            )
        c.execute("UPDATE audit_log SET undone = 1 WHERE id = ?", (audit_id,))
    return {"status": "success"}


@app.post("/api/mark_visited")
def mark_visited():
    now = db.mark_visited()
    return {"status": "success", "last_visit": now}


@app.get("/api/last_visit")
def get_last_visit():
    return {"last_visit": db.get_last_visit()}


def _trigger_restart():
    """Triggers a server restart using the command defined in settings.
    Returns True if a command was started, False otherwise.
    """
    import subprocess

    if settings.restart_command:
        try:
            subprocess.Popen(
                settings.restart_command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return True
        except Exception as e:
            logger.error(f"Failed to trigger restart: {e}")
            return False
    return False


_reset_tokens: dict = {}


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False, loop="asyncio")
