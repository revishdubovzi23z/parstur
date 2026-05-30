import json
import os
import re
import time

import requests

from app_core import VIDEO_CATEGORY_IDS
from db import Database
from logging_config import setup_logging
from script_utils import clear_stop_flag, load_config, should_stop
from settings import settings
from tmdb_client import TMDBClient

RUTOR_MIRROR = settings.rutor_mirror.rstrip("/")

GARBAGE_KEYWORDS = [
    "S01",
    "S02",
    "S03",
    "S04",
    "S05",
    "S06",
    "S07",
    "S08",
    "S09",
    "S10",
    "L1",
    "L2",
    "MVO",
    "DVO",
    "ПОЛНЫЙ",
    "СЕЗОН",
    "WEB-DL",
    "BDRIP",
    "1080P",
    "720P",
]

_config = load_config()
REPROCESS_BATCH_SIZE = _config.get("reprocess", {}).get("batch_size", 100)
REPROCESS_REQUEST_DELAY = _config.get("reprocess", {}).get("request_delay", 0.5)
STATUS_KEY = settings.status_key
logger = setup_logging("parsclode.reprocess", "reprocess_log.txt")


def has_garbage_title(title):
    return any(x in (title or "").upper() for x in GARBAGE_KEYWORDS)


def report_progress(current, total, status_key="reprocess"):
    try:
        p_file = os.path.join(settings.app_data_dir, f"progress_{status_key}.json")
        with open(p_file, "w") as f:
            json.dump({"current": current, "total": total}, f)
    except Exception:
        pass


def reprocess_all(force_all=False, specific_id=None):
    logger = setup_logging("parsclode.reprocess", "reprocess_log.txt")
    clear_stop_flag(STATUS_KEY)
    db = Database()
    conn = db.get_connection()

    logger.info("Подключено к БД. Режим WAL включен.")

    cats_ph = ",".join(["?"] * len(VIDEO_CATEGORY_IDS))
    garbage_like = " OR ".join(["items.title LIKE ?" for _ in GARBAGE_KEYWORDS[:6]])
    garbage_params = [f"%{kw}%" for kw in GARBAGE_KEYWORDS[:6]]

    if specific_id:
        where_clause = "items.id = ?"
        where_params: list = [specific_id]
    else:
        where_clause = f"items.category_id IN ({cats_ph}) AND items.is_reprocessed = 0"
        where_params = list(VIDEO_CATEGORY_IDS)
    if not force_all:
        where_clause += f"""
          AND (items.is_metadata_fixed = 0 OR items.kp_id IS NULL OR items.kp_id = '' OR items.imdb_id IS NULL OR items.imdb_id = '')
          AND (
            items.poster_url   IS NULL OR items.poster_url   = '' OR
            items.description  IS NULL OR items.description  = '' OR
            items.imdb_id      IS NULL OR items.imdb_id      = '' OR
            items.kp_id        IS NULL OR items.kp_id        = '' OR
            items.imdb_rating  IS NULL OR items.imdb_rating  = 0  OR
            {garbage_like}
          )
        """
        where_params.extend(garbage_params)

    tmdb = TMDBClient()

    mode_str = "ПОЛНОЕ ОБНОВЛЕНИЕ" if force_all else "УМНАЯ ПРОВЕРКА"
    logger.info(f"=== ЗАПУСК: {mode_str} ===")

    total_to_process = db.get_items_count(where_clause, where_params, conn=conn)
    logger.info(f"Найдено элементов для обработки: {total_to_process}")

    total_updated = 0
    total_fixed_all = 0

    batch_size = int(REPROCESS_BATCH_SIZE)
    while True:
        cursor = conn.cursor()
        cursor.execute(
            f"""
            SELECT items.id, items.title, items.year,
                   items.poster_url, items.description,
                    items.kp_id, items.imdb_id,
                   items.imdb_rating, items.kp_rating,
                   MIN(releases.rutor_id) as rutor_id,
                   items.category_id
            FROM items
            LEFT JOIN releases ON items.id = releases.item_id
            WHERE {where_clause}
            GROUP BY items.id
            ORDER BY items.id DESC
            LIMIT ?
            """,
            [*where_params, batch_size],
        )
        items = [dict(r) for r in cursor.fetchall()]

        if not items:
            report_progress(total_to_process, total_to_process)
            if total_updated == 0:
                logger.info("💎 Все карточки полностью заполнены!")
            else:
                logger.info(
                    f"\n✅ Обработка завершена. Всего обновлено: {total_updated}, полностью заполнено: {total_fixed_all}"
                )
            break

        logger.info(f"\n📦 Обработка пакета из {len(items)} карточек...")
        for idx, row in enumerate(items, 1):
            if should_stop(STATUS_KEY):
                logger.info("[STOP] Graceful shutdown requested.")
                conn.close()
                return

            report_progress(total_updated + idx, total_to_process)
            item_id = row["id"]

            old_title = row["title"] or ""
            year = row["year"]
            rutor_id = row["rutor_id"]
            kp_id = row["kp_id"] or ""
            imdb_id = row["imdb_id"] or ""
            poster = row["poster_url"] or ""
            desc = row["description"] or ""
            imdb_rating = row["imdb_rating"] or 0.0
            kp_rating = row["kp_rating"] or 0.0
            final_title = old_title

            if tmdb.is_limited:
                logger.warning("\n[!] Лимит TMDB исчерпан. Остановка процесса.")
                conn.close()
                return

            needs = []
            if not kp_id:
                needs.append("KP ID")
            if not imdb_id:
                needs.append("IMDb ID")
            if not poster:
                needs.append("постер")
            if not desc:
                needs.append("описание")
            if not imdb_rating:
                needs.append("рейтинг IMDb")
            if has_garbage_title(old_title):
                needs.append("чистое название")

            if needs:
                logger.info(f"  📋 Нужно: {', '.join(needs)}")

            changes = []

            if not kp_id or not imdb_id:
                # Only probe the first 3 releases. An item can accumulate
                # dozens of releases over its lifetime; if neither KP nor
                # IMDb id is present in the first three Rutor pages, the
                # rest almost never have it either, and the additional
                # round-trips push the per-item budget into seconds.
                rels = db.get_rutor_ids_for_item(item_id, conn=conn)[:3]
                for rel_idx, rel_rutor_id in enumerate(rels):
                    if kp_id and imdb_id:
                        break
                    try:
                        time.sleep(0.3)
                        MIRROR = RUTOR_MIRROR
                        logger.info(f"  🔍 Рутор (1.1): {MIRROR}/torrent/{rel_rutor_id}")
                        from proxy_manager import proxy_manager

                        proxies = proxy_manager.get_requests_proxies("rutor") or {}
                        resp = requests.get(
                            f"{MIRROR}/torrent/{rel_rutor_id}", timeout=20, proxies=proxies
                        )
                        if resp.status_code == 200:
                            if not kp_id:
                                m = re.search(
                                    r"(?:rating\.)?kinopoisk\.ru/(?:rating/)?(\d+)\.gif", resp.text
                                )
                                if not m:
                                    m = re.search(
                                        r"kinopoisk\.ru/(?:level/1/)?(?:film/|series/)+(\d+)",
                                        resp.text,
                                    )
                                if not m:
                                    m = re.search(r"film/(\d+)", resp.text)
                                if m:
                                    kp_id = m.group(1)
                                    changes.append(f"    ✅ Нашел KP ID: {kp_id}")
                            if not imdb_id:
                                m = re.search(r"imdb\.com/title/(tt\d+)", resp.text)
                                if m:
                                    imdb_id = m.group(1)
                                    changes.append(f"    ✅ Нашел IMDb ID: {imdb_id}")
                        else:
                            logger.warning(f"    ⚠️ Ошибка Rutor: {resp.status_code}")
                    except Exception as e:
                        logger.warning(f"    ⚠️ Ошибка глубокого поиска: {e}")

            tmdb_data = None
            if not poster or not desc or not imdb_rating or has_garbage_title(old_title):
                try:
                    if imdb_id:
                        tmdb_data = tmdb.find_by_imdb_id(imdb_id)
                    if not tmdb_data:
                        t_parts = old_title.split(" / ")
                        ru_part = re.sub(r"\s*\(\d{4}\)\s*", "", t_parts[0].split("/")[0]).strip()
                        en_part = None
                        if len(t_parts) > 1:
                            en_part = re.sub(
                                r"\s*\(\d{4}\)\s*", "", t_parts[1].split("/")[0]
                            ).strip()
                        search_primary = en_part or ru_part
                        search_alt = ru_part if en_part else None
                        tmdb_data = tmdb.search_movie(search_primary, year, alt_title=search_alt)
                    if tmdb_data:
                        logger.info("  ✅ Данные в TMDB получены")
                        if not imdb_id and tmdb_data.get("imdb_id"):
                            imdb_id = tmdb_data["imdb_id"]
                            changes.append(f"    🎯 Нашел IMDb ID (через TMDB): {imdb_id}")
                        if not poster and tmdb_data.get("poster_url"):
                            poster = tmdb_data["poster_url"]
                            changes.append("    ✅ Постер добавлен")
                        if not desc and tmdb_data.get("description"):
                            desc = tmdb_data["description"]
                            changes.append("    ✅ Описание добавлено")
                        if tmdb_data.get("title"):
                            new_ru = tmdb_data["title"]
                            new_orig = tmdb_data.get("original_title") or tmdb_data.get("title")
                            if new_ru.lower() != new_orig.lower():
                                new_title = f"{new_ru} / {new_orig}"
                            else:
                                new_title = new_ru
                            if year:
                                new_title += f" ({year})"
                            from app_core import clean_title_year_duplicates

                            new_title = clean_title_year_duplicates(new_title)
                            if new_title != old_title:
                                final_title = new_title
                                changes.append(f"    ✨ Название обновлено: {final_title}")
                            if new_orig:
                                original_title = new_orig
                    else:
                        logger.info("  ⚠️ TMDB: ничего не найдено")
                except Exception as e:
                    logger.warning(f"    ⚠️ Ошибка TMDB: {e}")

            for c in changes:
                logger.info(c)

            all_ok = (
                poster
                and desc
                and kp_id
                and imdb_id
                and (kp_rating > 0)
                and not has_garbage_title(final_title)
            )
            is_fixed = 1 if all_ok else 0

            if is_fixed:
                logger.info("  ✅ Карточка полностью заполнена.")
                total_fixed_all += 1
            elif not changes:
                logger.info("  💎 Изменений не найдено.")
                if kp_id and imdb_id:
                    is_fixed = 1
                    total_fixed_all += 1

            try:
                db.fill_item_metadata(
                    item_id,
                    conn=conn,
                    title=final_title,
                    poster_url=poster,
                    description=desc,
                    imdb_id=imdb_id,
                    kp_id=kp_id,
                    kp_rating=kp_rating,
                    imdb_rating=imdb_rating,
                    original_title=tmdb_data.get("original_title") if tmdb_data else None,
                )
                db.update_item(item_id, conn=conn, is_metadata_fixed=is_fixed, is_reprocessed=1)
                conn.commit()
                total_updated += 1
            except Exception:
                logger.info(f"  🔗 Обнаружен дубликат для '{final_title}'. Сливаю карточки...")
                existing_id = db.find_duplicate_item_id(
                    final_title, year, row["category_id"], item_id, conn=conn
                )
                if existing_id:
                    db.reassign_releases(item_id, existing_id, conn=conn)
                    db.update_item(existing_id, conn=conn, is_reprocessed=1)
                    db.delete_item(item_id, conn=conn)
                    conn.commit()
                    logger.info(f"  ✅ Успешно слито с карточкой ID {existing_id}")
                else:
                    db.update_item(item_id, conn=conn, is_reprocessed=1)
                    conn.commit()

            time.sleep(REPROCESS_REQUEST_DELAY)

        if specific_id:
            break

    conn.close()


if __name__ == "__main__":
    import sys

    force = "--force" in sys.argv
    specific_id = None
    if "--id" in sys.argv:
        try:
            idx = sys.argv.index("--id")
            specific_id = int(sys.argv[idx + 1])
        except Exception:
            pass

    reprocess_all(force, specific_id)
