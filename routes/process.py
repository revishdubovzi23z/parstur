import asyncio
import json
import os
from datetime import datetime

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, JSONResponse

router = APIRouter()


@router.post("/api/start_full_pipeline")
async def start_full_pipeline():
    import main

    main.check_any_running()
    await main.task_queue.add_task(main.run_pipeline_task, "full_pipeline")
    return {"status": "started"}


@router.post("/api/start_sync_video")
async def start_sync_video(
    min_year: int = None,
    max_year: int = None,
    min_date: str = None,
):
    import main

    main.check_any_running()

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

    await main.task_queue.add_task(
        main.run_script_with_args, "sync_video", "sync_job.py", args, "sync_video", log_file
    )
    return {"status": "started"}


@router.post("/api/start_sync_other")
async def start_sync_other(
    min_year: int = None,
    max_year: int = None,
    min_date: str = None,
):
    import main

    main.check_any_running()

    log_file = "sync_other_log.txt"
    with open(log_file, "w", encoding="utf-8") as f:
        f.write(f"=== Запуск парсинга ИГР и СОФТА ({datetime.now().strftime('%H:%M:%S')}) ===\n")
        if min_date:
            f.write(f"Ручная дата начала: {min_date}\n")

    args = ["other", str(min_year or 0), str(max_year or 0)]
    if min_date:
        args.append(min_date)

    await main.task_queue.add_task(
        main.run_script_with_args, "sync_other", "sync_job.py", args, "sync_other", log_file
    )
    return {"status": "started"}


@router.post("/api/start_sync_rezka")
async def start_sync_rezka():
    import main

    main.check_any_running()

    log_file = "sync_rezka_log.txt"
    with open(log_file, "w", encoding="utf-8") as f:
        f.write(f"=== Запуск синхронизации REZKA ({datetime.now().strftime('%H:%M:%S')}) ===\n")

    await main.task_queue.add_task(
        main.run_script_with_args, "rezka", "rezka_sync.py", [], "rezka", log_file
    )
    return {"status": "started"}


@router.post("/api/start_cleanup")
async def start_cleanup():
    import main

    # cleanup_duplicates rewrites items/releases/collection_items and may
    # delete rows; it must never run alongside another job (e.g. sync_job
    # or reprocess_database) that mutates the same tables.
    main.check_any_running()
    log_file = "cleanup_log.txt"
    with open(log_file, "w", encoding="utf-8") as f:
        f.write("=== Запуск очистки дубликатов ===\n")
    await main.task_queue.add_task(
        main.run_script_with_args,
        "cleanup",
        "cleanup_duplicates.py",
        [],
        "cleanup",
        log_file,
    )
    return {"status": "started"}


@router.post("/api/start_rezka_collections")
async def start_rezka_collections():
    import main

    # main.check_any_running() raises HTTPException itself when something is busy
    # and returns None otherwise; calling it as a boolean was misleading
    # (the if branch only ever fired through the raised exception inside
    # main.check_any_running, never via the truthiness check). Call it directly.
    main.check_any_running()
    log_file = "rezka_collections_log.txt"
    with open(log_file, "w", encoding="utf-8") as f:
        f.write("=== Синхронизация коллекций Rezka ===\n")
    await main.task_queue.add_task(
        main.run_script_with_args,
        "rezka_collections",
        "rezka_collections_sync.py",
        [],
        "rezka_collections",
        log_file,
    )
    return {"status": "started"}


@router.post("/api/stop/{key}")
async def stop_process(key: str):
    import main

    if not main._is_valid_status_key(key):
        raise HTTPException(status_code=400, detail="Unknown process key")

    config = main.load_config()
    graceful_timeout = config.get("shutdown", {}).get("graceful_timeout", 5)

    if key == "full_pipeline":
        main.pipeline_stop_requested = True
        active_key = main.running_processes.get("active_pipeline_key")
        if active_key and main._is_valid_status_key(active_key):
            with open(f"stop_{active_key}.flag", "w") as f:
                f.write("stop")
        proc = main.running_processes.get("active_pipeline_proc")
        if proc and proc.returncode is None:
            try:
                await asyncio.wait_for(proc.wait(), timeout=graceful_timeout)
            except asyncio.TimeoutError:
                proc.terminate()
        main.process_status["full_pipeline"] = "stopped"
        return {"status": "stopped"}

    proc = main.running_processes.get(key)
    if proc and proc.returncode is None:
        with open(f"stop_{key}.flag", "w") as f:
            f.write("stop")
        try:
            await asyncio.wait_for(proc.wait(), timeout=graceful_timeout)
        except asyncio.TimeoutError:
            proc.terminate()
        main.process_status[key] = "stopped"
        return {"status": "stopped"}
    return {"status": "not_running"}


@router.post("/api/start_fix")
async def start_fix():
    import main

    main.check_any_running()

    log_file = "fix_tech_log.txt"
    with open(log_file, "w", encoding="utf-8") as f:
        f.write(f"=== Поиск (Legacy API) {datetime.now().strftime('%H:%M:%S')} ===\n")
    await main.task_queue.add_task(
        main.run_script_with_args,
        "fix",
        "fix_posters.py",
        ["tech", log_file],
        "fix",
        log_file,
    )
    return {"status": "started"}


@router.post("/api/start_fix_poisk")
async def start_fix_poisk():
    import main

    main.check_any_running()

    log_file = "fix_poiskkino_log.txt"
    with open(log_file, "w", encoding="utf-8") as f:
        f.write(f"=== Поиск (PoiskKino API) {datetime.now().strftime('%H:%M:%S')} ===\n")
    await main.task_queue.add_task(
        main.run_script_with_args,
        "poiskkino",
        "fix_posters.py",
        ["poiskkino", log_file],
        "poiskkino",
        log_file,
    )
    return {"status": "started"}


@router.post("/api/start_reprocess")
async def start_reprocess(force: bool = False):
    import main

    main.check_any_running()

    log_file = "reprocess_log.txt"
    with open(log_file, "w", encoding="utf-8") as f:
        f.write(f"=== Полное обновление базы {datetime.now().strftime('%H:%M:%S')} ===\n")

    args = []
    if force:
        args.append("--force")

    await main.task_queue.add_task(
        main.run_script_with_args,
        "reprocess",
        "reprocess_database.py",
        args,
        "reprocess",
        log_file,
    )
    return {"status": "started"}


@router.get("/api/process_status")
def get_process_status():
    import main

    """Возвращает статусы и прогресс всех процессов."""
    progress = {}
    for key in main.process_status.keys():
        p_file = f"progress_{key}.json"
        if os.path.exists(p_file):
            try:
                with open(p_file) as f:
                    progress[key] = json.load(f)
            except Exception:
                progress[key] = {"current": 0, "total": 0}
        else:
            progress[key] = {"current": 0, "total": 0}

    return {"statuses": main.process_status, "progress": progress}


@router.get("/api/sync_log")
def get_sync_log(log_type: str = "video"):
    import main

    filename = main._LOG_FILES.get(log_type)
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


@router.get("/api/download_log")
def download_log(log_type: str = "video"):
    import main

    filename = main._LOG_FILES.get(log_type)
    if filename and os.path.exists(filename):
        return FileResponse(path=filename, filename=filename, media_type="text/plain")
    return {"error": "Файл не найден"}


@router.post("/api/clear_log")
def clear_log(log_type: str = "video"):
    import main

    filename = main._LOG_FILES.get(log_type)
    if not filename:
        return {"status": "error", "message": "Unknown log type"}
    with open(filename, "w", encoding="utf-8") as f:
        f.write("=== Очищено ===\n")
    return {"status": "success"}


@router.post("/api/sync_user")
async def sync_user_data():
    import main

    with open("user_sync_log.txt", "w", encoding="utf-8") as f:
        f.write("=== Старт ===\n")
    await main.task_queue.add_task(main.run_script, "user", "user_sync.py", "user")
    return {"status": "started"}
