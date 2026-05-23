from db import db
from logging_config import setup_logging
from settings import settings
from tmdb_client import TMDBClient

logger = setup_logging("tmdb_sync", settings.log_file_path)

CATEGORY_TO_MEDIA_TYPE = {
    1: "movie",
    5: "movie",
    12: "movie",
    4: "tv",
    16: "tv",
    7: "movie",  # Default fallback
    10: "tv",  # Default fallback
}


def sync_tmdb_collections():
    logger.info("=== СИНХРОНИЗАЦИЯ TMDB ===")
    logger.info("[*] Запуск двусторонней синхронизации...")

    client = TMDBClient()
    if not client.api_token:
        logger.error("[-] Токен TMDB v4 не найден в настройках или базе.")
        return

    collections = db.get_collections()
    if not collections:
        logger.info("[*] Локальных коллекций не найдено.")
        return

    logger.info(f"[*] Найдено локальных коллекций: {len(collections)}")

    with db._conn() as c:
        row = c.execute("SELECT value FROM app_state WHERE key = 'tmdb_account_id'").fetchone()
        account_id = row[0] if row else None

    existing_tmdb_lists = []
    if account_id:
        existing_tmdb_lists = client.get_user_lists(account_id)
        logger.info(f"[*] Найдено существующих списков на TMDB: {len(existing_tmdb_lists)}")

    for coll in collections:
        coll_id = coll["id"]
        coll_name = coll["name"]
        logger.info(f"\n[sync] Обработка коллекции '{coll_name}' (id={coll_id})")

        # Get TMDB list ID
        with db._conn() as c:
            row = c.execute(
                "SELECT value FROM app_state WHERE key = ?", (f"tmdb_list_id_{coll_id}",)
            ).fetchone()
            list_id = row[0] if row else None

        if not list_id:
            matched_list = None
            for lst in existing_tmdb_lists:
                if (lst.get("name") or "").strip().lower() == coll_name.strip().lower():
                    matched_list = lst
                    break

            if matched_list:
                list_id = str(matched_list["id"])
                logger.info(
                    f"  [✓] Найден существующий список на TMDB с именем '{coll_name}' (ID: {list_id}). Привязываем к базе."
                )
                with db._conn() as c:
                    c.execute(
                        "INSERT OR REPLACE INTO app_state (key, value) VALUES (?, ?)",
                        (f"tmdb_list_id_{coll_id}", list_id),
                    )
            else:
                logger.info(f"  [+] Создаем новый список на TMDB для '{coll_name}'")
                list_id = client.create_list(
                    coll_name, f"Синхронизировано из Antigravity Tracker (Коллекция ID {coll_id})"
                )
                if list_id:
                    logger.info(f"  [✓] Создан список на TMDB с ID {list_id}")
                    with db._conn() as c:
                        c.execute(
                            "INSERT OR REPLACE INTO app_state (key, value) VALUES (?, ?)",
                            (f"tmdb_list_id_{coll_id}", list_id),
                        )
                else:
                    logger.error(f"  [-] Не удалось создать список на TMDB для '{coll_name}'")
                    continue

        logger.info(f"  [*] Используем список TMDB ID {list_id}")

        # Get items in local collection
        with db._conn() as c:
            local_item_ids = [
                r[0]
                for r in c.execute(
                    "SELECT item_id FROM collection_items WHERE collection_id = ?",
                    (coll_id,),
                ).fetchall()
            ]

        local_tmdb_items = []
        for item_id in local_item_ids:
            item = db.get_item(item_id)
            if not item:
                continue

            imdb_id = item.get("imdb_id")
            category_id = item.get("category_id")
            media_type = CATEGORY_TO_MEDIA_TYPE.get(category_id, "movie")

            tmdb_id = None
            if imdb_id:
                meta = client.find_by_imdb_id(imdb_id, return_meta=True)
                if meta:
                    tmdb_id = meta.get("tmdb_id")
                    media_type = meta.get("media_type") or media_type

            if not tmdb_id:
                title = item.get("title")
                year = item.get("year")
                if title:
                    meta = client.search_movie(title, year)
                    if meta:
                        tmdb_id = meta.get("tmdb_id")
                        media_type = meta.get("media_type") or media_type

            if tmdb_id:
                local_tmdb_items.append(
                    {"media_type": media_type, "media_id": int(tmdb_id), "local_id": item_id}
                )
            else:
                logger.warning(f"  [?] Не удалось сопоставить с TMDB: {item.get('title')}")

        # Get items currently on TMDB list
        tmdb_list_items = client.get_list_items(list_id)

        tmdb_item_set = {
            (item.get("media_type", "movie"), item.get("id"))
            for item in tmdb_list_items
            if item.get("id")
        }

        local_item_set = {(item["media_type"], item["media_id"]) for item in local_tmdb_items}

        # 1. ADD TO TMDB (Local -> TMDB)
        to_add_to_tmdb = [
            item
            for item in local_tmdb_items
            if (item["media_type"], item["media_id"]) not in tmdb_item_set
        ]

        if to_add_to_tmdb:
            logger.info(f"  [+] Добавляем {len(to_add_to_tmdb)} элементов на TMDB")
            items_payload = [
                {"media_type": i["media_type"], "media_id": i["media_id"]} for i in to_add_to_tmdb
            ]
            client.add_items_to_list(list_id, items_payload)
            logger.info(f"  [✓] Успешно добавлено {len(to_add_to_tmdb)} элементов.")

        # 2. PULL FROM TMDB (TMDB -> Local)
        to_add_to_local = [
            item
            for item in tmdb_list_items
            if item.get("id")
            and (item.get("media_type", "movie"), item["id"]) not in local_item_set
        ]

        if to_add_to_local:
            logger.info(f"  [↓] Загружаем {len(to_add_to_local)} элементов из TMDB в проект")
            for tmdb_item in to_add_to_local:
                tmdb_id = tmdb_item["id"]
                media_type = tmdb_item.get("media_type", "movie")
                title = tmdb_item.get("title") or tmdb_item.get("name") or "Unknown"
                date_str = tmdb_item.get("release_date") or tmdb_item.get("first_air_date") or ""
                year = int(date_str[:4]) if date_str and len(date_str) >= 4 else None
                category_id = 1 if media_type == "movie" else 4

                # Try to find in DB
                item_id = None
                with db._conn() as c:
                    row = c.execute(
                        "SELECT id FROM items WHERE title = ? AND year = ? AND category_id = ?",
                        (title, year, category_id),
                    ).fetchone()
                    if row:
                        item_id = row[0]

                if not item_id:
                    # Create stub
                    logger.info(f"    [new] Создаем карточку для '{title}' ({year})")
                    try:
                        with db._conn() as c:
                            cursor = c.execute(
                                "INSERT INTO items (title, year, category_id, is_metadata_fixed) VALUES (?, ?, ?, 1)",
                                (title, year, category_id),
                            )
                            item_id = cursor.lastrowid
                    except Exception as e:
                        logger.error(f"    [-] Ошибка создания карточки: {e}")
                        continue

                # Add to collection
                if item_id:
                    try:
                        with db._conn() as c:
                            c.execute(
                                "INSERT OR IGNORE INTO collection_items (collection_id, item_id) VALUES (?, ?)",
                                (coll_id, item_id),
                            )
                    except Exception as e:
                        logger.error(f"    [-] Ошибка добавления в коллекцию: {e}")

        logger.info(f"  [✓] Синхронизация коллекции '{coll_name}' завершена.")

    logger.info("=== СИНХРОНИЗАЦИЯ TMDB ЗАВЕРШЕНА ===")


if __name__ == "__main__":
    sync_tmdb_collections()
