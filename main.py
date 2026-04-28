from fastapi import FastAPI, Request, BackgroundTasks
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
import sqlite3
import uvicorn
import unicodedata
from pydantic import BaseModel
import os
import subprocess
import sys
import signal
from app_core import RUTOR_CATEGORIES, VIDEO_CATEGORY_IDS

app = FastAPI(title="Tracker Filter")


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
    return FileResponse("static/icon.png")  # Заглушка если есть


# Состояние процессов: храним реальные объекты Popen
running_processes = {"sync": None, "fix": None, "user": None, "cleanup": None, "rezka": None, "fix_titles": None}
process_status = {"sync": "idle", "fix": "idle", "user": "idle", "cleanup": "idle", "rezka": "idle", "fix_titles": "idle"}


def run_script(script_name, status_key):
    global process_status, running_processes
    process_status[status_key] = "running"
    env = {
        **os.environ,
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
        "PYTHONUNBUFFERED": "1",
    }
    try:
        proc = subprocess.Popen([sys.executable, script_name], env=env)
        running_processes[status_key] = proc
        proc.wait()
        process_status[status_key] = "completed" if proc.returncode == 0 else "stopped"
    except Exception as e:
        print(f"Ошибка при запуске {script_name}: {e}")
        process_status[status_key] = "error"
    finally:
        running_processes[status_key] = None


@app.post("/api/start_sync_video")
def start_sync_video(
    background_tasks: BackgroundTasks,
    min_year: int = None,
    max_year: int = None,
    min_date: str = None,
):
    from datetime import datetime

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

    background_tasks.add_task(
        run_script_with_args, "sync_job.py", args, "sync_video", log_file
    )
    return {"status": "started"}


@app.post("/api/start_sync_other")
def start_sync_other(
    background_tasks: BackgroundTasks,
    min_year: int = None,
    max_year: int = None,
    min_date: str = None,
):
    from datetime import datetime

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

    background_tasks.add_task(
        run_script_with_args, "sync_job.py", args, "sync_other", log_file
    )
    return {"status": "started"}


def run_script_with_args(script_name, args, status_key, log_file=None):
    global process_status, running_processes
    process_status[status_key] = "running"
    env = {
        **os.environ,
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
        "PYTHONUNBUFFERED": "1",
    }
    try:
        if log_file:
            with open(log_file, "a", encoding="utf-8") as f:
                proc = subprocess.Popen(
                    [sys.executable, script_name] + args, stdout=f, stderr=f, env=env
                )
        else:
            proc = subprocess.Popen([sys.executable, script_name] + args, env=env)
        running_processes[status_key] = proc
        proc.wait()
        process_status[status_key] = "completed" if proc.returncode == 0 else "stopped"
    except Exception as e:
        print(f"Ошибка при запуске {script_name} {args}: {e}")
        process_status[status_key] = "error"
    finally:
        running_processes[status_key] = None


@app.post("/api/start_sync_rezka")
def start_sync_rezka(background_tasks: BackgroundTasks):
    from datetime import datetime

    log_file = "sync_rezka_log.txt"
    with open(log_file, "w", encoding="utf-8") as f:
        f.write(f"=== Запуск синхронизации REZKA ({datetime.now().strftime('%H:%M:%S')}) ===\n")

    background_tasks.add_task(
        run_script_with_args, "rezka_sync.py", [], "rezka", log_file
    )
    return {"status": "started"}


@app.post("/api/start_fix_titles")
def start_fix_titles(background_tasks: BackgroundTasks):
    from datetime import datetime
    log_file = "fix_titles_log.txt"
    with open(log_file, "w", encoding="utf-8") as f:
        f.write(f"=== Запуск исправления названий ({datetime.now().strftime('%H:%M:%S')}) ===\n")
    background_tasks.add_task(
        run_script, "fix_corrupted_titles.py", "fix_titles"
    )
    return {"status": "started"}


@app.post("/api/start_cleanup")
def start_cleanup(background_tasks: BackgroundTasks):
    log_file = "cleanup_log.txt"
    with open(log_file, "w", encoding="utf-8") as f:
        f.write("=== Запуск очистки дубликатов ===\n")
    background_tasks.add_task(
        run_script_with_args, "cleanup_duplicates.py", [], "cleanup", log_file
    )
    return {"status": "started"}


@app.post("/api/stop/{key}")
def stop_process(key: str):
    proc = running_processes.get(key)
    if proc and proc.poll() is None:
        proc.terminate()
        process_status[key] = "stopped"
        return {"status": "stopped"}
    return {"status": "not_running"}


@app.post("/api/start_fix")
def start_fix(background_tasks: BackgroundTasks):
    from datetime import datetime

    log_file = "fix_log.txt"
    # Очищаем лог при новом запуске (по желанию можно оставить "a" в скрипте)
    with open(log_file, "w", encoding="utf-8") as f:
        f.write(f"=== Поиск (Legacy API) {datetime.now().strftime('%H:%M:%S')} ===\n")
    background_tasks.add_task(
        run_script_with_args, "fix_posters.py", ["tech"], "fix", log_file
    )
    return {"status": "started"}


@app.post("/api/start_fix_uz")
def start_fix_uz(background_tasks: BackgroundTasks):
    from datetime import datetime

    log_file = "fix_log.txt"
    with open(log_file, "w", encoding="utf-8") as f:
        f.write(f"=== Поиск (UZ API) {datetime.now().strftime('%H:%M:%S')} ===\n")
    background_tasks.add_task(
        run_script_with_args, "fix_posters.py", ["uz"], "fix", log_file
    )
    return {"status": "started"}


@app.post("/api/start_fix_poisk")
def start_fix_poisk(background_tasks: BackgroundTasks):
    from datetime import datetime

    log_file = "fix_log.txt"
    with open(log_file, "w", encoding="utf-8") as f:
        f.write(
            f"=== Поиск (PoiskKino API) {datetime.now().strftime('%H:%M:%S')} ===\n"
        )
    background_tasks.add_task(
        run_script_with_args, "fix_posters.py", ["poiskkino"], "poiskkino", log_file
    )
    return {"status": "started"}


@app.post("/api/start_reprocess")
def start_reprocess(background_tasks: BackgroundTasks):
    from datetime import datetime

    log_file = "reprocess_log.txt"
    with open(log_file, "w", encoding="utf-8") as f:
        f.write(
            f"=== Полное обновление базы {datetime.now().strftime('%H:%M:%S')} ===\n"
        )
    background_tasks.add_task(
        run_script_with_args, "reprocess_database.py", [], "reprocess", log_file
    )
    return {"status": "started"}


@app.get("/api/process_status")
def get_process_status():
    return process_status


def get_db():
    conn = sqlite3.connect("app_data.db", timeout=30.0)
    conn.row_factory = sqlite3.Row

    # Регистрируем функцию для корректной работы с кириллицей в нижнем регистре + нормализация
    def py_lower(x):
        if x is None:
            return None
        return unicodedata.normalize("NFC", str(x)).lower().replace('x', 'х').strip()

    conn.create_function("py_lower", 1, py_lower)
    return conn


def get_watched_item_ids(cursor):
    """
    Один раз собирает все ID фильмов которые просмотрены.
    Использует IMDb ID, Кинопоиск ID и нормализованные названия.
    """
    cursor.execute(
        "SELECT imdb_id, kp_id, title_norm, original_title_norm, item_year FROM user_ratings"
    )
    ratings = cursor.fetchall()

    if not ratings:
        return set()

    rated_imdb_ids = set()
    rated_kp_ids = set()
    rated_names = {}  # name_norm -> список годов

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

    # 1. Матч по IMDb ID
    if rated_imdb_ids:
        placeholders = ",".join("?" * len(rated_imdb_ids))
        cursor.execute(
            f"SELECT id FROM items WHERE imdb_id IN ({placeholders})",
            list(rated_imdb_ids),
        )
        for row in cursor.fetchall():
            watched_ids.add(row[0])

    # 2. Матч по Кинопоиск ID
    if rated_kp_ids:
        placeholders = ",".join("?" * len(rated_kp_ids))
        cursor.execute(
            f"SELECT id FROM items WHERE kp_id IN ({placeholders})",
            list(rated_kp_ids),
        )
        for row in cursor.fetchall():
            watched_ids.add(row[0])

    # 3. Матч по названиям
    if rated_names:
        name_list = list(rated_names.keys())
        # Разбиваем на чанки если имен слишком много (SQLite limit)
        chunk_size = 900
        for i in range(0, len(name_list), chunk_size):
            chunk = name_list[i : i + chunk_size]
            placeholders = ",".join("?" * len(chunk))
            cursor.execute(
                f"""
                SELECT sn.item_id, sn.name_norm, i.year 
                FROM item_search_names sn 
                JOIN items i ON sn.item_id = i.id
                WHERE sn.name_norm IN ({placeholders})
                """,
                chunk,
            )
            for item_id, name_norm, item_year in cursor.fetchall():
                # Для простоты скрываем все совпадения по названию (для сериалов полезно)
                watched_ids.add(item_id)

    return watched_ids


@app.get("/api/categories")
def get_categories(hide_rated: bool = False, hide_collected: bool = False):
    conn = get_db()
    cursor = conn.cursor()

    # Если нужно скрыть просмотренные — один раз получаем список их ID
    watched_ids = get_watched_item_ids(cursor) if hide_rated else set()
    
    def make_filters(alias="i"):
        clauses = []
        if watched_ids:
            ids_str = ",".join(map(str, watched_ids))
            clauses.append(f"{alias}.id NOT IN ({ids_str})")
        if hide_collected:
            clauses.append(f"{alias}.id NOT IN (SELECT item_id FROM collection_items)")
        
        if not clauses:
            return ""
        return " AND " + " AND ".join(clauses)

    not_in = make_filters("i")

    # Считаем количество в реальных категориях
    cursor.execute(f"""
        SELECT c.id, c.name, (
            SELECT COUNT(*) FROM items i 
            WHERE i.category_id = c.id AND i.is_ignored = 0 {not_in}
        ) as count
        FROM categories c 
        ORDER BY c.name
    """)
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

    # Считаем количество без постеров и без оценок
    cursor.execute(f"""
        SELECT COUNT(*) FROM items i 
        WHERE i.category_id IN ({ids_str_cats}) AND i.is_ignored = 0 
        AND (i.poster_url IS NULL OR i.poster_url = '') {not_in}
    """)
    no_poster_count = cursor.fetchone()[0]

    cursor.execute(f"""
        SELECT COUNT(*) FROM items i 
        WHERE i.category_id IN ({ids_str_cats}) AND i.is_ignored = 0 
        AND (i.kp_rating = 0 OR i.kp_rating IS NULL OR i.imdb_rating = 0 OR i.imdb_rating IS NULL) {not_in}
    """)
    no_ratings_count = cursor.fetchone()[0]

    cursor.execute(f"""
        SELECT COUNT(*) FROM items i 
        WHERE i.category_id IN ({ids_str_cats}) AND i.is_ignored = 0 
        AND (i.kp_id IS NULL OR i.kp_id = '') {not_in}
    """)
    no_kp_id_count = cursor.fetchone()[0]

    cursor.execute(f"""
        SELECT COUNT(*) FROM items i 
        WHERE i.category_id IN ({ids_str_cats}) AND i.is_ignored = 0 
        AND (i.imdb_id IS NULL OR i.imdb_id = '') {not_in}
    """)
    no_imdb_id_count = cursor.fetchone()[0]

    cursor.execute(f"""
        SELECT COUNT(*) FROM items i 
        WHERE i.category_id IN ({ids_str_cats}) AND i.is_ignored = 0 
        AND (i.kp_id IS NULL OR i.kp_id = '') AND (i.imdb_id IS NULL OR i.imdb_id = '') {not_in}
    """)
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


# API для получения ленты
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
        # Фильтр по коллекции (закладке)
        where_clauses.append(
            "items.id IN (SELECT item_id FROM collection_items WHERE collection_id = ?)"
        )
        params.append(collection_id)
    elif category_id == -2:
        # Специальная категория для игнорируемых
        where_clauses.append("items.is_ignored = 1")
    else:
        if category_id == -1:
            # Общая категория видео-файлов
            ids_str = ",".join(map(str, VIDEO_CATEGORY_IDS))
            where_clauses.append(f"items.category_id IN ({ids_str})")
        elif category_id == -100:
            # Без постеров
            ids_str = ",".join(map(str, VIDEO_CATEGORY_IDS))
            where_clauses.append(f"items.category_id IN ({ids_str})")
            where_clauses.append("(items.poster_url IS NULL OR items.poster_url = '')")
        elif category_id == -101:
            # Без оценок
            ids_str = ",".join(map(str, VIDEO_CATEGORY_IDS))
            where_clauses.append(f"items.category_id IN ({ids_str})")
            where_clauses.append(
                "(items.kp_rating = 0 OR items.kp_rating IS NULL OR items.imdb_rating = 0 OR items.imdb_rating IS NULL)"
            )
        elif category_id == -102:
            # Без КП ID
            ids_str = ",".join(map(str, VIDEO_CATEGORY_IDS))
            where_clauses.append(f"items.category_id IN ({ids_str})")
            where_clauses.append("(items.kp_id IS NULL OR items.kp_id = '')")
        elif category_id == -103:
            # Без IMDb ID
            ids_str = ",".join(map(str, VIDEO_CATEGORY_IDS))
            where_clauses.append(f"items.category_id IN ({ids_str})")
            where_clauses.append("(items.imdb_id IS NULL OR items.imdb_id = '')")
        elif category_id == -104:
            # Без ID вообще
            ids_str = ",".join(map(str, VIDEO_CATEGORY_IDS))
            where_clauses.append(f"items.category_id IN ({ids_str})")
            where_clauses.append("(items.kp_id IS NULL OR items.kp_id = '') AND (items.imdb_id IS NULL OR items.imdb_id = '')")
        elif category_id != 0:
            where_clauses.append("items.category_id = ?")
            params.append(category_id)

        if hide_ignored:
            where_clauses.append("items.is_ignored = 0")

    if search:
        search_val = f"%{search.lower()}%"
        where_clauses.append(f"""(
            items.title LIKE ? OR 
            items.title_norm LIKE ? OR
            EXISTS (SELECT 1 FROM item_search_names sn WHERE sn.item_id = items.id AND sn.name_norm LIKE ?)
        )""")
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
            ids_str = ",".join(map(str, watched_ids))
            where_clauses.append(f"items.id NOT IN ({ids_str})")

    if hide_collected and not collection_id:
        where_clauses.append("items.id NOT IN (SELECT item_id FROM collection_items)")

    # Рейтинги (КП)
    if min_kp > 0 or max_kp < 10:
        if min_kp > 0:
            where_clauses.append("items.kp_rating >= ?")
            params.append(min_kp)
        if max_kp < 10:
            where_clauses.append("items.kp_rating <= ?")
            params.append(max_kp)
        # Если фильтр активен, но min_kp не задан (0), исключаем 0.0 (неоцененные)
        if min_kp == 0:
            where_clauses.append("items.kp_rating > 0")

    # Рейтинги (IMDb)
    if min_imdb > 0 or max_imdb < 10:
        if min_imdb > 0:
            where_clauses.append("items.imdb_rating >= ?")
            params.append(min_imdb)
        if max_imdb < 10:
            where_clauses.append("items.imdb_rating <= ?")
            params.append(max_imdb)
        if min_imdb == 0:
            where_clauses.append("items.imdb_rating > 0")

    if min_year:
        where_clauses.append("items.year >= ?")
        params.append(min_year)

    if max_year:
        where_clauses.append("items.year <= ?")
        params.append(max_year)

    where_sql = " AND ".join(where_clauses)

    # Считаем общее количество страниц
    count_query = f"SELECT COUNT(DISTINCT items.id) FROM items WHERE {where_sql}"
    cursor.execute(count_query, params)
    total_count = cursor.fetchone()[0]
    total_pages = (total_count + limit - 1) // limit if total_count > 0 else 1

    if category_id == -2:
        order_by = "items.ignored_at DESC, latest_release DESC"
    elif collection_id:
        order_by = "ci.added_at DESC, latest_release DESC"
    else:
        order_by = "latest_release DESC NULLS LAST"

    query = f"""
        SELECT items.*, (SELECT MAX(date_added) FROM releases WHERE item_id = items.id) as latest_release 
        FROM items 
        {f"JOIN collection_items ci ON items.id = ci.item_id AND ci.collection_id = {collection_id}" if collection_id else ""}
        WHERE {where_sql}
        ORDER BY {order_by}
    """

    offset = (page - 1) * limit
    query += " LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    cursor.execute(query, params)
    items = [dict(row) for row in cursor.fetchall()]

    # Подтягиваем раздачи для каждого фильма
    for item in items:
        cursor.execute(
            "SELECT * FROM releases WHERE item_id = ? ORDER BY date_added DESC",
            (item["id"],),
        )
        item["releases"] = [dict(row) for row in cursor.fetchall()]

    conn.close()
    return {"items": items, "totalPages": total_pages}


class IgnoreRequest(BaseModel):
    item_id: int


@app.post("/api/ignore/{item_id}")
def ignore_item(item_id: int):
    from datetime import datetime
    conn = get_db()
    cursor = conn.cursor()
    # Получаем текущее состояние
    cursor.execute("SELECT is_ignored FROM items WHERE id = ?", (item_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return {"status": "error"}
    
    new_state = 1 - row['is_ignored']
    ignored_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S") if new_state == 1 else None
    
    cursor.execute(
        "UPDATE items SET is_ignored = ?, ignored_at = ? WHERE id = ?", 
        (new_state, ignored_at, item_id)
    )
    conn.commit()
    conn.close()
    return {"status": "success"}


@app.get("/api/stats")
def get_stats():
    conn = get_db()
    cursor = conn.cursor()
    ids_str = ",".join(map(str, VIDEO_CATEGORY_IDS))
    video_cats = f"({ids_str})"

    # Без постера
    cursor.execute(
        f"SELECT COUNT(*) FROM items WHERE category_id IN {video_cats} AND is_ignored = 0 AND (poster_url IS NULL OR poster_url = '')"
    )
    no_poster = cursor.fetchone()[0]

    # Без оценок
    cursor.execute(
        f"SELECT COUNT(*) FROM items WHERE category_id IN {video_cats} AND is_ignored = 0 AND (kp_rating = 0 OR kp_rating IS NULL OR imdb_rating = 0 OR imdb_rating IS NULL)"
    )
    no_ratings = cursor.fetchone()[0]

    # Всего видео
    cursor.execute(f"SELECT COUNT(*) FROM items WHERE category_id IN {video_cats} AND is_ignored = 0")
    total_video = cursor.fetchone()[0]

    conn.close()
    return {
        "no_poster": no_poster,
        "no_ratings": no_ratings,
        "total_video": total_video,
    }


# --- Коллекции (Закладки) ---


@app.get("/api/collections")
def get_collections():
    conn = get_db()
    cursor = conn.cursor()
    # Считаем количество предметов в каждой коллекции
    cursor.execute("""
        SELECT c.*, COUNT(ci.item_id) as count 
        FROM collections c 
        LEFT JOIN collection_items ci ON c.id = ci.collection_id 
        GROUP BY c.id
        ORDER BY c.sort_order ASC, c.name ASC
    """)
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
        return {"status": "error", "message": "Коллекция с таким именем уже существует"}
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
    from datetime import datetime
    conn = get_db()
    cursor = conn.cursor()
    # Проверяем, есть ли уже этот предмет в коллекции
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
        added_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute(
            "INSERT INTO collection_items (collection_id, item_id, added_at) VALUES (?, ?, ?)",
            (collection_id, data.item_id, added_at),
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
    order: list[int]  # Список ID в нужном порядке


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


# HTML Интерфейс
@app.get("/", response_class=HTMLResponse)
def get_index():
    with open("index.html", "r", encoding="utf-8") as f:
        return f.read()


import os
from fastapi.responses import FileResponse


@app.get("/api/sync_log")
def get_sync_log(log_type: str = "video"):
    log_files = {
        "video": "sync_video_log.txt",
        "other": "sync_other_log.txt",
        "fix": "fix_log.txt",
        "user": "user_sync_log.txt",
        "reprocess": "reprocess_log.txt",
        "cleanup": "cleanup_log.txt",
        "rezka": "sync_rezka_log.txt",
        "fix_titles": "fix_titles_log.txt",
    }
    filename = log_files.get(log_type, "sync_video_log.txt")

    try:
        if os.path.exists(filename):
            with open(filename, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
                return {"log": "".join(lines[-100:]), "filename": filename}
        return {"log": f"Лог-файл '{filename}' пока пуст.", "filename": filename}
    except Exception as e:
        return {"log": f"Ошибка чтения лога: {e}"}


@app.get("/api/download_log")
def download_log(log_type: str = "video"):
    log_files = {
        "video": "sync_video_log.txt",
        "other": "sync_other_log.txt",
        "fix": "fix_log.txt",
        "user": "user_sync_log.txt",
        "reprocess": "reprocess_log.txt",
        "cleanup": "cleanup_log.txt",
        "rezka": "sync_rezka_log.txt",
        "fix_titles": "fix_titles_log.txt",
    }
    filename = log_files.get(log_type, "sync_video_log.txt")
    if os.path.exists(filename):
        return FileResponse(path=filename, filename=filename, media_type="text/plain")
    return {"error": "Файл не найден"}


@app.post("/api/clear_log")
def clear_log(log_type: str = "video"):
    log_files = {
        "video": "sync_video_log.txt",
        "other": "sync_other_log.txt",
        "fix": "fix_log.txt",
        "user": "user_sync_log.txt",
        "reprocess": "reprocess_log.txt",
        "cleanup": "cleanup_log.txt",
        "rezka": "sync_rezka_log.txt",
        "fix_titles": "fix_titles_log.txt",
    }
    filename = log_files.get(log_type, "sync_video_log.txt")
    try:
        with open(filename, "w", encoding="utf-8") as f:
            f.write(
                f"=== Лог очищен пользователем ({os.getlogin() if hasattr(os, 'getlogin') else 'user'}) ===\n"
            )
        return {"status": "success"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


from user_sync import UserSync


@app.post("/api/sync_user")
def sync_user_data(background_tasks: BackgroundTasks):
    import os

    kp_id = os.getenv("KINOPOISK_USER_ID")
    imdb_id = os.getenv("IMDB_USER_ID")

    if not kp_id and not imdb_id:
        return {
            "status": "error",
            "message": "IDs не настроены в .env (KINOPOISK_USER_ID, IMDB_USER_ID)",
        }

    with open("user_sync_log.txt", "w", encoding="utf-8") as f:
        f.write(
            "=== Инициализация синхронизации ваших оценок ===\nПожалуйста, подождите...\n"
        )
    background_tasks.add_task(run_script, "user_sync.py", "user")
    return {"status": "started", "message": "Синхронизация запущена в фоновом режиме."}


if __name__ == "__main__":
    print("Запуск сервера на http://localhost:8000")
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
