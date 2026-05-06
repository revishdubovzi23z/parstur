import asyncio
import base64
import csv
import hashlib
import io
import json
import os
import re
import secrets
import sys
import time
from contextlib import asynccontextmanager
from datetime import datetime

import uvicorn
from dotenv import load_dotenv
from fastapi import (
    FastAPI,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel

from db import db
from script_utils import clear_checkpoint, clear_stop_flag, load_config

load_dotenv()

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
        print(f"[QUEUE] Task added: {status_key}")
        await self.queue.put((coro_func, status_key, args, kwargs))

    async def worker(self):
        print("[WORKER] Started and waiting for tasks...")
        while True:
            coro_func, status_key, args, kwargs = await self.queue.get()
            try:
                print(f"[WORKER] Executing task: {status_key} ({coro_func.__name__})")
                await coro_func(*args, **kwargs)
                print(f"[WORKER] Task finished: {status_key}")
            except Exception as e:
                print(f"[WORKER] Error in task {status_key}: {e}")
                import traceback

                traceback.print_exc()
            finally:
                self.queue.task_done()

    def start(self):
        print("[QUEUE] Starting worker task...")
        self.worker_task = asyncio.create_task(self.worker())

    def stop(self):
        if self.worker_task:
            print("[QUEUE] Stopping worker task...")
            self.worker_task.cancel()


task_queue = TaskQueue()

AUTH_USER = os.getenv("AUTH_USER", "")
AUTH_PASS = os.getenv("AUTH_PASS", "")
# Preferred way to configure the password: a pbkdf2_sha256-encoded hash. When
# AUTH_PASS_HASH is set we ignore AUTH_PASS for verification (but still allow
# AUTH_PASS as a one-off fallback when the hash isn't provided so existing
# deployments don't break). Generate a hash with:
#
#     python -c "import hashlib,base64,secrets,sys; pw=sys.argv[1]; \
#       salt=secrets.token_bytes(16); \
#       h=hashlib.pbkdf2_hmac('sha256', pw.encode(), salt, 600_000); \
#       print('pbkdf2_sha256$600000$'+base64.b64encode(salt).decode()+'$' \
#             +base64.b64encode(h).decode())" 'your-password'
AUTH_PASS_HASH = os.getenv("AUTH_PASS_HASH", "")
_auth_enabled = bool(AUTH_USER) and bool(AUTH_PASS or AUTH_PASS_HASH)

# Map token -> Unix-epoch expiry timestamp. We use a sliding 7-day TTL so a
# user who keeps using the app stays logged in indefinitely, but a token that
# was issued and never used past 7 days becomes invalid.
SESSION_TTL_SECONDS = 7 * 24 * 60 * 60
_session_tokens: dict[str, float] = {}


def _verify_password(plaintext: str) -> bool:
    """Return True if plaintext matches the configured password.

    Verification is constant-time. AUTH_PASS_HASH (pbkdf2_sha256) takes
    precedence; AUTH_PASS plaintext is used as a fallback for backward
    compatibility.
    """
    if AUTH_PASS_HASH:
        try:
            algo, iter_str, salt_b64, hash_b64 = AUTH_PASS_HASH.split("$", 3)
        except ValueError:
            return False
        if algo != "pbkdf2_sha256":
            return False
        try:
            iterations = int(iter_str)
        except ValueError:
            return False
        try:
            salt = base64.b64decode(salt_b64)
            expected = base64.b64decode(hash_b64)
        except Exception:
            return False
        actual = hashlib.pbkdf2_hmac("sha256", plaintext.encode("utf-8"), salt, iterations)
        return secrets.compare_digest(actual, expected)
    if AUTH_PASS:
        return secrets.compare_digest(plaintext.encode("utf-8"), AUTH_PASS.encode("utf-8"))
    return False


def _check_token(token: str) -> bool:
    """Return True if token is known and not expired; refresh sliding TTL."""
    expiry = _session_tokens.get(token)
    if expiry is None:
        return False
    now = time.time()
    if now > expiry:
        _session_tokens.pop(token, None)
        return False
    # Sliding refresh — every successful check pushes the expiry forward.
    _session_tokens[token] = now + SESSION_TTL_SECONDS
    return True


def _check_auth(request: Request) -> bool:
    if not _auth_enabled:
        return True
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return _check_token(auth[7:])
    return False


async def require_auth(request: Request):
    if not _auth_enabled:
        return
    if _check_auth(request):
        return
    raise HTTPException(
        status_code=401, detail="Unauthorized", headers={"WWW-Authenticate": "Basic"}
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
        print(f"[WS] threadsafe broadcast failed: {type(e).__name__}: {e}")


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
    print("[SERVER] Startup event triggered")
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
        db.check_and_migrate_schema()
    except Exception as e:
        print(f"[DB] schema init failed: {type(e).__name__}: {e}")
    task_queue.start()
    try:
        db.ensure_fts_indexed()
    except Exception as e:
        print(f"[FTS5] Index init skipped: {e}")
    # Run one checkpoint at boot so the *previous* run's WAL is reaped
    # immediately rather than waiting WAL_CHECKPOINT_INTERVAL_SECONDS.
    try:
        await asyncio.to_thread(db.wal_checkpoint, "TRUNCATE")
    except Exception as e:
        print(f"[WAL] startup checkpoint skipped: {type(e).__name__}: {e}")
    global _wal_checkpoint_task
    _wal_checkpoint_task = asyncio.create_task(_wal_checkpoint_loop())
    # 6.2 — get_running_loop() is the modern, non-deprecated way; it
    # raises if called outside an async context (good — we are inside
    # one here) instead of get_event_loop()'s creates-a-loop fallback.
    await asyncio.get_running_loop().run_in_executor(None, _init_rezka_session)

    yield

    # ── shutdown ───────────────────────────────────────────────────
    print("[SERVER] Shutdown event triggered")
    if _wal_checkpoint_task is not None:
        _wal_checkpoint_task.cancel()
        try:
            await _wal_checkpoint_task
        except (asyncio.CancelledError, Exception):
            pass
        _wal_checkpoint_task = None
    config = load_config()
    graceful_timeout = config.get("shutdown", {}).get("graceful_timeout", 5)
    for key, proc in running_processes.items():
        if key in ("active_pipeline_proc", "active_pipeline_key"):
            continue
        if proc and proc.returncode is None:
            print(f"[SHUTDOWN] Writing stop flag for: {key}")
            with open(f"stop_{key}.flag", "w") as f:
                f.write("stop")
    await asyncio.sleep(min(graceful_timeout, 2))
    for key, proc in running_processes.items():
        if key in ("active_pipeline_proc", "active_pipeline_key"):
            continue
        if proc and proc.returncode is None:
            print(f"[SHUTDOWN] Force terminating: {key}")
            proc.terminate()
    task_queue.stop()


app = FastAPI(title="Tracker Filter", lifespan=lifespan)


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


def _init_rezka_session():
    global rezka_session, rezka_session_folders_cache
    rezka_email = os.getenv("REZKA_EMAIL", "")
    rezka_password = os.getenv("REZKA_PASSWORD", "")
    if not rezka_email or not rezka_password:
        print("[REZKA] No credentials, session skipped", flush=True)
        return
    try:
        from HdRezkaApi import HdRezkaSession as _Session

        rezka_session = _Session("https://rezka.ag/")
        rezka_session.login(rezka_email, rezka_password)
        _refresh_rezka_folders_cache()
        print(
            f"[REZKA] Session initialized, cookies: {list(rezka_session.cookies.keys())}",
            flush=True,
        )
    except Exception as e:
        print(f"[REZKA] Session init failed: {e}", flush=True)
        rezka_session = None


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
    print(f"[REZKA] session looks dead (status={resp.status_code}, url={url}); re-login")
    try:
        _init_rezka_session()
    except Exception as e:
        print(f"[REZKA] re-login failed: {type(e).__name__}: {e}")
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
        print(f"[REZKA] Folders cache refreshed: {len(folders)} folders")
    except Exception as e:
        print(f"[REZKA] Folders cache refresh failed: {e}")
        rezka_session_folders_cache = None


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket, token: str | None = None):
    # WebSocket upgrades bypass HTTP middleware, so we must enforce auth here
    # ourselves when it is enabled. Token is passed as a query parameter
    # because browsers do not allow setting custom headers on WebSocket open.
    if _auth_enabled and (not token or not _check_token(token)):
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
            {"type": "status", "statuses": dict(process_status), "progress": progress}
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
    p_file = f"progress_{key}.json"
    if os.path.exists(p_file):
        try:
            with open(p_file) as f:
                return json.load(f)
        except Exception:
            return {"current": 0, "total": 0}
    return {"current": 0, "total": 0}


@app.post("/api/login")
async def login(request: Request):
    if not _auth_enabled:
        return {"token": "none", "auth_enabled": False}
    body = await request.json()
    user = body.get("username", "") or ""
    password = body.get("password", "") or ""
    # Constant-time on both username and password to avoid leaking whether
    # the username is correct via timing.
    user_ok = secrets.compare_digest(user.encode("utf-8"), AUTH_USER.encode("utf-8"))
    pass_ok = _verify_password(password)
    if user_ok and pass_ok:
        token = secrets.token_hex(32)
        _session_tokens[token] = time.time() + SESSION_TTL_SECONDS
        return {"token": token, "auth_enabled": True}
    raise HTTPException(status_code=401, detail="Invalid credentials")


@app.post("/api/logout")
async def logout(request: Request):
    """Invalidate the bearer token from the request, if any.

    Always returns 200 — even if the token was unknown, expired or missing —
    so callers can use this as a fire-and-forget on the way out.
    """
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        _session_tokens.pop(auth[7:], None)
    return {"ok": True}


@app.get("/api/auth_status")
async def auth_status():
    return {"auth_enabled": _auth_enabled}


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


async def _wal_checkpoint_loop() -> None:
    while True:
        await asyncio.sleep(WAL_CHECKPOINT_INTERVAL_SECONDS)
        try:
            busy, log_pages, ckpt_pages = await asyncio.to_thread(db.wal_checkpoint, "TRUNCATE")
            if busy:
                # A long-running reader/writer held the WAL; not fatal,
                # the next tick will retry. Worth logging once so the
                # operator knows why the sidecar didn't shrink.
                print(f"[WAL] checkpoint busy (log_pages={log_pages}, checkpointed={ckpt_pages})")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[WAL] checkpoint error: {type(e).__name__}: {e}")


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
        print(f"[WORKER] Script not found: {script_path}")
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
        print(f"Ошибка при запуске {script_name}: {e}")
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
                print("ПАЙПЛАЙН ОСТАНОВЛЕН ПОЛЬЗОВАТЕЛЕМ")
                break

            # Запускаем шаг и ждем его завершения
            await run_script_with_args(script, args, key, log, is_pipeline_step=True)

            # Если шаг был остановлен или упал с ошибкой - прерываем цепочку
            if process_status[key] in ["stopped", "error"]:
                print(f"Шаг {key} завершился со статусом {process_status[key]}. Прерываю цикл.")
                break

        if pipeline_stop_requested:
            process_status["full_pipeline"] = "stopped"
        else:
            process_status["full_pipeline"] = "completed"

    except Exception as e:
        print(f"ОШИБКА В ПАЙПЛАЙНЕ: {e}")
        process_status["full_pipeline"] = "error"
    finally:
        pipeline_stop_requested = False


@app.post("/api/start_full_pipeline")
async def start_full_pipeline():
    check_any_running()
    await task_queue.add_task(run_pipeline_task, "full_pipeline")
    return {"status": "started"}


@app.post("/api/start_sync_video")
async def start_sync_video(
    min_year: int = None,
    max_year: int = None,
    min_date: str = None,
):
    check_any_running()

    log_file = "sync_video_log.txt"
    with open(log_file, "w", encoding="utf-8") as f:
        f.write(f"=== Запуск парсинга ВИДЕО ({datetime.now().strftime('%H:%M:%S')}) ===\n")
        if min_date:
            f.write(f"Ручная дата начала: {min_date}\n")
        if min_year:
            f.write(f"Фильтр года: от {min_year}\n")
        if max_year:
            f.write(f"Фильтр года: до {max_year}\n")

    args = ["video", str(min_year or 0), str(max_year or 0)]
    if min_date:
        args.append(min_date)

    await task_queue.add_task(
        run_script_with_args, "sync_video", "sync_job.py", args, "sync_video", log_file
    )
    return {"status": "started"}


@app.post("/api/start_sync_other")
async def start_sync_other(
    min_year: int = None,
    max_year: int = None,
    min_date: str = None,
):
    check_any_running()

    log_file = "sync_other_log.txt"
    with open(log_file, "w", encoding="utf-8") as f:
        f.write(f"=== Запуск парсинга ИГР и СОФТА ({datetime.now().strftime('%H:%M:%S')}) ===\n")
        if min_date:
            f.write(f"Ручная дата начала: {min_date}\n")

    args = ["other", str(min_year or 0), str(max_year or 0)]
    if min_date:
        args.append(min_date)

    await task_queue.add_task(
        run_script_with_args, "sync_other", "sync_job.py", args, "sync_other", log_file
    )
    return {"status": "started"}


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
        print(f"[WORKER] Script not found: {script_path}")
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
        print(f"[WORKER] Starting process: {sys.executable} -u {script_path} {args}")

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

        print(f"[WORKER] Process started with PID: {proc.pid}")
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

                    # 6.2 — `time.monotonic()` is exactly what
                    # `loop.time()` returns under the hood (see
                    # asyncio source) and avoids the deprecated
                    # `get_event_loop()` call here.
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
        print(f"Ошибка при выполнении {script_name}: {e}")
        import traceback

        tb = traceback.format_exc()
        print(tb)
        if log_file:
            try:
                with open(log_file, "a", encoding="utf-8") as f:
                    f.write(f"\n[CRITICAL ERROR] Не удалось запустить процесс: {e}\n")
                    f.write(tb)
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
            print(f"Ошибка записи истории: {e}")


@app.post("/api/start_sync_rezka")
async def start_sync_rezka():
    check_any_running()

    log_file = "sync_rezka_log.txt"
    with open(log_file, "w", encoding="utf-8") as f:
        f.write(f"=== Запуск синхронизации REZKA ({datetime.now().strftime('%H:%M:%S')}) ===\n")

    await task_queue.add_task(run_script_with_args, "rezka", "rezka_sync.py", [], "rezka", log_file)
    return {"status": "started"}


@app.post("/api/start_cleanup")
async def start_cleanup():
    # cleanup_duplicates rewrites items/releases/collection_items and may
    # delete rows; it must never run alongside another job (e.g. sync_job
    # or reprocess_database) that mutates the same tables.
    check_any_running()
    log_file = "cleanup_log.txt"
    with open(log_file, "w", encoding="utf-8") as f:
        f.write("=== Запуск очистки дубликатов ===\n")
    await task_queue.add_task(
        run_script_with_args,
        "cleanup",
        "cleanup_duplicates.py",
        [],
        "cleanup",
        log_file,
    )
    return {"status": "started"}


@app.post("/api/start_rezka_collections")
async def start_rezka_collections():
    # check_any_running() raises HTTPException itself when something is busy
    # and returns None otherwise; calling it as a boolean was misleading
    # (the if branch only ever fired through the raised exception inside
    # check_any_running, never via the truthiness check). Call it directly.
    check_any_running()
    log_file = "rezka_collections_log.txt"
    with open(log_file, "w", encoding="utf-8") as f:
        f.write("=== Синхронизация коллекций Rezka ===\n")
    await task_queue.add_task(
        run_script_with_args,
        "rezka_collections",
        "rezka_collections_sync.py",
        [],
        "rezka_collections",
        log_file,
    )
    return {"status": "started"}


@app.post("/api/stop/{key}")
async def stop_process(key: str):
    global pipeline_stop_requested

    if not _is_valid_status_key(key):
        raise HTTPException(status_code=400, detail="Unknown process key")

    config = load_config()
    graceful_timeout = config.get("shutdown", {}).get("graceful_timeout", 5)

    if key == "full_pipeline":
        pipeline_stop_requested = True
        active_key = running_processes.get("active_pipeline_key")
        if active_key and _is_valid_status_key(active_key):
            with open(f"stop_{active_key}.flag", "w") as f:
                f.write("stop")
        proc = running_processes.get("active_pipeline_proc")
        if proc and proc.returncode is None:
            try:
                await asyncio.wait_for(proc.wait(), timeout=graceful_timeout)
            except asyncio.TimeoutError:
                proc.terminate()
        process_status["full_pipeline"] = "stopped"
        return {"status": "stopped"}

    proc = running_processes.get(key)
    if proc and proc.returncode is None:
        with open(f"stop_{key}.flag", "w") as f:
            f.write("stop")
        try:
            await asyncio.wait_for(proc.wait(), timeout=graceful_timeout)
        except asyncio.TimeoutError:
            proc.terminate()
        process_status[key] = "stopped"
        return {"status": "stopped"}
    return {"status": "not_running"}


@app.post("/api/start_fix")
async def start_fix():
    check_any_running()

    log_file = "fix_tech_log.txt"
    with open(log_file, "w", encoding="utf-8") as f:
        f.write(f"=== Поиск (Legacy API) {datetime.now().strftime('%H:%M:%S')} ===\n")
    await task_queue.add_task(
        run_script_with_args,
        "fix",
        "fix_posters.py",
        ["tech", log_file],
        "fix",
        log_file,
    )
    return {"status": "started"}


@app.post("/api/start_fix_poisk")
async def start_fix_poisk():
    check_any_running()

    log_file = "fix_poiskkino_log.txt"
    with open(log_file, "w", encoding="utf-8") as f:
        f.write(f"=== Поиск (PoiskKino API) {datetime.now().strftime('%H:%M:%S')} ===\n")
    await task_queue.add_task(
        run_script_with_args,
        "poiskkino",
        "fix_posters.py",
        ["poiskkino", log_file],
        "poiskkino",
        log_file,
    )
    return {"status": "started"}


@app.post("/api/start_reprocess")
async def start_reprocess(force: bool = False):
    check_any_running()

    log_file = "reprocess_log.txt"
    with open(log_file, "w", encoding="utf-8") as f:
        f.write(f"=== Полное обновление базы {datetime.now().strftime('%H:%M:%S')} ===\n")

    args = []
    if force:
        args.append("--force")

    await task_queue.add_task(
        run_script_with_args,
        "reprocess",
        "reprocess_database.py",
        args,
        "reprocess",
        log_file,
    )
    return {"status": "started"}


@app.post("/api/update_item/{item_id}")
async def update_item(item_id: int):
    # Этот процесс можно запускать параллельно, так как он трогает только одну запись
    # Но для безопасности логов лучше тоже через run_script_with_args
    log_file = "single_update_log.txt"
    with open(log_file, "w", encoding="utf-8") as f:
        f.write(f"=== Обновление карточки ID {item_id} ===\n")

    await task_queue.add_task(
        run_script_with_args,
        "single_update",
        "single_item_update.py",
        [str(item_id)],
        "single_update",
        log_file,
    )
    return {"status": "started"}


@app.get("/api/process_status")
def get_process_status():
    """Возвращает статусы и прогресс всех процессов."""
    progress = {}
    for key in process_status.keys():
        p_file = f"progress_{key}.json"
        if os.path.exists(p_file):
            try:
                with open(p_file) as f:
                    progress[key] = json.load(f)
            except Exception:
                progress[key] = {"current": 0, "total": 0}
        else:
            progress[key] = {"current": 0, "total": 0}

    return {"statuses": process_status, "progress": progress}


@app.get("/api/feed")
def get_feed(
    category_id: int = -1,
    collection_id: int = None,
    search: str = None,
    min_kp: float = 0.0,
    max_kp: float = 10.0,
    min_imdb: float = 0.0,
    max_imdb: float = 10.0,
    min_year: int = None,
    max_year: int = None,
    min_date: str = None,
    max_date: str = None,
    hide_ignored: bool = True,
    hide_rated: bool = False,
    hide_collected: bool = False,
    page: int = 1,
    limit: int = None,
):
    if limit is None:
        limit = load_config().get("feed", {}).get("default_limit", 20)
    return db.get_feed(
        category_id=category_id,
        collection_id=collection_id,
        search=search,
        min_kp=min_kp,
        max_kp=max_kp,
        min_imdb=min_imdb,
        max_imdb=max_imdb,
        min_year=min_year,
        max_year=max_year,
        min_date=min_date,
        max_date=max_date,
        hide_ignored=hide_ignored,
        hide_rated=hide_rated,
        hide_collected=hide_collected,
        page=page,
        limit=limit,
    )


@app.post("/api/ignore/{item_id}")
def ignore_item(item_id: int):
    new_state = db.toggle_ignore(item_id)
    if new_state < 0:
        return {"status": "error"}
    return {"status": "success"}


class ResetFieldsRequest(BaseModel):
    fields: list[str]


@app.post("/api/reset_item/{item_id}")
def reset_item(item_id: int, data: ResetFieldsRequest):
    db.reset_item(item_id, data.fields)
    return {"status": "success"}


@app.get("/api/categories")
def get_categories(hide_rated: bool = False, hide_collected: bool = False):
    return db.get_categories_with_counts(hide_rated, hide_collected)


@app.get("/api/stats")
def get_stats():
    return db.get_stats()


@app.get("/api/job_history")
def get_job_history(limit: int = 20):
    return db.get_job_history(limit)


@app.get("/api/collections")
def get_collections():
    return db.get_collections()


class CollectionCreate(BaseModel):
    name: str


def _rezka_folder_action(action: str, params: dict):
    if not rezka_session:
        return None
    try:
        params["action"] = action
        resp = _rezka_request(
            "POST",
            "https://rezka.ag/ajax/favorites/",
            data=params,
            headers={
                "User-Agent": "Mozilla/5.0",
                "X-Requested-With": "XMLHttpRequest",
            },
            cookies=rezka_session.cookies,
            timeout=10,
        )
        if resp is not None:
            result = resp.json()
            _refresh_rezka_folders_cache()
            return result
    except Exception as e:
        print(f"[REZKA FOLDER ACTION ERROR] {e}")
    return None


@app.post("/api/collections")
def create_collection(data: CollectionCreate):
    try:
        db.create_collection(data.name)
    except Exception:
        return {"status": "error", "message": "Коллекция существует"}
    if rezka_session:
        _rezka_folder_action("add_cat", {"name": data.name})
    return {"status": "success"}


@app.delete("/api/collections/{id}")
def delete_collection(id: int):
    if rezka_session:
        row = (
            db.get_connection()
            .cursor()
            .execute("SELECT name FROM collections WHERE id = ?", (id,))
            .fetchone()
        )
        if row:
            from app_core import normalize_title

            coll_norm = normalize_title(row["name"])
            if rezka_session_folders_cache and coll_norm in rezka_session_folders_cache:
                _rezka_folder_action(
                    "remove_cat", {"cat_id": rezka_session_folders_cache[coll_norm]}
                )
    db.delete_collection(id)
    return {"status": "success"}


class CollectionRename(BaseModel):
    name: str


@app.put("/api/collections/{id}")
def rename_collection(id: int, data: CollectionRename):
    cat_id = None
    if rezka_session:
        row = (
            db.get_connection()
            .cursor()
            .execute("SELECT name FROM collections WHERE id = ?", (id,))
            .fetchone()
        )
        if row:
            from app_core import normalize_title

            coll_norm = normalize_title(row["name"])
            if rezka_session_folders_cache and coll_norm in rezka_session_folders_cache:
                cat_id = rezka_session_folders_cache[coll_norm]
    db.rename_collection(id, data.name)
    if cat_id:
        _rezka_folder_action("change_cat_name", {"cat_id": cat_id, "name": data.name})
    return {"status": "success"}


# 8.2 — collections export/import.
#
# Two formats:
#   * JSON (default) — full structure, preserves added_at, sort_order
#     and all item identifiers. Round-trips losslessly.
#   * CSV — flat (collection_name, kp_id, imdb_id, rezka_url, title,
#     original_title, year, added_at, sort_order). Easier to edit by
#     hand or pipe through a spreadsheet; still re-importable.
#
# Items are referenced by external identity (kp_id / imdb_id /
# rezka_url / title+year), NOT by autoincrement id, so an export
# from one par2 DB can be merged into another instance whose ids
# differ. See `Database.export_collections` / `import_collections`.
@app.get("/api/collections/export")
def collections_export(fmt: str = "json"):
    payload = db.export_collections()
    if fmt == "csv":
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(
            [
                "collection_name",
                "sort_order",
                "kp_id",
                "imdb_id",
                "rezka_url",
                "title",
                "original_title",
                "year",
                "added_at",
            ]
        )
        for col in payload:
            name = col["name"]
            sort_order = col.get("sort_order") or 0
            items = col.get("items") or []
            if not items:
                # 8.2 — also emit a row for an empty collection so
                # round-trip preserves the collection itself.
                writer.writerow([name, sort_order, "", "", "", "", "", "", ""])
                continue
            for it in items:
                writer.writerow(
                    [
                        name,
                        sort_order,
                        it.get("kp_id") or "",
                        it.get("imdb_id") or "",
                        it.get("rezka_url") or "",
                        it.get("title") or "",
                        it.get("original_title") or "",
                        it.get("year") or "",
                        it.get("added_at") or "",
                    ]
                )
        from fastapi.responses import Response as _Resp

        return _Resp(
            content=out.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=collections.csv"},
        )
    return JSONResponse(
        {"version": 1, "collections": payload},
        headers={"Content-Disposition": "attachment; filename=collections.json"},
    )


class CollectionsImport(BaseModel):
    # Accept the export shape directly. Either pass {collections: [...]}
    # (the same envelope the /export endpoint emits) or a raw list.
    # `replace` controls whether existing membership is wiped or merged.
    collections: list[dict]
    replace: bool = False


@app.post("/api/collections/import")
def collections_import(data: CollectionsImport):
    return db.import_collections(data.collections, replace=data.replace)


# 8.2 — CSV import is a separate endpoint so the JSON one stays
# strictly typed via pydantic. CSV is parsed on the server (so the
# UI can just upload the raw file) and translated to the same
# `import_collections` call.
@app.post("/api/collections/import_csv")
async def collections_import_csv(request: Request, replace: bool = False):
    body = await request.body()
    try:
        text = body.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = body.decode("latin-1")
    reader = csv.DictReader(io.StringIO(text))
    grouped: dict[str, dict] = {}
    for row in reader:
        name = (row.get("collection_name") or "").strip()
        if not name:
            continue
        sort_order_raw = (row.get("sort_order") or "").strip()
        try:
            sort_order = int(sort_order_raw) if sort_order_raw else 0
        except ValueError:
            sort_order = 0
        coll = grouped.setdefault(name, {"name": name, "sort_order": sort_order, "items": []})
        # An empty marker row (no identifiers, no title) just creates
        # the collection — skip the item part of the row.
        has_any_ref = any(
            (row.get(k) or "").strip() for k in ("kp_id", "imdb_id", "rezka_url", "title")
        )
        if not has_any_ref:
            continue
        year_raw = (row.get("year") or "").strip()
        try:
            year = int(year_raw) if year_raw else None
        except ValueError:
            year = None
        coll["items"].append(
            {
                "kp_id": (row.get("kp_id") or "").strip() or None,
                "imdb_id": (row.get("imdb_id") or "").strip() or None,
                "rezka_url": (row.get("rezka_url") or "").strip() or None,
                "title": (row.get("title") or "").strip() or None,
                "original_title": (row.get("original_title") or "").strip() or None,
                "year": year,
                "added_at": (row.get("added_at") or "").strip() or None,
            }
        )
    return db.import_collections(list(grouped.values()), replace=replace)


class CollectionItemRequest(BaseModel):
    item_id: int


def _sync_rezka_folder(action, collection_id, item_id):
    try:
        from app_core import normalize_title

        if not rezka_session:
            return

        _c = db.get_connection().cursor()
        _c.execute("SELECT name FROM collections WHERE id = ?", (collection_id,))
        _coll = _c.fetchone()
        if not _coll:
            return

        _c.execute("SELECT rezka_url FROM items WHERE id = ?", (item_id,))
        _item = _c.fetchone()
        if not _item or not _item["rezka_url"]:
            return

        import re as _re

        rezka_url = _item["rezka_url"]
        _m = _re.search(
            r"/(?:films|series|cartoons|animation|show|telecasts)/[^/]+/(\d+)-",
            rezka_url,
        )
        if not _m:
            return
        post_id = _m.group(1)

        coll_norm = normalize_title(_coll["name"])
        cat_id = None
        if rezka_session_folders_cache and coll_norm in rezka_session_folders_cache:
            cat_id = rezka_session_folders_cache[coll_norm]
        else:
            _refresh_rezka_folders_cache()
            if rezka_session_folders_cache and coll_norm in rezka_session_folders_cache:
                cat_id = rezka_session_folders_cache[coll_norm]

        if not cat_id:
            return

        data = {"post_id": post_id, "cat_id": cat_id, "action": "add_post"}
        if action == "removed":
            data["del"] = "1"

        # 6.7 — POST through the helper so a stale cookie triggers
        # a single re-login + retry instead of silently dropping
        # the favorite.
        _rezka_request(
            "POST",
            "https://rezka.ag/ajax/favorites/",
            data=data,
            headers={
                "User-Agent": "Mozilla/5.0",
                "X-Requested-With": "XMLHttpRequest",
            },
            cookies=rezka_session.cookies,
            timeout=10,
        )
        _refresh_rezka_folders_cache()
    except Exception as e:
        print(f"[REZKA SYNC ERROR] {e}")
        try:
            _init_rezka_session()
        except Exception:
            pass


def _sync_rezka_folder_wrapper(action, collection_id, item_id):
    try:
        _sync_rezka_folder(action, collection_id, item_id)
    except Exception as e:
        # 6.3 — this runs in a worker thread (run_in_executor), so
        # we cannot call `asyncio.get_event_loop().create_task(...)`
        # from here — that would (a) try to attach to a per-thread
        # loop that doesn't exist, and (b) even if it did, it
        # wouldn't be the FastAPI main loop where ws_manager lives.
        # `_broadcast_threadsafe` does a thread-safe submit onto
        # `_main_loop` captured during lifespan startup.
        _broadcast_threadsafe(
            {
                "type": "rezka_sync_error",
                "message": f"Ошибка синхронизации с Rezka: {e}",
                "item_id": item_id,
                "collection_id": collection_id,
                "action": action,
            }
        )


@app.post("/api/collections/{collection_id}/toggle")
async def toggle_collection_item(collection_id: int, data: CollectionItemRequest):
    action = db.toggle_collection_item(collection_id, data.item_id)

    if action in ("added", "removed") and rezka_session:
        # 6.2 — get_running_loop() inside an async endpoint is the
        # modern, non-deprecated API.
        loop = asyncio.get_running_loop()
        loop.run_in_executor(None, _sync_rezka_folder_wrapper, action, collection_id, data.item_id)

    return {"status": "success", "action": action}


@app.get("/api/item_collections/{item_id}")
def get_item_collections(item_id: int):
    return db.get_item_collections(item_id)


class BatchCollectionsRequest(BaseModel):
    ids: list[int]


@app.post("/api/batch_item_collections")
def batch_item_collections(data: BatchCollectionsRequest):
    result = {}
    for item_id in data.ids:
        result[str(item_id)] = db.get_item_collections(item_id)
    return result


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
            print(f"[REZKA] URL recovered for item {item_id}: {old_url} -> {url}")
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


@app.get("/api/online_sources/{item_id}")
def get_online_sources(item_id: int):
    row = (
        db.get_connection()
        .cursor()
        .execute("SELECT kp_id, imdb_id, title FROM items WHERE id = ?", (item_id,))
        .fetchone()
    )
    if not row:
        return JSONResponse({"error": "not found"}, status_code=404)

    kp_id = row["kp_id"]
    imdb_id = row["imdb_id"]
    if not kp_id and not imdb_id:
        return {"sources": []}

    page_url = f"https://fbdomen.cfd/film/{kp_id}/" if kp_id else ""

    all_players = {}

    def _merge_players(data):
        if not isinstance(data, dict):
            return
        for p in data.get("data", []):
            if not p.get("iframeUrl") or not p.get("type"):
                continue
            key = p["type"].lower()
            if key not in all_players:
                all_players[key] = {
                    "type": p["type"],
                    "iframeUrl": p["iframeUrl"],
                    "translations": p.get("translations") or [],
                }

    def _fetch_kinobox_api(params):
        try:
            from curl_cffi import requests as _cf

            r = _cf.get(
                "https://api.kinobox.tv/api/players",
                params=params,
                impersonate="chrome",
                timeout=10,
            )
            if r.status_code == 200:
                _merge_players(r.json())
                return True
        except Exception:
            pass
        return False

    def _fetch_fbphdplay(params):
        try:
            import requests as _req

            r = _req.get(
                "https://fbphdplay.top/api/players",
                params=params,
                headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
                timeout=10,
            )
            if r.status_code == 200:
                _merge_players(r.json())
        except Exception:
            pass

    params_kp = {"kinopoisk": kp_id} if kp_id else {}
    params_imdb = {"imdb": imdb_id} if imdb_id else {}

    if kp_id:
        _fetch_kinobox_api(params_kp)
        _fetch_fbphdplay(params_kp)

    if imdb_id and not all_players:
        _fetch_kinobox_api(params_imdb)
        _fetch_fbphdplay(params_imdb)

    if not all_players and imdb_id:
        _fetch_fbphdplay({**params_kp, **params_imdb})

    sources = list(all_players.values())
    return {"sources": sources, "pageUrl": page_url}


@app.get("/api/stream_info/{item_id}")
def get_stream_info(item_id: int):
    from HdRezkaApi import HdRezkaApi
    from HdRezkaApi.types import TVSeries

    row = (
        db.get_connection()
        .cursor()
        .execute("SELECT rezka_url FROM items WHERE id = ?", (item_id,))
        .fetchone()
    )
    if not row or not row["rezka_url"]:
        return {"error": "no rezka_url"}

    rezka, _ = _get_rezka_obj(item_id, row["rezka_url"])
    if not rezka:
        return {"error": "failed to load page"}

    try:
        is_series = rezka.type == TVSeries
        result = {
            "type": "series" if is_series else "movie",
            "name": rezka.name,
            "translators": rezka.translators,
        }

        if is_series:
            series_data = {}
            for tid, info in rezka.seriesInfo.items():
                series_data[str(tid)] = {
                    "name": info.get("translator_name", ""),
                    "premium": info.get("premium", False),
                    "seasons": {str(k): v for k, v in info.get("seasons", {}).items()},
                    "episodes": {
                        str(s): {str(e): name for e, name in eps.items()}
                        for s, eps in info.get("episodes", {}).items()
                    },
                }
            result["series_info"] = series_data

        return result
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/stream/{item_id}")
def get_stream(
    item_id: int,
    season: str | None = None,
    episode: str | None = None,
    translator: str | None = None,
):
    row = (
        db.get_connection()
        .cursor()
        .execute("SELECT rezka_url FROM items WHERE id = ?", (item_id,))
        .fetchone()
    )
    if not row or not row["rezka_url"]:
        return {"error": "no rezka_url"}

    rezka, _ = _get_rezka_obj(item_id, row["rezka_url"])
    if not rezka:
        return {"error": "failed to load page"}

    try:
        kwargs = {}
        if translator:
            kwargs["translation"] = translator

        if season and episode:
            stream = rezka.getStream(season, episode, **kwargs)
        else:
            stream = rezka.getStream(**kwargs)

        videos = {}
        for quality, urls in stream.videos.items():
            videos[quality] = urls[0] if urls else None

        subtitles = {}
        if stream.subtitles and stream.subtitles.subtitles:
            for lang, info in stream.subtitles.subtitles.items():
                subtitles[lang] = {
                    "title": info.get("title", lang),
                    "link": info.get("link", ""),
                }

        return {
            "videos": videos,
            "subtitles": subtitles,
            "translator_id": stream.translator_id,
        }
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/stream_m3u/{item_id}")
def get_stream_m3u(
    item_id: int,
    season: str | None = None,
    episode: str | None = None,
    translator: str | None = None,
    quality: str | None = None,
):
    from fastapi.responses import Response as R

    row = (
        db.get_connection()
        .cursor()
        .execute("SELECT rezka_url, title FROM items WHERE id = ?", (item_id,))
        .fetchone()
    )
    if not row or not row["rezka_url"]:
        return R(content="error: no rezka_url", media_type="text/plain", status_code=404)

    rezka, _ = _get_rezka_obj(item_id, row["rezka_url"])
    if not rezka:
        return R(
            content="error: failed to load page",
            media_type="text/plain",
            status_code=500,
        )

    try:
        kwargs = {}
        if translator:
            kwargs["translation"] = translator

        if season and episode:
            stream = rezka.getStream(season, episode, **kwargs)
        else:
            stream = rezka.getStream(**kwargs)

        if not quality or quality not in stream.videos:
            quality = max(
                stream.videos.keys(),
                key=lambda q: {
                    "4K": 7,
                    "2K": 6,
                    "1080p Ultra": 5,
                    "1080p": 4,
                    "720p": 3,
                    "480p": 2,
                    "360p": 1,
                }.get(q, 0),
            )

        url = stream.videos[quality][0] if stream.videos[quality] else None
        if not url:
            return R(content="error: no stream url", media_type="text/plain", status_code=500)

        title = row["title"]
        if season and episode:
            title += f" - S{season}E{episode}"

        safe_title = re.sub(r'[<>:"/\\|?*]', "", title).encode("ascii", "replace").decode("ascii")
        m3u = f"#EXTM3U\n#EXTINF:-1,{title}\n{url}\n"
        return R(
            content=m3u.encode("utf-8"),
            media_type="audio/mpegurl; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{safe_title}.m3u"'},
        )
    except Exception as e:
        return R(content=f"error: {e}", media_type="text/plain", status_code=500)


@app.post("/api/mark_season_seen/{item_id}")
def mark_season_seen(item_id: int):
    row = (
        db.get_connection()
        .cursor()
        .execute("SELECT latest_season, latest_episode FROM items WHERE id = ?", (item_id,))
        .fetchone()
    )
    if not row:
        return {"error": "not found"}
    key = f"rezka_seen_{item_id}"
    value = f"s{row['latest_season']}e{row['latest_episode']}"
    conn = db.get_connection()
    conn.execute("INSERT OR REPLACE INTO app_state (key, value) VALUES (?, ?)", (key, value))
    conn.commit()
    conn.close()
    return {"status": "success"}


class SaveOrderRequest(BaseModel):
    order: list[int]


@app.post("/api/collections/save_order")
def save_collections_order(data: SaveOrderRequest):
    db.save_collections_order(data.order)
    return {"status": "success"}


@app.get("/", response_class=HTMLResponse)
def get_index():
    with open("index.html", encoding="utf-8") as f:
        return f.read()


@app.get("/api/sync_log")
def get_sync_log(log_type: str = "video"):
    log_files = {
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
    filename = log_files.get(log_type)
    if not filename:
        return JSONResponse(
            content={"log": "Неизвестный тип лога", "filename": ""}, status_code=400
        )

    log_content = "Пусто"
    if os.path.exists(filename):
        with open(filename, encoding="utf-8", errors="replace") as f:
            log_content = "".join(f.readlines()[-100:])

    return JSONResponse(
        content={"log": log_content, "filename": filename},
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
    )


@app.get("/api/download_log")
def download_log(log_type: str = "video"):
    log_files = {
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
    filename = log_files.get(log_type)
    if filename and os.path.exists(filename):
        return FileResponse(path=filename, filename=filename, media_type="text/plain")
    return {"error": "Файл не найден"}


@app.post("/api/clear_log")
def clear_log(log_type: str = "video"):
    log_files = {
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
    filename = log_files.get(log_type)
    if not filename:
        return {"status": "error", "message": "Unknown log type"}
    with open(filename, "w", encoding="utf-8") as f:
        f.write("=== Очищено ===\n")
    return {"status": "success"}


@app.post("/api/sync_user")
async def sync_user_data():
    with open("user_sync_log.txt", "w", encoding="utf-8") as f:
        f.write("=== Старт ===\n")
    await task_queue.add_task(run_script, "user", "user_sync.py", "user")
    return {"status": "started"}


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
@app.get("/api/backup/download")
async def backup_download():
    out_dir = "backups"
    os.makedirs(out_dir, exist_ok=True)
    stamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(out_dir, f"app_data-{stamp}.db")
    try:
        # SQLite backup is blocking I/O; push it off the loop.
        size = await asyncio.to_thread(db.backup_to, dest)
    except Exception as e:
        return JSONResponse(
            {"error": f"{type(e).__name__}: {e}"},
            status_code=500,
        )
    print(f"[BACKUP] wrote {dest} ({size} bytes)")
    return FileResponse(
        dest,
        media_type="application/octet-stream",
        filename=os.path.basename(dest),
    )


@app.get("/api/export")
def export_data(fmt: str = "json", category_id: int = -1):
    items = db.export_items(category_id)

    if fmt == "csv":
        output = io.StringIO()
        if items:
            writer = csv.DictWriter(output, fieldnames=items[0].keys())
            writer.writeheader()
            writer.writerows(items)
        from fastapi.responses import Response as _Response

        return _Response(
            content=output.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=export.csv"},
        )

    return JSONResponse(
        content=items,
        headers={"Content-Disposition": "attachment; filename=export.json"},
    )


class SetIdsRequest(BaseModel):
    kp_id: str | None = None
    imdb_id: str | None = None


@app.post("/api/set_ids/{item_id}")
def set_ids(item_id: int, data: SetIdsRequest):
    db.set_ids(item_id, kp_id=data.kp_id, imdb_id=data.imdb_id)
    return {"status": "success"}


# 8.17 — manual rebind of KP/IMDb/Rezka identifiers from the card UI.
# Captures the prior values into audit_log so 8.18 / undo can restore them.
class RebindRequest(BaseModel):
    kp_id: str | None = None
    imdb_id: str | None = None
    rezka_url: str | None = None


@app.post("/api/rebind/{item_id}")
def rebind_item(item_id: int, data: RebindRequest):
    import json as _json

    with db._conn() as c:
        row = c.execute(
            "SELECT kp_id, imdb_id, rezka_url FROM items WHERE id = ?",
            (item_id,),
        ).fetchone()
        if row is None:
            return JSONResponse({"error": "item not found"}, status_code=404)
        before = {
            "kp_id": row["kp_id"],
            "imdb_id": row["imdb_id"],
            "rezka_url": row["rezka_url"],
        }

    after = {**before}
    sets: list[str] = []
    params: list = []
    if data.kp_id is not None:
        after["kp_id"] = data.kp_id.strip() or None
        sets.append("kp_id = ?")
        params.append(after["kp_id"])
        sets.extend(["checked_poiskkino = 0", "checked_tech = 0", "checked_rezka = 0"])
    if data.imdb_id is not None:
        after["imdb_id"] = data.imdb_id.strip() or None
        sets.append("imdb_id = ?")
        params.append(after["imdb_id"])
        sets.append("checked_rezka = 0")
    if data.rezka_url is not None:
        after["rezka_url"] = data.rezka_url.strip() or None
        sets.append("rezka_url = ?")
        params.append(after["rezka_url"])
    if not sets:
        return {"status": "noop"}
    sets.extend(["is_metadata_fixed = 0", "is_reprocessed = 0"])
    params.append(item_id)
    with db._conn() as c:
        c.execute(f"UPDATE items SET {', '.join(sets)} WHERE id = ?", params)

    db.append_audit(
        action="rebind",
        item_id=item_id,
        field="kp_id,imdb_id,rezka_url",
        old_value=_json.dumps(before, ensure_ascii=False),
        new_value=_json.dumps(after, ensure_ascii=False),
    )
    return {"status": "success", "before": before, "after": after}


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
@app.get("/api/trailer/{item_id}")
def get_trailer(item_id: int):
    from tmdb_client import TMDBClient

    with db._conn() as c:
        row = c.execute(
            "SELECT id, imdb_id, tmdb_id, title, original_title, year FROM items WHERE id = ?",
            (item_id,),
        ).fetchone()
        if not row:
            return JSONResponse({"error": "item not found"}, status_code=404)

    client = TMDBClient()
    if not client.api_key:
        return JSONResponse({"error": "TMDB_API_KEY not configured"}, status_code=503)

    tmdb_id = row["tmdb_id"]
    media_type = "movie"
    if not tmdb_id and row["imdb_id"]:
        meta = client.find_by_imdb_id(row["imdb_id"], return_meta=True)
        if meta and meta.get("tmdb_id"):
            tmdb_id = str(meta["tmdb_id"])
            media_type = meta.get("media_type") or "movie"
            with db._conn() as c:
                c.execute(
                    "UPDATE items SET tmdb_id = ? WHERE id = ?",
                    (tmdb_id, item_id),
                )
    if not tmdb_id:
        return JSONResponse(
            {"error": "no TMDB id for this item; set imdb_id first"},
            status_code=404,
        )

    videos = client.get_videos(media_type, tmdb_id) or []

    # Prefer YouTube + Trailer + Official.
    def _score(v: dict) -> int:
        s = 0
        if (v.get("site") or "").lower() == "youtube":
            s += 100
        if (v.get("type") or "").lower() == "trailer":
            s += 50
        if v.get("official"):
            s += 25
        return s

    videos.sort(key=_score, reverse=True)
    # Return up to 5 YouTube candidates so the frontend can fall back
    # to the next one if a video has embedding disabled (YouTube error
    # 101 / 150 / 153). TMDB doesn't expose an embed-allowed flag, so
    # serial fallback is the only reliable workaround.
    youtube_candidates = [
        {
            "youtube_key": v["key"],
            "name": v.get("name") or "",
            "type": v.get("type") or "",
            "official": bool(v.get("official")),
        }
        for v in videos
        if (v.get("site") or "").lower() == "youtube" and v.get("key")
    ][:5]
    if not youtube_candidates:
        return JSONResponse({"error": "no trailer available"}, status_code=404)
    primary = youtube_candidates[0]
    return {
        # Backwards-compatible flat fields (first / best candidate).
        "youtube_key": primary["youtube_key"],
        "name": primary["name"],
        "type": primary["type"],
        "official": primary["official"],
        # New: ranked list for client-side embed fallback.
        "candidates": youtube_candidates,
        "tmdb_id": tmdb_id,
        "media_type": media_type,
    }


# 8.12 — same lookup as /api/stream_m3u but returns the resolved
# direct URL as JSON so the frontend can render an embedded player
# (HTML5 <video> / hls.js) instead of triggering an m3u download.
@app.get("/api/stream_url/{item_id}")
def get_stream_url(
    item_id: int,
    season: str | None = None,
    episode: str | None = None,
    translator: str | None = None,
    quality: str | None = None,
):
    with db._conn() as c:
        row = c.execute("SELECT rezka_url, title FROM items WHERE id = ?", (item_id,)).fetchone()
    if not row or not row["rezka_url"]:
        return JSONResponse({"error": "no rezka_url"}, status_code=404)

    rezka, _ = _get_rezka_obj(item_id, row["rezka_url"])
    if not rezka:
        return JSONResponse({"error": "failed to load page"}, status_code=502)
    try:
        kwargs: dict = {}
        if translator:
            kwargs["translation"] = translator
        if season and episode:
            stream = rezka.getStream(season, episode, **kwargs)
        else:
            stream = rezka.getStream(**kwargs)

        if not quality or quality not in stream.videos:
            quality = max(
                stream.videos.keys(),
                key=lambda q: {
                    "4K": 7,
                    "2K": 6,
                    "1080p Ultra": 5,
                    "1080p": 4,
                    "720p": 3,
                    "480p": 2,
                    "360p": 1,
                }.get(q, 0),
            )
        url = stream.videos[quality][0] if stream.videos[quality] else None
        if not url:
            return JSONResponse({"error": "no stream url"}, status_code=502)
        is_hls = ".m3u8" in url.lower()
        return {
            "url": url,
            "quality": quality,
            "title": row["title"],
            "is_hls": is_hls,
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=502)


# 8.12 — subtitle CORS proxy. Rezka serves VTT/SRT files without
# Access-Control-Allow-Origin, so the browser refuses to attach them
# to a <track> element. We re-fetch and stream the body back with
# permissive CORS so HTML5 captions render. Only forwards http(s)
# URLs whose host belongs to a small allow-list (rezka mirrors).
_SUBTITLE_HOST_ALLOWLIST = (
    "rezka.ag",
    "hdrezka",
    "rezka.cdnstream",
    "voidboost",
    "videocdn",
)


@app.get("/api/subtitle_proxy")
def subtitle_proxy(url: str):
    from urllib.parse import urlparse

    from fastapi.responses import Response

    if not url or not url.startswith(("http://", "https://")):
        return JSONResponse({"error": "invalid url"}, status_code=400)
    host = (urlparse(url).hostname or "").lower()
    if not any(host.endswith(h) or h in host for h in _SUBTITLE_HOST_ALLOWLIST):
        return JSONResponse({"error": f"host {host} not allowed"}, status_code=403)
    try:
        # Subtitle files live on CDNs (no auth required) — use a plain
        # requests.get rather than `_rezka_request`, which would no-op
        # when the user hasn't configured Rezka credentials.
        import requests as _req

        resp = _req.get(url, timeout=10)
        if resp.status_code != 200:
            return JSONResponse(
                {"error": f"upstream HTTP {resp.status_code}"},
                status_code=502,
            )
        # Pick a Content-Type that the browser will accept for <track>.
        ct = resp.headers.get("Content-Type") or ""
        if "vtt" in ct.lower() or url.lower().endswith(".vtt"):
            ct = "text/vtt; charset=utf-8"
        else:
            # Most rezka subtitles are SRT — convert lazily so the
            # browser <track> element accepts them as captions.
            ct = "text/plain; charset=utf-8"
        body = resp.content
        # If the file is SRT (common on rezka), do a quick conversion
        # to WebVTT — browsers only render captions in VTT format.
        if b"WEBVTT" not in body[:64] and (url.lower().endswith(".srt") or b"-->" in body[:512]):
            try:
                txt = body.decode("utf-8-sig", errors="replace")
                txt = txt.replace("\r\n", "\n")
                # SRT timestamps use comma; VTT uses dot.
                import re as _re

                txt = _re.sub(
                    r"(\d{2}:\d{2}:\d{2}),(\d{3})",
                    r"\1.\2",
                    txt,
                )
                body = ("WEBVTT\n\n" + txt).encode("utf-8")
                ct = "text/vtt; charset=utf-8"
            except Exception:
                pass
        return Response(
            content=body,
            media_type=ct,
            headers={
                "Access-Control-Allow-Origin": "*",
                "Cache-Control": "public, max-age=3600",
            },
        )
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=502)


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


@app.post("/api/self_update")
def self_update():
    import subprocess

    try:
        result = subprocess.run(
            ["git", "pull"],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=os.path.dirname(os.path.abspath(__file__)),
        )
        output = result.stdout.strip()
        if result.returncode != 0:
            return {"status": "error", "message": result.stderr.strip()[:500]}
        if "Already up to date" in output:
            return {"status": "up_to_date", "message": output}
        subprocess.Popen(
            ["systemctl", "restart", "parsclode"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return {"status": "updated", "message": output}
    except Exception as e:
        return {"status": "error", "message": str(e)[:500]}


@app.post("/api/reset_database")
def reset_database():
    import subprocess

    db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app_data.db")
    if not os.path.exists(db_path):
        return {"status": "error", "message": "Database file not found"}
    os.remove(db_path)
    subprocess.Popen(
        ["systemctl", "restart", "parsclode"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return {"status": "success", "message": "Database deleted, server restarting"}


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False, loop="asyncio")
