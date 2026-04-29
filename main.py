from datetime import datetime
import json
import sqlite3
import uvicorn
import unicodedata
import asyncio
import os
import subprocess
import sys
import signal
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel
from app_core import RUTOR_CATEGORIES, VIDEO_CATEGORY_IDS
from script_utils import load_config, clear_stop_flag, clear_checkpoint

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

app = FastAPI(title="Tracker Filter")


@app.on_event("startup")
async def startup_event():
    print("[SERVER] Startup event triggered")
    task_queue.start()


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
    "reprocess": "idle",
    "user": "idle",
    "cleanup": "idle",
    "rezka": "idle",
    "full_pipeline": "idle",
    "single_update": "idle",
}
pipeline_stop_requested = False


async def run_script(script_name, status_key):
    global process_status, running_processes
    process_status[status_key] = "running"

    progress_file = f"progress_{status_key}.json"
    if os.path.exists(progress_file):
        try:
            os.remove(progress_file)
        except:
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

    start_time = datetime.now()

    # Очищаем старый файл прогресса
    progress_file = f"progress_{status_key}.json"
    if os.path.exists(progress_file):
        try:
            os.remove(progress_file)
        except:
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
                    # Читаем небольшими порциями, чтобы не ждать новой строки
                    chunk = await proc.stdout.read(1024)
                    if not chunk:
                        break

                    decoded_chunk = chunk.decode("utf-8", errors="replace")
                    f.write(decoded_chunk)
                    f.flush()
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
            except:
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
            except:
                pass

        try:
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO job_history (job_type, start_time, end_time, duration, items_processed, total_items, status)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    status_key,
                    start_time.strftime("%Y-%m-%d %H:%M:%S"),
                    end_time.strftime("%Y-%m-%d %H:%M:%S"),
                    duration,
                    items_processed,
                    total_items,
                    status,
                ),
            )
            conn.commit()
            conn.close()
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


@app.post("/api/reprocess_item/{item_id}")
async def reprocess_item(item_id: int):
    # Очищаем лог перед запуском
    log_file = "single_update_log.txt"
    with open(log_file, "w", encoding="utf-8") as f:
        f.write(
            f"=== Обновление метаданных карточки ID {item_id} ({datetime.now().strftime('%H:%M:%S')}) ===\n"
        )

    args = ["--force", "--id", str(item_id)]
    await task_queue.add_task(
        run_script_with_args,
        "single_update",
        "reprocess_database.py",
        args,
        "single_update",
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
            except:
                progress[key] = {"current": 0, "total": 0}
        else:
            progress[key] = {"current": 0, "total": 0}

    return {"statuses": process_status, "progress": progress}


def get_db():
    conn = sqlite3.connect("app_data.db", timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.row_factory = sqlite3.Row

    def py_lower(x):
        if x is None:
            return None
        return unicodedata.normalize("NFC", str(x)).lower().replace("x", "х").strip()

    conn.create_function("py_lower", 1, py_lower)
    return conn


def get_watched_item_ids(cursor):
    cursor.execute(
        "SELECT imdb_id, kp_id, title_norm, original_title_norm, item_year FROM user_ratings"
    )
    ratings = cursor.fetchall()
    if not ratings:
        return set()
    rated_imdb_ids = set()
    rated_kp_ids = set()
    rated_names = {}
    for imdb_id, kp_id, title_norm, orig_norm, item_year in ratings:
        if imdb_id:
            rated_imdb_ids.add(imdb_id)
        if kp_id:
            rated_kp_ids.add(kp_id)
        for name in [title_norm, orig_norm]:
            if name:
                if name not in rated_names:
                    rated_names[name] = []
                rated_names[name].append(item_year)
    watched_ids = set()
    if rated_imdb_ids:
        placeholders = ",".join("?" * len(rated_imdb_ids))
        cursor.execute(
            f"SELECT id FROM items WHERE imdb_id IN ({placeholders})",
            list(rated_imdb_ids),
        )
        for row in cursor.fetchall():
            watched_ids.add(row[0])
    if rated_kp_ids:
        placeholders = ",".join("?" * len(rated_kp_ids))
        cursor.execute(
            f"SELECT id FROM items WHERE kp_id IN ({placeholders})", list(rated_kp_ids)
        )
        for row in cursor.fetchall():
            watched_ids.add(row[0])
    if rated_names:
        name_list = list(rated_names.keys())
        chunk_size = 900
        for i in range(0, len(name_list), chunk_size):
            chunk = name_list[i : i + chunk_size]
            placeholders = ",".join("?" * len(chunk))
            cursor.execute(
                f"SELECT sn.item_id FROM item_search_names sn WHERE sn.name_norm IN ({placeholders})",
                chunk,
            )
            for row in cursor.fetchall():
                watched_ids.add(row[0])
    return watched_ids


@app.get("/api/categories")
def get_categories(hide_rated: bool = False, hide_collected: bool = False):
    conn = get_db()
    cursor = conn.cursor()
    watched_ids = get_watched_item_ids(cursor) if hide_rated else set()

    def make_filters(alias="i"):
        clauses = []
        if watched_ids:
            ids_str = ",".join(map(str, watched_ids))
            clauses.append(f"{alias}.id NOT IN ({ids_str})")
        if hide_collected:
            clauses.append(f"{alias}.id NOT IN (SELECT item_id FROM collection_items)")
        return " AND " + " AND ".join(clauses) if clauses else ""

    not_in = make_filters("i")
    cursor.execute(
        f"SELECT c.id, c.name, (SELECT COUNT(*) FROM items i WHERE i.category_id = c.id AND i.is_ignored = 0 {not_in}) as count FROM categories c ORDER BY c.name"
    )
    cats = [dict(row) for row in cursor.fetchall()]
    ids_str_cats = ",".join(map(str, VIDEO_CATEGORY_IDS))
    cursor.execute(
        f"SELECT COUNT(*) FROM items i WHERE category_id IN ({ids_str_cats}) AND is_ignored = 0 {not_in}"
    )
    count_video = cursor.fetchone()[0]
    cursor.execute(f"SELECT COUNT(*) FROM items i WHERE is_ignored = 0 {not_in}")
    count_any = cursor.fetchone()[0]
    cursor.execute(f"SELECT COUNT(*) FROM items i WHERE is_ignored = 1 {not_in}")
    count_ignored = cursor.fetchone()[0]
    cursor.execute(
        f"SELECT COUNT(*) FROM items i WHERE i.category_id IN ({ids_str_cats}) AND i.is_ignored = 0 AND (i.poster_url IS NULL OR i.poster_url = '') {not_in}"
    )
    no_poster_count = cursor.fetchone()[0]
    cursor.execute(
        f"SELECT COUNT(*) FROM items i WHERE i.category_id IN ({ids_str_cats}) AND i.is_ignored = 0 AND (i.kp_rating = 0 OR i.kp_rating IS NULL OR i.imdb_rating = 0 OR i.imdb_rating IS NULL) {not_in}"
    )
    no_ratings_count = cursor.fetchone()[0]
    cursor.execute(
        f"SELECT COUNT(*) FROM items i WHERE i.category_id IN ({ids_str_cats}) AND i.is_ignored = 0 AND (i.kp_id IS NULL OR i.kp_id = '') {not_in}"
    )
    no_kp_id_count = cursor.fetchone()[0]
    cursor.execute(
        f"SELECT COUNT(*) FROM items i WHERE i.category_id IN ({ids_str_cats}) AND i.is_ignored = 0 AND (i.imdb_id IS NULL OR i.imdb_id = '') {not_in}"
    )
    no_imdb_id_count = cursor.fetchone()[0]
    cursor.execute(
        f"SELECT COUNT(*) FROM items i WHERE i.category_id IN ({ids_str_cats}) AND i.is_ignored = 0 AND (i.kp_id IS NULL OR i.kp_id = '') AND (i.imdb_id IS NULL OR i.imdb_id = '') {not_in}"
    )
    no_any_id_count = cursor.fetchone()[0]
    conn.close()
    result = [
        {"id": -1, "name": "Все видео", "count": count_video},
        {"id": -100, "name": "🖼️ БЕЗ ПОСТЕРОВ", "count": no_poster_count},
        {"id": -101, "name": "📊 БЕЗ ОЦЕНОК", "count": no_ratings_count},
        {"id": -102, "name": "🆔 БЕЗ КП ID", "count": no_kp_id_count},
        {"id": -103, "name": "🆔 БЕЗ IMDb ID", "count": no_imdb_id_count},
        {"id": -104, "name": "🚫 БЕЗ ID ВООБЩЕ", "count": no_any_id_count},
        {"id": 0, "name": "Любая категория", "count": count_any},
    ]
    result.extend(cats)
    result.append({"id": -2, "name": "🗑️ ИГНОРИРУЕМЫЕ", "count": count_ignored})
    return result


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
    limit: int = 40,
):
    conn = get_db()
    cursor = conn.cursor()
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
        if hide_ignored:
            where_clauses.append("items.is_ignored = 0")
    if search:
        search_val = f"%{search.lower()}%"
        where_clauses.append(
            f"(items.title LIKE ? OR items.title_norm LIKE ? OR EXISTS (SELECT 1 FROM item_search_names sn WHERE sn.item_id = items.id AND sn.name_norm LIKE ?))"
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
        watched_ids = get_watched_item_ids(cursor)
        if watched_ids:
            where_clauses.append(f"items.id NOT IN ({','.join(map(str, watched_ids))})")
    if hide_collected and not collection_id:
        where_clauses.append("items.id NOT IN (SELECT item_id FROM collection_items)")
    if min_kp > 0:
        where_clauses.append("items.kp_rating >= ?")
        params.append(min_kp)
    if max_kp < 10:
        where_clauses.append("items.kp_rating <= ?")
        params.append(max_kp)
    if min_kp == 0 and (min_kp > 0 or max_kp < 10):
        where_clauses.append("items.kp_rating > 0")
    if min_imdb > 0:
        where_clauses.append("items.imdb_rating >= ?")
        params.append(min_imdb)
    if max_imdb < 10:
        where_clauses.append("items.imdb_rating <= ?")
        params.append(max_imdb)
    if min_imdb == 0 and (min_imdb > 0 or max_imdb < 10):
        where_clauses.append("items.imdb_rating > 0")
    if min_year:
        where_clauses.append("items.year >= ?")
        params.append(min_year)
    if max_year:
        where_clauses.append("items.year <= ?")
        params.append(max_year)
    where_sql = " AND ".join(where_clauses)
    cursor.execute(
        f"SELECT COUNT(DISTINCT items.id) FROM items WHERE {where_sql}", params
    )
    total_count = cursor.fetchone()[0]
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
    query = f"SELECT items.*, (SELECT MAX(date_added) FROM releases WHERE item_id = items.id) as latest_release FROM items {f'JOIN collection_items ci ON items.id = ci.item_id AND ci.collection_id = {collection_id}' if collection_id else ''} WHERE {where_sql} ORDER BY {order_by} LIMIT ? OFFSET ?"
    params.extend([limit, (page - 1) * limit])
    cursor.execute(query, params)
    items = [dict(row) for row in cursor.fetchall()]
    for item in items:
        cursor.execute(
            "SELECT * FROM releases WHERE item_id = ? ORDER BY date_added DESC",
            (item["id"],),
        )
        item["releases"] = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return {"items": items, "totalPages": total_pages}


@app.post("/api/ignore/{item_id}")
def ignore_item(item_id: int):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT is_ignored FROM items WHERE id = ?", (item_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return {"status": "error"}
    new_state = 1 - row["is_ignored"]
    ignored_at = (
        datetime.now().strftime("%Y-%m-%d %H:%M:%S") if new_state == 1 else None
    )
    cursor.execute(
        "UPDATE items SET is_ignored = ?, ignored_at = ? WHERE id = ?",
        (new_state, ignored_at, item_id),
    )
    conn.commit()
    conn.close()
    return {"status": "success"}


class ResetFieldsRequest(BaseModel):
    fields: list[str]


@app.post("/api/reset_item/{item_id}")
def reset_item(item_id: int, data: ResetFieldsRequest):
    conn = get_db()
    cursor = conn.cursor()

    field_map = {
        "poster": "poster_url = NULL",
        "description": "description = NULL",
        "kp_id": "kp_id = NULL",
        "imdb_id": "imdb_id = NULL",
        "rezka_url": "rezka_url = NULL",
        "ratings": "kp_rating = 0, imdb_rating = 0",
        "is_reprocessed": "is_reprocessed = 0",
        "is_metadata_fixed": "is_metadata_fixed = 0",
        "checked_poiskkino": "checked_poiskkino = 0",
        "checked_tech": "checked_tech = 0",
        "checked_rezka": "checked_rezka = 0",
    }

    updates = []
    for f in data.fields:
        if f in field_map:
            updates.append(field_map[f])

    if updates:
        # При сбросе критичных полей сбрасываем и флаги проверки
        if any(f in ["kp_id", "imdb_id", "poster", "ratings"] for f in data.fields):
            updates.append("is_reprocessed = 0")
            updates.append("is_metadata_fixed = 0")
            updates.append(
                "checked_rezka = 0"
            )  # При смене ID нужно перепроверить Резку
            if "kp_id" in data.fields or "ratings" in data.fields:
                updates.append("checked_poiskkino = 0")
                updates.append("checked_tech = 0")

        if "rezka_url" in data.fields:
            updates.append("checked_rezka = 0")

        # Убираем дубликаты если добавили флаги
        updates = list(set(updates))
        sql = f"UPDATE items SET {', '.join(updates)} WHERE id = ?"
        cursor.execute(sql, (item_id,))
        conn.commit()

    conn.close()
    return {"status": "success"}


@app.get("/api/stats")
def get_stats():
    conn = get_db()
    cursor = conn.cursor()
    ids_str = ",".join(map(str, VIDEO_CATEGORY_IDS))

    cursor.execute(
        f"SELECT COUNT(*) FROM items WHERE category_id IN ({ids_str}) AND is_ignored = 0"
    )
    total_video = cursor.fetchone()[0]

    cursor.execute(
        f"SELECT COUNT(*) FROM items WHERE category_id IN ({ids_str}) AND is_ignored = 0 AND (poster_url IS NULL OR poster_url = '')"
    )
    no_poster = cursor.fetchone()[0]

    cursor.execute(
        f"SELECT COUNT(*) FROM items WHERE category_id IN ({ids_str}) AND is_ignored = 0 AND (kp_rating = 0 OR kp_rating IS NULL OR imdb_rating = 0 OR imdb_rating IS NULL)"
    )
    no_ratings = cursor.fetchone()[0]

    cursor.execute(
        f"SELECT COUNT(*) FROM items WHERE category_id IN ({ids_str}) AND is_ignored = 0 AND (rezka_url IS NULL OR rezka_url = '')"
    )
    no_rezka = cursor.fetchone()[0]

    cursor.execute(
        f"SELECT COUNT(*) FROM items WHERE category_id IN ({ids_str}) AND is_ignored = 0 AND (kp_id IS NULL OR kp_id = '') AND (imdb_id IS NULL OR imdb_id = '')"
    )
    no_ids = cursor.fetchone()[0]

    # Последние запуски
    cursor.execute(
        "SELECT job_type, MAX(end_time) as last_run FROM job_history GROUP BY job_type"
    )
    history = {row["job_type"]: row["last_run"] for row in cursor.fetchall()}

    conn.close()
    return {
        "no_poster": no_poster,
        "no_ratings": no_ratings,
        "no_rezka": no_rezka,
        "no_ids": no_ids,
        "total_video": total_video,
        "last_runs": history,
    }


@app.get("/api/job_history")
def get_job_history(limit: int = 20):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM job_history ORDER BY start_time DESC LIMIT ?", (limit,)
    )
    history = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return history


@app.get("/api/collections")
def get_collections():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT c.*, COUNT(ci.item_id) as count FROM collections c LEFT JOIN collection_items ci ON c.id = ci.collection_id GROUP BY c.id ORDER BY c.sort_order ASC, c.name ASC"
    )
    collections = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return collections


class CollectionCreate(BaseModel):
    name: str


@app.post("/api/collections")
def create_collection(data: CollectionCreate):
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO collections (name) VALUES (?)", (data.name,))
        conn.commit()
    except sqlite3.IntegrityError:
        return {"status": "error", "message": "Коллекция существует"}
    finally:
        conn.close()
    return {"status": "success"}


@app.delete("/api/collections/{id}")
def delete_collection(id: int):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM collections WHERE id = ?", (id,))
    conn.commit()
    conn.close()
    return {"status": "success"}


class CollectionItemRequest(BaseModel):
    item_id: int


@app.post("/api/collections/{collection_id}/toggle")
def toggle_collection_item(collection_id: int, data: CollectionItemRequest):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT 1 FROM collection_items WHERE collection_id = ? AND item_id = ?",
        (collection_id, data.item_id),
    )
    if cursor.fetchone():
        cursor.execute(
            "DELETE FROM collection_items WHERE collection_id = ? AND item_id = ?",
            (collection_id, data.item_id),
        )
        action = "removed"
    else:
        cursor.execute(
            "INSERT INTO collection_items (collection_id, item_id, added_at) VALUES (?, ?, ?)",
            (collection_id, data.item_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )
        action = "added"
    conn.commit()
    conn.close()
    return {"status": "success", "action": action}


@app.get("/api/item_collections/{item_id}")
def get_item_collections(item_id: int):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT collection_id FROM collection_items WHERE item_id = ?", (item_id,)
    )
    ids = [row[0] for row in cursor.fetchall()]
    conn.close()
    return ids


class SaveOrderRequest(BaseModel):
    order: list[int]


@app.post("/api/collections/save_order")
def save_collections_order(data: SaveOrderRequest):
    conn = get_db()
    cursor = conn.cursor()
    for i, col_id in enumerate(data.order):
        cursor.execute(
            "UPDATE collections SET sort_order = ? WHERE id = ?", (i, col_id)
        )
    conn.commit()
    conn.close()
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
    conn = get_db()
    cursor = conn.cursor()
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
    cursor.execute(
        f"""
        SELECT items.id, items.title, items.year, items.category_id, items.kp_rating, items.imdb_rating,
               items.poster_url, items.description, items.imdb_id, items.kp_id, items.rezka_url,
               items.original_title,
               (SELECT MAX(date_added) FROM releases WHERE item_id = items.id) as latest_release
        FROM items WHERE {where_sql}
        ORDER BY latest_release DESC NULLS LAST
    """,
        params,
    )
    rows = cursor.fetchall()
    conn.close()

    items = []
    for row in rows:
        items.append(
            {
                "id": row[0],
                "title": row[1],
                "year": row[2],
                "category_id": row[3],
                "kp_rating": row[4],
                "imdb_rating": row[5],
                "poster_url": row[6],
                "description": row[7],
                "imdb_id": row[8],
                "kp_id": row[9],
                "rezka_url": row[10],
                "original_title": row[11],
                "latest_release": row[12],
            }
        )

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
    kp_id: str = None
    imdb_id: str = None


@app.post("/api/set_ids/{item_id}")
def set_ids(item_id: int, data: SetIdsRequest):
    conn = get_db()
    cursor = conn.cursor()
    updates = []
    if data.kp_id is not None:
        updates.append(f"kp_id = '{data.kp_id}'")
        updates.append("checked_poiskkino = 0")
        updates.append("checked_tech = 0")
        updates.append("checked_rezka = 0")
    if data.imdb_id is not None:
        updates.append(f"imdb_id = '{data.imdb_id}'")
        updates.append("checked_rezka = 0")
    if not updates:
        conn.close()
        return {"status": "error", "message": "No IDs provided"}
    updates.append("is_metadata_fixed = 0")
    updates.append("is_reprocessed = 0")
    sql = f"UPDATE items SET {', '.join(updates)} WHERE id = ?"
    cursor.execute(sql, (item_id,))
    conn.commit()
    conn.close()
    return {"status": "success"}


@app.post("/api/mark_visited")
def mark_visited():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS app_state (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute(
        "INSERT OR REPLACE INTO app_state (key, value) VALUES ('last_visit', ?)", (now,)
    )
    conn.commit()
    conn.close()
    return {"status": "success", "last_visit": now}


@app.get("/api/last_visit")
def get_last_visit():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS app_state (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    cursor.execute("SELECT value FROM app_state WHERE key = 'last_visit'")
    row = cursor.fetchone()
    conn.close()
    return {"last_visit": row[0] if row else None}


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False, loop="asyncio")
