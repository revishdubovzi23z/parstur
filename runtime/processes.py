"""Background process registry and runner.

This module owns the lifecycle of long-running sync / cleanup / pipeline
scripts. The previous monolithic `main.py` exposed the same names as
module-level attributes; routes referenced them via `import main`.
Everything is preserved (same names, same semantics) — just relocated.

Exposed names:

* `PROCESS_KEYS` / `VALID_STATUS_KEYS` — the canonical set of valid
  background-process status keys (also gates `/api/stop/{key}` against
  path traversal).
* `running_processes` — `{key: asyncio.subprocess.Process | None, ...}`
  plus two sentinel slots used by pipeline orchestration.
* `process_status` — `{key: "idle" | "queued" | "running" | "completed"
  | "stopped" | "error"}`.
* `pipeline_stop_requested` — module-level bool, mutated by both the
  pipeline runner and the `/api/stop/full_pipeline` handler.
* `_LOG_FILES` — log-type → filename routing for `/api/sync_log` etc.
* `_is_valid_status_key`, `_read_progress`, `check_any_running` — helpers.
* `run_script`, `run_script_with_args`, `run_pipeline_task` — the
  three subprocess runners.
* `TaskQueue` / `task_queue` — the single-worker FIFO that serialises
  background jobs.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
import traceback
from datetime import datetime

from fastapi import HTTPException

from db import db
from runtime.ws import ws_manager
from script_utils import clear_checkpoint, clear_stop_flag
from settings import settings

logger = logging.getLogger("parsclode.runtime.processes")

# Single source of truth for every background-process key the app
# knows about. `running_processes` (real Popen objects) and
# `process_status` (idle / queued / running / done / error) are both
# derived from this tuple so the two dicts can never drift out of
# sync.
#
# `active_pipeline_*` are sentinel slots inside `running_processes`
# only; they don't carry a status and aren't a "process key".
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
    "kinopub",
    "tmdb",
    "full_pipeline",
    "single_update",
)

running_processes: dict = {key: None for key in PROCESS_KEYS}
running_processes["active_pipeline_proc"] = None
running_processes["active_pipeline_key"] = None

process_status: dict[str, str] = {key: "idle" for key in PROCESS_KEYS}

# Whitelist of valid status keys, used to guard endpoints/IO that
# build paths from a key (`stop_<key>.flag`, `progress_<key>.json`,
# etc.). Anything outside this set must be rejected to prevent path
# traversal via `/api/stop/{key}`.
VALID_STATUS_KEYS = frozenset(PROCESS_KEYS)

pipeline_stop_requested = False


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
    "kinopub": "sync_kinopub_log.txt",
    "tmdb": "sync_tmdb_log.txt",
    "single_update": "single_update_log.txt",
}


def _is_valid_status_key(key: str) -> bool:
    return isinstance(key, str) and key in VALID_STATUS_KEYS


def _read_progress(key: str) -> dict:
    """Return the latest `{current, total}` for `key` from disk.

    The sync / reprocess scripts write `progress_<key>.json` between
    iterations; this helper is called from the websocket loop ~once
    per second to drive the frontend progress bars.
    """
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


def check_any_running() -> None:
    """Raise 400 if any background job is already running or queued.

    Used by every `start_*` endpoint to enforce the single-writer
    invariant — sync / reprocess / cleanup all mutate overlapping
    tables and must never run concurrently.
    """
    for key, status in process_status.items():
        if status in ("running", "queued"):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Другой процесс ({key}) уже "
                    f"{'запущен' if status == 'running' else 'в очереди'}. "
                    "Пожалуйста, дождитесь его завершения."
                ),
            )


class TaskQueue:
    """Single-worker FIFO that serialises background jobs.

    The queue holds `(coro_func, status_key, args, kwargs)` tuples;
    the worker drains them one at a time so background scripts never
    race against each other on the SQLite DB.
    """

    def __init__(self) -> None:
        self.queue: asyncio.Queue = asyncio.Queue()
        self.worker_task: asyncio.Task | None = None

    async def add_task(self, coro_func, status_key, *args, **kwargs) -> None:
        process_status[status_key] = "queued"
        logger.info(f"[QUEUE] Task added: {status_key}")
        await self.queue.put((coro_func, status_key, args, kwargs))

    async def worker(self) -> None:
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

    def start(self) -> None:
        logger.info("[QUEUE] Starting worker task...")
        self.worker_task = asyncio.create_task(self.worker())

    def stop(self) -> None:
        if self.worker_task:
            logger.info("[QUEUE] Stopping worker task...")
            self.worker_task.cancel()


task_queue = TaskQueue()


async def run_script(script_name: str, status_key: str) -> None:
    """Run a background script as a subprocess without I/O capture.

    Used by the `sync_user` endpoint, which streams output to its
    own log file. Status transitions are broadcast over the
    websocket.
    """
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


async def run_script_with_args(
    script_name: str,
    args: list[str],
    status_key: str,
    log_file: str | None = None,
    is_pipeline_step: bool = False,
) -> None:
    """Run a background script with arguments and live log streaming.

    Captures stdout via a pipe so progress lines (and full transcripts)
    can be tailed into `log_file` AND mirrored to all connected
    websocket clients in real time. Job duration / progress counters
    are persisted to `job_history` on completion.
    """
    process_status[status_key] = "running"
    await ws_manager.broadcast({"type": "status", "key": status_key, "value": "running"})

    start_time = datetime.now()
    last_progress_broadcast = 0.0

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
    status = "completed"

    try:
        logger.info(f"[WORKER] Starting process: {sys.executable} -u {script_path} {args}")

        # PIPE for reliable async stdout reads; `-u` disables Python
        # output buffering so log lines surface immediately.
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

        if log_file:
            with open(log_file, "a", encoding="utf-8") as f:
                if not is_pipeline_step:
                    f.write(
                        f"=== [WORKER] Запуск {script_name} "
                        f"({datetime.now().strftime('%H:%M:%S')}) ===\n"
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

        # Wait again in case the read loop exited before the process did.
        await proc.wait()

        if proc.returncode == 0:
            status = "completed"
        elif proc.returncode in (-15, 1, 15):
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


async def run_pipeline_task() -> None:
    """Run the canonical sync → reprocess → fix → rezka → cleanup chain.

    Drives `run_script_with_args` step-by-step. A step that ends in
    "stopped" or "error" short-circuits the rest of the chain so a
    partial DB state doesn't get cleaned up by the next pass.
    """
    global pipeline_stop_requested
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

            await run_script_with_args(script, args, key, log, is_pipeline_step=True)

            if process_status[key] in ("stopped", "error"):
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


def set_pipeline_stop_requested(value: bool) -> None:
    """Cross-module setter for `pipeline_stop_requested`.

    Reassigning the module attribute from another module (e.g. a
    route handler that does `runtime.processes.pipeline_stop_requested
    = True`) works in Python, but routing the write through this
    helper keeps the mutation point grep-able.
    """
    global pipeline_stop_requested
    pipeline_stop_requested = value
