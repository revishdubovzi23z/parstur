import asyncio
import hashlib
import json
import os
import sys
import time
from contextlib import asynccontextmanager

import uvicorn
from fastapi import (
    FastAPI,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from db import db
from logging_config import setup_logging
from routes import admin, auth, collections, feed, items, kinopub, process, streams
from routes.auth import (
    _auth_enabled,
    _check_auth,
    _check_token,
    _ws_tickets,
)
from runtime import rezka as _rezka
from runtime.processes import (
    _read_progress,
    process_status,
    running_processes,
    task_queue,
)
from runtime.ws import set_main_loop, ws_manager
from script_utils import load_config
from settings import settings

logger = setup_logging("parsclode.main", settings.log_file_path)

# Force the ProactorEventLoop on Windows so asyncio can spawn
# subprocesses (the SelectorEventLoop does not support them).
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())


# 6.1 — replaces the deprecated @app.on_event("startup"/"shutdown")
# decorators with a single lifespan async context manager. The body
# above the `yield` is the startup phase; the body below `yield` is
# shutdown.
@asynccontextmanager
async def lifespan(_app):
    # ── startup ────────────────────────────────────────────────────
    logger.info("[SERVER] Startup event triggered")
    # 6.3 — capture the running loop so threads spawned via
    # run_in_executor can schedule coroutines back on it via
    # asyncio.run_coroutine_threadsafe.
    set_main_loop(asyncio.get_running_loop())
    # Make sure the DB schema is up-to-date on every boot. Both
    # init_schema (CREATE TABLE IF NOT EXISTS) and check_and_migrate_schema
    # are idempotent; together they cover both fresh databases and
    # installs whose schema predates a recently-added migration
    # (e.g. filter_rules / audit_log added in 0002, tmdb_id in 0003).
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
    set_main_loop(None)


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
app.include_router(kinopub.router)

# ROADMAP Stage 10.7z — the Vite/Vue 3/TS SPA in `frontend/dist` is now
# THE frontend; the legacy CDN-driven `index.html` at the repo root is
# gone.
#
# Layout:
#   * `GET /` is an explicit route (defined further down) that reads
#     `frontend/dist/index.html` off disk. When the dist directory is
#     missing (fresh checkout / nobody ran `npm run build`), we return
#     a 503 with a clear "please build the frontend" page instead of
#     a bare 404.
#   * `frontend/dist/assets/` is mounted at `/assets` at the bottom of
#     this file. We deliberately do NOT mount StaticFiles at `/`:
#     Starlette routes are evaluated in registration order, and a
#     root-mount would eat `/manifest.json`, `/sw.js`, `/health`,
#     `/api/...` etc. before they reached their explicit handlers if
#     anyone ever reordered things.
_FRONTEND_DIST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontend", "dist")
if not os.path.isdir(_FRONTEND_DIST):
    logger.warning(
        "[FRONTEND] %s missing — `/` will serve a build-instructions page "
        "until you run `npm run build` in frontend/.",
        _FRONTEND_DIST,
    )

# Plain-text HTML, intentionally inline (no template engine) so the
# fallback page works even when the dev hasn't installed Jinja2 or
# materialised any static assets. Styled with system-stack typography
# so it's readable without any external CSS.
_FRONTEND_NOT_BUILT_HTML = """\
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>par2 — frontend not built</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI",
           Roboto, Oxygen, Ubuntu, sans-serif; max-width: 720px; margin: 4rem auto;
           padding: 0 1.5rem; color: #1f2937; line-height: 1.5; }
    h1   { font-size: 1.5rem; margin-bottom: 0.5rem; }
    code { background: #f3f4f6; padding: 0.15rem 0.4rem;
           border-radius: 3px; font-family: ui-monospace, SFMono-Regular,
           Menlo, monospace; font-size: 0.95em; }
    pre  { background: #f3f4f6; padding: 0.75rem 1rem; border-radius: 4px;
           overflow-x: auto; }
    .note { color: #6b7280; font-size: 0.9rem; margin-top: 1.5rem; }
  </style>
</head>
<body>
  <h1>Frontend bundle is not built yet.</h1>
  <p>The backend is running fine, but the Vite SPA in
     <code>frontend/dist</code> hasn't been produced. Build it once:</p>
  <pre>cd frontend
npm install
npm run build</pre>
  <p>Then refresh this page.</p>
  <p class="note">If you're developing the frontend, run
     <code>npm run dev</code> instead and open the Vite dev server URL.</p>
</body>
</html>
"""


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket, token: str | None = None, ticket: str | None = None):
    # WebSocket upgrades bypass HTTP middleware, so we must enforce auth here
    # ourselves when it is enabled.
    # 5.10: Support both ?token=... (legacy) and ?ticket=... (preferred).
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
                "rezka_session": _rezka.rezka_session_state,
            }
        )
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(ws)
    except Exception:
        ws_manager.disconnect(ws)


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
#   * ROADMAP 10.7z — the frontend is a Vite-built ESM bundle served from
#     `frontend/dist`, so script-src is just 'self'. No more public CDNs,
#     no more 'unsafe-eval' (Vue runtime no longer compiles templates in
#     the browser — SFCs are pre-compiled by `@vitejs/plugin-vue`).
#   * style-src keeps 'unsafe-inline' for now: Vue's `:style` bindings
#     emit inline `style="..."` attributes (e.g. SyncPanel's progress
#     bars), and the Tailwind utility classes that ship inside the
#     bundle are plain CSS — the inline allowance covers the bindings.
#   * Posters come from arbitrary external hosts (TMDB, Kinopoisk, Rezka)
#     so img-src has to allow https: and data:.
#   * connect-src has to allow ws:/wss: for the /ws endpoint, plus any
#     same-origin XHRs.
#   * frame-ancestors 'none' replicates X-Frame-Options: DENY in modern
#     browsers; we send both for compatibility.
_CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
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
        "/favicon.png",
        "/icon-192.png",
        "/icon-512.png",
        "/sw.js",
    ):
        return await call_next(request)
    # ROADMAP 10.7z — `/assets/...` is now the SPA's hashed bundle, which
    # must load BEFORE login (otherwise the user can't see the login
    # form). Auth-gate only the API surface here; static assets are
    # public by construction.
    if path.startswith("/api/"):
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
    success = await asyncio.to_thread(_rezka._init_rezka_session)
    if success:
        return

    wait = 300  # 5 minutes
    while True:
        try:
            await asyncio.sleep(wait)
            success = await asyncio.to_thread(_rezka._init_rezka_session)
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
    """Periodically purge expired session rows and in-memory WS tickets.

    The sessions table itself is self-cleaning on every lookup (an
    expired row is deleted on first sight), but a token that was
    issued and then never used will sit in the table forever
    without this loop. The same goes for ws_tickets in memory.
    """
    while True:
        await asyncio.sleep(SESSION_GC_INTERVAL_SECONDS)
        try:
            now = time.time()
            reaped = await asyncio.to_thread(db.session_purge_expired, now=now)
            if reaped:
                logger.info(f"[AUTH] GC reaped {reaped} expired sessions")

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


@app.get("/favicon.png")
def get_favicon():
    return FileResponse(os.path.join(_FRONTEND_DIST, "favicon.png"))


@app.get("/icon-192.png")
def get_icon192():
    return FileResponse(os.path.join(_FRONTEND_DIST, "icon-192.png"))


@app.get("/icon-512.png")
def get_icon512():
    return FileResponse(os.path.join(_FRONTEND_DIST, "icon-512.png"))


def _sw_version() -> str:
    """Build a cache-bust token for the service worker.

    Hashes (mtime, size) of the SPA entry point + sw.js + manifest.json
    so any deploy-time change to those files yields a new SW body and
    the browser re-installs the worker (which clears prior caches in
    the activate handler — see sw.js).

    ROADMAP 10.7z: the legacy `index.html` at the repo root is gone;
    the version key now hashes `frontend/dist/index.html`, which Vite
    rewrites on every build (asset URLs change on every content-hash).
    """
    parts: list[str] = []
    spa_html = os.path.join(_FRONTEND_DIST, "index.html")
    for fname in (spa_html, "sw.js", "manifest.json"):
        try:
            st = os.stat(fname)
            parts.append(f"{fname}:{int(st.st_mtime)}:{st.st_size}")
        except OSError:
            parts.append(f"{fname}:missing")
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:12]


def _sw_precache_urls() -> list[str]:
    """ROADMAP 10.7z — list the URLs the SW should precache on install.

    We always include `/` (the SPA shell) plus the static metadata
    files, then enumerate everything Vite emitted into
    `frontend/dist/assets/` so the first-paint bundle is fully
    available offline after the SW activates. Content-hashed filenames
    mean the list rotates whenever the bundle changes, which combines
    with `_sw_version()` to invalidate the previous cache cleanly.
    """
    urls = ["/", "/manifest.json", "/favicon.png", "/icon-192.png", "/icon-512.png"]
    assets_dir = os.path.join(_FRONTEND_DIST, "assets")
    if os.path.isdir(assets_dir):
        for fname in sorted(os.listdir(assets_dir)):
            urls.append(f"/assets/{fname}")
    return urls


@app.get("/sw.js")
def get_sw():
    # 7.1 — substitute __SW_VERSION__ + __SW_PRECACHE__ at request time
    # so each deploy produces a different sw.js body (different bytes
    # -> browser reinstalls the worker -> activate handler purges old
    # caches).
    try:
        with open("sw.js", encoding="utf-8") as f:
            body = f.read()
    except OSError:
        return JSONResponse({"error": "sw.js missing"}, status_code=500)
    body = body.replace("__SW_VERSION__", _sw_version())
    body = body.replace("__SW_PRECACHE__", json.dumps(_sw_precache_urls()))
    return HTMLResponse(
        content=body,
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache, max-age=0"},
    )


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


# ROADMAP Stage 10.7z — mount the Vite-emitted assets directory at
# `/assets`. We deliberately mount the asset subdirectory (not the
# whole dist root) so the mount doesn't intercept `/`, `/manifest.json`,
# `/sw.js`, etc. The SPA shell itself is served by the explicit
# `GET /` handler below.
_FRONTEND_ASSETS = os.path.join(_FRONTEND_DIST, "assets")
if os.path.isdir(_FRONTEND_ASSETS):
    app.mount(
        "/assets",
        StaticFiles(directory=_FRONTEND_ASSETS),
        name="spa-assets",
    )
    logger.info(f"[FRONTEND] mounted SPA assets from {_FRONTEND_ASSETS} at /assets")


@app.get("/", response_class=HTMLResponse)
def get_spa_shell():
    """Serve the Vite-built SPA shell.

    When `frontend/dist/index.html` is missing (fresh checkout, nobody
    ran `npm run build` yet) we return a 503 with an inline
    instructions page instead of a bare 404 — the bare 404 was
    confusing for new contributors who'd just cloned the repo and
    started `uvicorn` without realising the SPA needs a build step.
    """
    spa_html = os.path.join(_FRONTEND_DIST, "index.html")
    try:
        with open(spa_html, encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except FileNotFoundError:
        return HTMLResponse(content=_FRONTEND_NOT_BUILT_HTML, status_code=503)


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False, loop="asyncio")
