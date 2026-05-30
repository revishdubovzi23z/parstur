import json
import os
import re
import sys
import time
import unicodedata

import requests

from app_core import RUTOR_CATEGORIES, VIDEO_CATEGORY_IDS, normalize_title
from db import Database
from kinopoisk_client import KinopoiskClient
from logging_config import setup_logging
from rutor_parser import RutorParser
from script_utils import (
    clear_checkpoint,
    clear_stop_flag,
    load_checkpoint,
    load_config,
    save_checkpoint,
    should_stop,
)
from settings import settings
from tmdb_client import TMDBClient

logger = setup_logging("parsclode.sync", settings.log_file_path)


def report_progress(current, total, status_key):
    try:
        p_file = os.path.join(settings.app_data_dir, f"progress_{status_key}.json")
        with open(p_file, "w") as f:
            json.dump({"current": current, "total": total}, f)
    except Exception:
        pass


MIN_YEAR = settings.sync_min_year
MAX_YEAR = settings.sync_max_year
STATUS_KEY = settings.status_key

_config = load_config()
SYNC_PAGE_DEPTH = _config.get("sync", {}).get("page_depth", 20)
SYNC_STOP_THRESHOLD = _config.get("sync", {}).get("stop_threshold", 5)
SYNC_DEFAULT_PERIOD_DAYS = _config.get("sync", {}).get("default_period_days", 30)
SYNC_REQUEST_DELAY = _config.get("sync", {}).get("request_delay", 0.3)


def parse_rutor_date(date_str):
    """Parse a Rutor 'date' cell.

    Returns an ISO 8601 string on success, or None when the input can't
    be parsed. The previous implementation fell back to datetime.now()
    on any error, which made unparseable releases look brand-new and
    poisoned the cursor used by subsequent syncs (see par2_code_review.md
    §3.3). Callers now decide explicitly what to do with None.
    """
    from datetime import datetime, timedelta

    if not date_str:
        return None

    now = datetime.now()

    MONTHS = {
        "Янв": 1,
        "Фев": 2,
        "Мар": 3,
        "Апр": 4,
        "Май": 5,
        "Июн": 6,
        "Июл": 7,
        "Авг": 8,
        "Сен": 9,
        "Окт": 10,
        "Ноя": 11,
        "Дек": 12,
    }

    date_str = date_str.strip()
    try:
        if "Сегодня" in date_str:
            t_str = date_str.replace("Сегодня", "").strip()
            h, m = map(int, t_str.split(":"))
            return now.replace(hour=h, minute=m, second=0, microsecond=0).isoformat(sep=" ")

        if "Вчера" in date_str:
            t_str = date_str.replace("Вчера", "").strip()
            h, m = map(int, t_str.split(":"))
            yesterday = now - timedelta(days=1)
            return yesterday.replace(hour=h, minute=m, second=0, microsecond=0).isoformat(sep=" ")

        parts = date_str.split()
        if len(parts) >= 3:
            day = int(parts[0])
            month = MONTHS.get(parts[1], 1)
            year = int(parts[2])
            if year < 100:
                year += 2000
            return datetime(year, month, day).isoformat(sep=" ")
    except Exception:
        return None

    return None


CATEGORIES_TO_SYNC = [
    {"id": 1, "use_tmdb": True},
    {"id": 4, "use_tmdb": True},
    {"id": 5, "use_tmdb": True},
    {"id": 16, "use_tmdb": True},
    {"id": 6, "use_tmdb": False},
    {"id": 7, "use_tmdb": True},
    {"id": 10, "use_tmdb": False},
    {"id": 15, "use_tmdb": False},
    {"id": 8, "use_tmdb": False},
    {"id": 12, "use_tmdb": False},
]

for c in CATEGORIES_TO_SYNC:
    c["name"] = RUTOR_CATEGORIES.get(c["id"], f"Unknown {c['id']}")


def deduplicate_releases(raw_list):
    groups = {}
    for r in raw_list:
        key = (normalize_title(r["parsed_title"]), r["year"])
        if key not in groups:
            groups[key] = {
                "display_title": r["parsed_title"],
                "year": r["year"],
                "releases": [],
            }
        groups[key]["releases"].append(r)
    return groups


def run_sync(mode="video", manual_min_date=None):
    logger = setup_logging("parsclode.sync", "sync_log.txt")
    db = Database()
    parser = RutorParser()
    tmdb = TMDBClient()
    kinopoisk = KinopoiskClient()

    status_key = STATUS_KEY
    checkpoint = load_checkpoint(status_key)
    completed_cats = checkpoint.get("completed_categories", []) if checkpoint else []
    resume_cat_id = checkpoint.get("current_category_id") if checkpoint else None
    resume_page = checkpoint.get("current_page", 0) if checkpoint else None

    if checkpoint:
        logger.info(
            f"[RESUME] Found checkpoint: completed cats {completed_cats}, resume at cat {resume_cat_id} page {resume_page}"
        )

    clear_stop_flag(status_key)

    if manual_min_date:
        target_date = manual_min_date
        logger.info(f"=== ЗАПУСК ПАРСИНГА (Режим: {mode}, РУЧНАЯ ДАТА: {target_date}) ===")
    else:
        target_date = db.get_last_release_date()
        logger.info(f"=== ЗАПУСК ПАРСИНГА (Режим: {mode}, Цель: {target_date}) ===")

    conn = db.get_connection()
    cursor = conn.cursor()

    for cat in CATEGORIES_TO_SYNC:
        cat_id = cat["id"]
        cat_name = cat["name"]
        use_tmdb = cat["use_tmdb"]

        if mode == "video" and not use_tmdb:
            continue
        if mode == "other" and use_tmdb:
            continue

        if cat_id in completed_cats:
            logger.info(f"\n--- Категория: {cat_name} (ПРОПУСК: уже обработана) ---")
            continue

        current_target = (
            manual_min_date if manual_min_date else db.get_last_release_date(category_id=cat_id)
        )
        logger.info(f"\n--- Категория: {cat_name} (Ищем новее {current_target}) ---")

        page_start = resume_page if cat_id == resume_cat_id else 0

        all_raw_releases = []
        empty_pages_in_a_row = 0
        for page in range(page_start, SYNC_PAGE_DEPTH):
            if should_stop(status_key):
                save_checkpoint(
                    status_key,
                    {
                        "completed_categories": completed_cats,
                        "current_category_id": cat_id,
                        "current_page": page,
                    },
                )
                logger.info("[STOP] Graceful shutdown. Checkpoint saved.")
                conn.close()
                return

            raw = parser.get_category_releases(cat_id, page=page)
            if not raw:
                break

            new_ones = []
            for r in raw:
                rutor_dt = parse_rutor_date(r["date_str"])
                # Treat undated releases as "unknown but maybe fresh" —
                # let them through so we don't silently drop legitimate
                # items because of a parser glitch on Rutor's side.
                if rutor_dt is not None and rutor_dt < current_target:
                    continue
                if cat_id in VIDEO_CATEGORY_IDS:
                    ry = r.get("year")
                    if ry:
                        if ry < MIN_YEAR or ry > MAX_YEAR:
                            continue
                new_ones.append(r)

            all_raw_releases.extend(new_ones)
            logger.info(f"  Стр {page}: новых (с учетом фильтров) {len(new_ones)}")
            # Stop only after two consecutive pages produced zero new
            # releases. The old heuristic (len(new_ones) < len(raw) -
            # SYNC_STOP_THRESHOLD) broke whenever a page had exactly
            # `threshold` old releases — it would terminate even though
            # the next page might still have new ones.
            if len(new_ones) == 0:
                empty_pages_in_a_row += 1
                if empty_pages_in_a_row >= 2:
                    break
            else:
                empty_pages_in_a_row = 0
            time.sleep(SYNC_REQUEST_DELAY)

        if not all_raw_releases:
            report_progress(1, 1, status_key)
            completed_cats.append(cat_id)
            continue

        unique_movies = deduplicate_releases(all_raw_releases)
        total_m = len(unique_movies)
        for idx, (key, movie_data) in enumerate(unique_movies.items(), 1):
            if should_stop(status_key):
                save_checkpoint(
                    status_key,
                    {
                        "completed_categories": completed_cats,
                        "current_category_id": cat_id,
                        "current_page": page_start + SYNC_PAGE_DEPTH,
                    },
                )
                logger.info("[STOP] Graceful shutdown during item processing. Checkpoint saved.")
                conn.close()
                return

            report_progress(idx, total_m, status_key)
            clean_t_key, year = key
            display_title = movie_data["display_title"]

            cursor.execute(
                "SELECT id, title FROM items WHERE year=? AND category_id=?",
                (year, cat_id),
            )
            potential_matches = cursor.fetchall()

            item_id = None
            for p_id, p_title in potential_matches:
                if normalize_title(p_title) == clean_t_key:
                    item_id = p_id
                    break

            is_new_item = False
            if not item_id:
                for _rel in movie_data["releases"]:
                    _existing_item_id = db.get_release_item_id(_rel["rutor_id"], conn=conn)
                    if _existing_item_id:
                        _owner = db.get_item(_existing_item_id, conn=conn)
                        if _owner:
                            item_id = _existing_item_id
                            logger.info(
                                f"\n  🔗 Найден существующий item {item_id} ({_owner['title']}) по релизу {_rel['rutor_id']}"
                            )
                            break
                        else:
                            logger.info(
                                f"  🔗 Релиз {_rel['rutor_id']} осиротевший (item {_existing_item_id} удалён)"
                            )

            if not item_id:
                is_new_item = True
                logger.info(f"\n[НОВЫЙ] 🎬 {display_title} ({year})")
                rutor_kp_id = None
                rutor_imdb_id = None

                if cat_id in [1, 4, 5, 16, 7]:
                    for rel_idx, rel in enumerate(movie_data["releases"]):
                        if rutor_kp_id and rutor_imdb_id:
                            break
                        try:
                            rel_url = f"{parser.mirror}/torrent/{rel['rutor_id']}"
                            logger.info(f"  🔍 Рутор (1.1): {rel_url}")
                            resp = requests.get(rel_url, timeout=20)
                            if resp.status_code == 200:
                                from bs4 import BeautifulSoup

                                soup = BeautifulSoup(resp.text, "html.parser")
                                h1 = soup.find("h1")
                                if h1:
                                    full_h1_title = h1.text.strip()
                                    if "Раздача не существует" not in full_h1_title:
                                        display_title = parser.clean_display_title(full_h1_title)
                                        logger.info(f"    ✨ Название уточнено: {display_title}")

                                if not rutor_kp_id:
                                    kp_match = re.search(
                                        r"(?:rating\.)?kinopoisk\.ru/(?:rating/)?(\d+)\.gif",
                                        resp.text,
                                    )
                                    if not kp_match:
                                        kp_match = re.search(
                                            r"kinopoisk\.ru/(?:level/1/)?(?:film/|series/)+(\d+)",
                                            resp.text,
                                        )
                                    if kp_match:
                                        rutor_kp_id = kp_match.group(1)
                                        logger.info(f"    ✅ Нашел KP ID: {rutor_kp_id}")

                                if not rutor_imdb_id:
                                    imdb_match = re.search(r"imdb\.com/title/(tt\d+)", resp.text)
                                    if imdb_match:
                                        rutor_imdb_id = imdb_match.group(1)
                                        logger.info(f"    ✅ Нашел IMDb ID: {rutor_imdb_id}")
                        except Exception as e:
                            logger.warning(f"    ⚠️ Ошибка парсинга страницы: {e}")

                        if len(movie_data["releases"]) > 1:
                            time.sleep(0.4)

                    if not rutor_kp_id or not rutor_imdb_id:
                        try:
                            search_term = display_title.split(" / ")[0].split("/")[0].strip()
                            search_term = re.sub(r"\(.*?\)", "", search_term)
                            search_term = re.sub(r"\[.*?\]", "", search_term).strip()
                            # Заменяем тире и дефисы на пробелы для расширения результатов поиска
                            search_term = search_term.replace("—", " ").replace("-", " ").strip()
                            search_term = re.sub(r"\s+", " ", search_term)

                            logger.info(f"  🔍 Рутор (1.2 Глубокий поиск): {search_term}")
                            search_results = parser.search_releases(search_term)
                            matches = [
                                res
                                for res in search_results
                                if res.get("year") and year and abs(res.get("year") - year) <= 1
                            ]

                            if matches:
                                logger.info(f"    🔎 Найдено в архиве: {len(matches)}. Проверяю...")
                                for m_idx, m in enumerate(matches[:3]):
                                    if rutor_kp_id and rutor_imdb_id:
                                        break
                                    rid = m["rutor_id"]
                                    time.sleep(0.4)
                                    resp = requests.get(
                                        f"{parser.mirror}/torrent/{rid}", timeout=20
                                    )
                                    if resp.status_code == 200:
                                        if not rutor_kp_id:
                                            m_kp = re.search(
                                                r"(?:rating\.)?kinopoisk\.ru/(?:rating/)?(\d+)\.gif",
                                                resp.text,
                                            )
                                            if not m_kp:
                                                m_kp = re.search(
                                                    r"kinopoisk\.ru/(?:level/1/)?(?:film/|series/)+(\d+)",
                                                    resp.text,
                                                )
                                            if not m_kp:
                                                m_kp = re.search(r"film/(\d+)", resp.text)
                                            if m_kp:
                                                rutor_kp_id = m_kp.group(1)
                                                logger.info(
                                                    f"      ✅ Нашел KP ID в архиве: {rutor_kp_id}"
                                                )
                                        if not rutor_imdb_id:
                                            m_imdb = re.search(
                                                r"imdb\.com/title/(tt\d+)", resp.text
                                            )
                                            if m_imdb:
                                                rutor_imdb_id = m_imdb.group(1)
                                                logger.info(
                                                    f"      ✅ Нашел IMDb ID в архиве: {rutor_imdb_id}"
                                                )
                            else:
                                logger.info("    ⚠️ В архиве Рутора совпадений не найдено.")
                        except Exception as e:
                            logger.warning(f"    ⚠️ Ошибка глубокого поиска: {e}")

                poster = ""
                desc = ""
                imdb_id = rutor_imdb_id
                imdb_rating = 0.0
                clean_display_title = display_title
                tmdb_id = None

                if use_tmdb:
                    if tmdb.is_limited:
                        logger.error(
                            "[LIMIT_EXHAUSTED] Лимит запросов TMDB исчерпан! Приостановка синхронизации..."
                        )
                        conn.commit()
                        save_checkpoint(
                            status_key,
                            {
                                "completed_categories": completed_cats,
                                "current_category_id": cat_id,
                                "current_page": page_start,
                            },
                        )
                        conn.close()
                        sys.exit(2)

                    tmdb_data = None
                    if imdb_id:
                        logger.info(f"  🔍 TMDB (2.1 по ID): {imdb_id}")
                        tmdb_data = tmdb.find_by_imdb_id(imdb_id)
                    if not tmdb_data and not tmdb.is_limited:
                        t_parts = display_title.split(" / ")
                        ru_part = re.sub(r"\s*\(\d{4}\)\s*", "", t_parts[0].split("/")[0]).strip()
                        en_part = None
                        if len(t_parts) > 1:
                            en_part = re.sub(
                                r"\s*\(\d{4}\)\s*", "", t_parts[1].split("/")[0]
                            ).strip()
                        search_primary = en_part or ru_part
                        search_alt = ru_part if en_part else None
                        logger.info(
                            f"  🔍 TMDB (2.2 поиск): {search_primary}"
                            + (f" / alt:{search_alt}" if search_alt else "")
                            + f" ({year})"
                        )
                        tmdb_data = tmdb.search_movie(search_primary, year, alt_title=search_alt)

                    if tmdb.is_limited:
                        logger.error(
                            "[LIMIT_EXHAUSTED] Лимит запросов TMDB исчерпан во время запроса! Приостановка синхронизации..."
                        )
                        conn.commit()
                        save_checkpoint(
                            status_key,
                            {
                                "completed_categories": completed_cats,
                                "current_category_id": cat_id,
                                "current_page": page_start,
                            },
                        )
                        conn.close()
                        sys.exit(2)

                    if tmdb_data:
                        poster = tmdb_data.get("poster_url", "")
                        desc = tmdb_data.get("description", "")
                        tmdb_id = tmdb_data.get("tmdb_id")
                        if tmdb_data.get("title") and tmdb_data.get("original_title"):
                            new_ru = tmdb_data["title"]
                            new_orig = tmdb_data["original_title"]
                            if new_ru.lower() != new_orig.lower():
                                clean_display_title = f"{new_ru} / {new_orig}"
                            else:
                                clean_display_title = new_ru
                            if year and str(year) not in clean_display_title:
                                clean_display_title += f" ({year})"
                            from app_core import clean_title_year_duplicates

                            clean_display_title = clean_title_year_duplicates(clean_display_title)
                        if not imdb_id:
                            imdb_id = tmdb_data.get("imdb_id", "")
                        logger.info(
                            f"    🎯 TMDB: данные получены (Постер: {'✅' if poster else '❌'})"
                        )
                    else:
                        logger.info("    ⚠️ TMDB: ничего не найдено.")

                title_norm = ""
                search_names = []
                t_clean = re.sub(r"\(.*?\)", "", clean_display_title)
                t_clean = re.sub(r"\[.*?\]", "", t_clean)
                t_clean = re.sub(
                    r"(?i)\b(UHD|BDRemu[xх]|BDRip|Web-DL|Blu-Ray|Remux|1080p|720p|4K|HDR|HEVC|SATRip)\b",
                    "",
                    t_clean,
                )
                parts = [p.strip() for p in t_clean.split("/") if p.strip()]
                for p in parts:
                    for pp in p.split(" / "):
                        if pp.strip():
                            search_names.append(unicodedata.normalize("NFC", pp.strip()).lower())

                search_names = list(set(search_names))
                if search_names:
                    title_norm = search_names[0]

                existing_id = db.find_existing_item(
                    kp_id=rutor_kp_id,
                    imdb_id=imdb_id,
                    title_norm=title_norm,
                    year=year,
                    category_id=cat_id,
                    conn=conn,
                )

                if existing_id:
                    item_id = existing_id
                    is_new_item = False
                    db.fill_item_metadata(
                        item_id,
                        conn=conn,
                        poster_url=poster,
                        description=desc,
                        imdb_id=imdb_id,
                        kp_id=rutor_kp_id,
                        imdb_rating=imdb_rating,
                        title=title_norm if title_norm else None,
                        tmdb_id=tmdb_id,
                    )
                    logger.info(f"  🔗 НАЙДЕН ДУБЛЬ: {display_title} ({year}) -> id={item_id}")
                else:
                    item_id = db.insert_item(
                        {
                            "title": clean_display_title,
                            "year": year,
                            "category_id": cat_id,
                            "poster_url": poster,
                            "description": desc,
                            "imdb_id": imdb_id,
                            "kp_id": rutor_kp_id,
                            "imdb_rating": imdb_rating,
                            "kp_rating": 0,
                            "is_metadata_fixed": 0,
                            "title_norm": title_norm,
                            "tmdb_id": tmdb_id,
                        },
                        conn=conn,
                    )

                for sn in search_names:
                    db.insert_search_name(item_id, sn, conn=conn)

                if existing_id:
                    pass
                else:
                    logger.info(f"  ➕ ДОБАВЛЕН: {display_title} ({year})")

            added_any = False
            for rel in movie_data["releases"]:
                if db.release_exists_by_rutor_id(rel["rutor_id"], conn=conn):
                    _owner_id = db.get_release_item_id(rel["rutor_id"], conn=conn)
                    if _owner_id and _owner_id != item_id:
                        _owner = db.get_item(_owner_id, conn=conn)
                        if _owner:
                            logger.info(
                                f"    ⚠️ Релиз {rel['rutor_id']} уже у item {_owner_id} ({_owner['title']})"
                            )
                        else:
                            logger.info(
                                f"    ⚠️ Релиз {rel['rutor_id']} осиротевший (item {_owner_id} удалён)"
                            )
                    if db.reassign_release_if_orphan(rel["rutor_id"], item_id, conn=conn):
                        logger.info(
                            f"    └─ Осиротевший релиз {rel['rutor_id']} переназначен на item {item_id}"
                        )
                    continue

                if not is_new_item and not added_any:
                    logger.info(
                        f"  🔗 Добавлен новый релиз к существующему фильму: {display_title}"
                    )
                    added_any = True

                rel_date = parse_rutor_date(rel["date_str"])
                if rel_date is None:
                    from datetime import datetime

                    rel_date = datetime.now().isoformat(sep=" ")
                    logger.warning(
                        f"    ⚠️  Не удалось разобрать дату '{rel.get('date_str')}', использую текущее время."
                    )

                db.insert_release(
                    {
                        "item_id": item_id,
                        "rutor_id": rel["rutor_id"],
                        "torrent_title": rel["full_title"],
                        "quality": rel["quality"],
                        "date_added": rel_date,
                        "magnet": rel["magnet"],
                        "link": rel["link"],
                    },
                    conn=conn,
                )
                logger.info(
                    f"    └─ Новая раздача: [{rel['quality']}] {rel['full_title'][:50]}... ({rel_date})"
                )

        conn.commit()
        completed_cats.append(cat_id)
        save_checkpoint(
            status_key,
            {
                "completed_categories": completed_cats,
                "current_category_id": None,
                "current_page": 0,
            },
        )

    conn.close()
    clear_checkpoint(status_key)
    logger.info("\n=== Готово! ===")


if __name__ == "__main__":
    from logging_config import setup_logging

    logger = setup_logging("parsclode.sync")
    mode = sys.argv[1] if len(sys.argv) > 1 else "video"
    manual_min_date = None

    if len(sys.argv) > 2:
        try:
            val = sys.argv[2]
            if "-" in val and len(val) > 5:
                manual_min_date = val
            else:
                temp_year = int(val)
                if temp_year > 0:
                    MIN_YEAR = temp_year
                    logger.info(f"Переопределен MIN_YEAR: {MIN_YEAR}")
        except Exception:
            pass
    if len(sys.argv) > 3:
        try:
            val = sys.argv[3]
            if "-" in val and len(val) > 5:
                manual_min_date = val
            else:
                temp_year = int(val)
                if temp_year > 0:
                    MAX_YEAR = temp_year
                    logger.info(f"Переопределен MAX_YEAR: {MAX_YEAR}")
        except Exception:
            pass

    if len(sys.argv) > 4:
        manual_min_date = sys.argv[4]
        logger.info(f"Используется ручная дата начала: {manual_min_date}")

    run_sync(mode, manual_min_date)
