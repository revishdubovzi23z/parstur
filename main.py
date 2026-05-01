from datetime import datetime
from typing import Optional
import json
import re
import uvicorn
import asyncio
import os
import subprocess
import sys
import signal
import secrets
import hashlib
from contextlib import asynccontextmanager
from fastapi import (
    FastAPI,
    Request,
    HTTPException,
    Depends,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel
from app_core import RUTOR_CATEGORIES, VIDEO_CATEGORY_IDS
from script_utils import load_config, clear_stop_flag, clear_checkpoint
from db import db
from dotenv import load_dotenv

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
_auth_enabled = bool(AUTH_USER and AUTH_PASS)
_session_tokens: dict[str, float] = {}


def _check_auth(request: Request) -> bool:
    if not _auth_enabled:
        return True
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:]
        if token in _session_tokens:
            return True
    return False


async def require_auth(request: Request):
    if not _auth_enabled:
        return
    if _check_auth(request):
        return
    raise HTTPException(
        status_code=401, detail="Unauthorized", headers={"WWW-Authenticate": "Basic"}
    )


app = FastAPI(title="Tracker Filter")


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
        to_remove = []
        for ws in self.active:
            try:
                await ws.send_json(message)
            except Exception:
                to_remove.append(ws)
        for ws in to_remove:
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


def _refresh_rezka_folders_cache():
    global rezka_session_folders_cache
    if not rezka_session:
        return
    try:
        import re as _re
        import requests as _req
        from bs4 import BeautifulSoup as _BS
        from app_core import normalize_title

        resp = _req.get(
            "https://rezka.ag/favorites/",
            headers={"User-Agent": "Mozilla/5.0"},
            cookies=rezka_session.cookies,
            timeout=15,
        )
        soup = _BS(resp.content, "html.parser")
        sidebar = soup.find(
            "div", class_="b-favorites_content__sidebarbar"
        ) or soup.find("div", class_="b-favorites_content__sidebar")
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
async def websocket_endpoint(ws: WebSocket):
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
    p_file = f"progress_{key}.json"
    if os.path.exists(p_file):
        try:
            with open(p_file, "r") as f:
                return json.load(f)
        except Exception:
            return {"current": 0, "total": 0}
    return {"current": 0, "total": 0}


@app.post("/api/login")
async def login(request: Request):
    if not _auth_enabled:
        return {"token": "none", "auth_enabled": False}
    body = await request.json()
    user = body.get("username", "")
    password = body.get("password", "")
    if user == AUTH_USER and password == AUTH_PASS:
        token = secrets.token_hex(32)
        _session_tokens[token] = True
        return {"token": token, "auth_enabled": True}
    raise HTTPException(status_code=401, detail="Invalid credentials")


@app.get("/api/auth_status")
async def auth_status():
    return {"auth_enabled": _auth_enabled}


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    if not _auth_enabled:
        return await call_next(request)
    path = request.url.path
    if path in (
        "/api/login",
        "/api/auth_status",
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


@app.on_event("startup")
async def startup_event():
    print("[SERVER] Startup event triggered")
    task_queue.start()
    try:
        db.ensure_fts_indexed()
    except Exception as e:
        print(f"[FTS5] Index init skipped: {e}")
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _init_rezka_session)


@app.on_event("shutdown")
async def shutdown_event():
    print("[SERVER] Shutdown event triggered")
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


@app.get("/api/debug/queue")
async def debug_queue():
    """Отладочный эндпоинт для проверки состояния очереди."""
    loop = asyncio.get_event_loop()
    return {
        "loop_type": str(type(loop)),
        "queue_size": task_queue.queue.qsize(),
        "worker_active": task_queue.worker_task is not None
        and not task_queue.worker_task.done(),
        "process_status": process_status,
    }


@app.post("/api/rebuild_fts")
def rebuild_fts():
    count = db.rebuild_fts()
    return {"status": "ok", "indexed": count}


@app.get("/manifest.json")
def get_manifest():
    return FileResponse("manifest.json")


@app.get("/sw.js")
def get_sw():
    return FileResponse("sw.js")


@app.get("/icon.png")
def get_icon():
    if os.path.exists("icon.png"):
        return FileResponse("icon.png")
    return FileResponse("static/icon.png")


# Состояние процессов: храним реальные объекты Popen
running_processes = {
    "sync_video": None,
    "sync_other": None,
    "fix": None,
    "reprocess": None,
    "user": None,
    "cleanup": None,
    "rezka": None,
    "full_pipeline": None,
    "single_update": None,
    "active_pipeline_proc": None,
    "active_pipeline_key": None,
}
process_status = {
    "sync_video": "idle",
    "sync_other": "idle",
    "fix": "idle",
    "poiskkino": "idle",
    "reprocess": "idle",
    "user": "idle",
    "cleanup": "idle",
    "rezka": "idle",
    "rezka_collections": "idle",
    "full_pipeline": "idle",
    "single_update": "idle",
}
pipeline_stop_requested = False


async def run_script(script_name, status_key):
    global process_status, running_processes
    process_status[status_key] = "running"
    await ws_manager.broadcast(
        {"type": "status", "key": status_key, "value": "running"}
    )

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
        proc = await asyncio.create_subprocess_exec(
            sys.executable, script_path, env=env
        )
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
                print(
                    f"Шаг {key} завершился со статусом {process_status[key]}. Прерываю цикл."
                )
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
        f.write(
            f"=== Запуск парсинга ВИДЕО ({datetime.now().strftime('%H:%M:%S')}) ===\n"
        )
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
        f.write(
            f"=== Запуск парсинга ИГР и СОФТА ({datetime.now().strftime('%H:%M:%S')}) ===\n"
        )
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
    await ws_manager.broadcast(
        {"type": "status", "key": status_key, "value": "running"}
    )

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

                    now = asyncio.get_event_loop().time()
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
                with open(progress_file, "r") as f:
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
        f.write(
            f"=== Запуск синхронизации REZKA ({datetime.now().strftime('%H:%M:%S')}) ===\n"
        )

    await task_queue.add_task(
        run_script_with_args, "rezka", "rezka_sync.py", [], "rezka", log_file
    )
    return {"status": "started"}


@app.post("/api/start_cleanup")
async def start_cleanup():
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


@app.post("/api/start_rezka_collections")
async def start_rezka_collections():
    if check_any_running():
        raise HTTPException(400, "Другой процесс уже запущен")
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

    config = load_config()
    graceful_timeout = config.get("shutdown", {}).get("graceful_timeout", 5)

    if key == "full_pipeline":
        pipeline_stop_requested = True
        active_key = running_processes.get("active_pipeline_key")
        if active_key:
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
        f.write(
            f"=== Поиск (PoiskKino API) {datetime.now().strftime('%H:%M:%S')} ===\n"
        )
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
        f.write(
            f"=== Полное обновление базы {datetime.now().strftime('%H:%M:%S')} ===\n"
        )

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
                with open(p_file, "r") as f:
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


@app.post("/api/collections")
def create_collection(data: CollectionCreate):
    try:
        db.create_collection(data.name)
    except Exception:
        return {"status": "error", "message": "Коллекция существует"}
    return {"status": "success"}


@app.delete("/api/collections/{id}")
def delete_collection(id: int):
    db.delete_collection(id)
    return {"status": "success"}


class CollectionItemRequest(BaseModel):
    item_id: int


def _sync_rezka_folder(action, collection_id, item_id):
    try:
        import requests as _req
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

        _req.post(
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
        import asyncio as _a

        try:
            _a.get_event_loop().create_task(
                ws_manager.broadcast(
                    {
                        "type": "rezka_sync_error",
                        "message": f"Ошибка синхронизации с Rezka: {e}",
                        "item_id": item_id,
                        "collection_id": collection_id,
                        "action": action,
                    }
                )
            )
        except Exception:
            pass


@app.post("/api/collections/{collection_id}/toggle")
async def toggle_collection_item(collection_id: int, data: CollectionItemRequest):
    action = db.toggle_collection_item(collection_id, data.item_id)

    if action in ("added", "removed") and rezka_session:
        loop = asyncio.get_event_loop()
        loop.run_in_executor(
            None, _sync_rezka_folder_wrapper, action, collection_id, data.item_id
        )

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

    try:
        cookies = rezka_session.cookies if rezka_session else {"hdmbbs": "1"}
        rezka = HdRezkaApi(row["rezka_url"], cookies=cookies)
        if not rezka.ok:
            return {"error": "failed to load page"}

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
    season: Optional[str] = None,
    episode: Optional[str] = None,
    translator: Optional[str] = None,
):
    from HdRezkaApi import HdRezkaApi

    row = (
        db.get_connection()
        .cursor()
        .execute("SELECT rezka_url FROM items WHERE id = ?", (item_id,))
        .fetchone()
    )
    if not row or not row["rezka_url"]:
        return {"error": "no rezka_url"}

    try:
        cookies = rezka_session.cookies if rezka_session else {"hdmbbs": "1"}
        rezka = HdRezkaApi(row["rezka_url"], cookies=cookies)
        if not rezka.ok:
            return {"error": "failed to load page"}

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
    season: Optional[str] = None,
    episode: Optional[str] = None,
    translator: Optional[str] = None,
    quality: Optional[str] = None,
):
    from HdRezkaApi import HdRezkaApi
    from fastapi.responses import Response as R

    row = (
        db.get_connection()
        .cursor()
        .execute("SELECT rezka_url, title FROM items WHERE id = ?", (item_id,))
        .fetchone()
    )
    if not row or not row["rezka_url"]:
        return R(
            content="error: no rezka_url", media_type="text/plain", status_code=404
        )

    try:
        cookies = rezka_session.cookies if rezka_session else {"hdmbbs": "1"}
        rezka = HdRezkaApi(row["rezka_url"], cookies=cookies)
        if not rezka.ok:
            return R(
                content="error: failed to load page",
                media_type="text/plain",
                status_code=500,
            )

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
            return R(
                content="error: no stream url", media_type="text/plain", status_code=500
            )

        title = row["title"]
        if season and episode:
            title += f" - S{season}E{episode}"

        safe_title = (
            re.sub(r'[<>:"/\\|?*]', "", title)
            .encode("ascii", "replace")
            .decode("ascii")
        )
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
        .execute(
            "SELECT latest_season, latest_episode FROM items WHERE id = ?", (item_id,)
        )
        .fetchone()
    )
    if not row:
        return {"error": "not found"}
    key = f"rezka_seen_{item_id}"
    value = f"s{row['latest_season']}e{row['latest_episode']}"
    conn = db.get_connection()
    conn.execute(
        "INSERT OR REPLACE INTO app_state (key, value) VALUES (?, ?)", (key, value)
    )
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
    with open("index.html", "r", encoding="utf-8") as f:
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

    from fastapi.responses import JSONResponse

    log_content = "Пусто"
    if os.path.exists(filename):
        with open(filename, "r", encoding="utf-8", errors="replace") as f:
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


import csv
import io


@app.get("/api/export")
def export_data(fmt: str = "json", category_id: int = -1):
    items = db.export_items(category_id)

    if fmt == "csv":
        output = io.StringIO()
        if items:
            writer = csv.DictWriter(output, fieldnames=items[0].keys())
            writer.writeheader()
            writer.writerows(items)
        from fastapi.responses import Response

        return Response(
            content=output.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=export.csv"},
        )

    from fastapi.responses import JSONResponse

    return JSONResponse(
        content=items,
        headers={"Content-Disposition": "attachment; filename=export.json"},
    )


class SetIdsRequest(BaseModel):
    kp_id: Optional[str] = None
    imdb_id: Optional[str] = None


@app.post("/api/set_ids/{item_id}")
def set_ids(item_id: int, data: SetIdsRequest):
    db.set_ids(item_id, kp_id=data.kp_id, imdb_id=data.imdb_id)
    return {"status": "success"}


@app.post("/api/mark_visited")
def mark_visited():
    now = db.mark_visited()
    return {"status": "success", "last_visit": now}


@app.get("/api/last_visit")
def get_last_visit():
    return {"last_visit": db.get_last_visit()}


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False, loop="asyncio")
