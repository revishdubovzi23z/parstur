import re
import sys
import time

from db import Database
from kinopoisk_client import KinopoiskClient
from logging_config import setup_logging
from poiskkino_client import PoiskKinoClient
from script_utils import clear_stop_flag, load_config, should_stop
from settings import settings

_config = load_config()
FIX_BATCH_SIZE = _config.get("fix_posters", {}).get("batch_size", 300)
FIX_REQUEST_DELAY = _config.get("fix_posters", {}).get("request_delay", 0.5)
STATUS_KEY = settings.status_key
logger = setup_logging("parsclode.fix", "fix_log.txt")


def fix_metadata(api_type="tech"):
    clear_stop_flag(STATUS_KEY)
    if api_type == "tech":
        client = KinopoiskClient()
        api_name = "Кинопоиск (Legacy/Tech)"
    else:
        client = PoiskKinoClient()
        api_name = "PoiskKino (Dev)"

    logger.info(f"\n\n=== ЗАПУСК: {api_name} ({time.strftime('%H:%M:%S')}) ===")
    check_col = f"checked_{api_type}"

    db = Database()
    conn = db.get_connection()

    items = db.get_items_needing_metadata(check_col, FIX_BATCH_SIZE, conn=conn)

    logger.info(f"Найдено {len(items)} релизов с пропусками данных.")
    if not items:
        logger.info("Все данные в порядке!")
    else:
        for idx, item_data in enumerate(items, 1):
            if should_stop(STATUS_KEY):
                logger.info("\n[STOP] Graceful shutdown requested.")
                break

            if client.is_limited:
                logger.warning("\n[!] Лимит API исчерпан. Остановка.")
                break

            item_id = item_data["id"]
            title = item_data["title"]
            year = item_data["year"]
            kp_id = item_data["kp_id"]
            imdb_id = item_data["imdb_id"]

            if not year or year == 0:
                rel_title = db.get_release_torrent_title(item_id, conn=conn)
                if rel_title:
                    year_match = re.search(r"\((\d{4})\)", rel_title)
                    if year_match:
                        year = int(year_match.group(1))
                        db.fill_item_metadata(item_id, conn=conn, year=year)

            search_title = title.split(" / ")[0].split("/")[0]
            search_title = re.sub(r"\(.*?\)", "", search_title)
            search_title = re.sub(r"\[.*?\]", "", search_title)
            tags = [
                "SATRip",
                "Web-DL",
                "BDRip",
                "1080p",
                "720p",
                "4K",
                "HDR",
                "S01",
                "S02",
                "L1",
                "L2",
            ]
            for tag in tags:
                search_title = re.sub(f"(?i){tag}", "", search_title)
            # `needed` collects the metadata columns this item is still
            # missing so the log line below can show what we are about
            # to refetch. The list is rebuilt per iteration; earlier
            # revisions of this loop initialised it outside the loop,
            # which masked a NameError on the first row where IMDb was
            # already populated.
            needed: list[str] = []
            if not item_data["kp_rating"]:
                needed.append("Кинопоиск")
            if not item_data["imdb_rating"]:
                needed.append("IMDb")
            if not item_data["poster_url"]:
                needed.append("постер")
            if not item_data["description"]:
                needed.append("описание")
            logger.info(f"\n[{idx}/{len(items)}] 🎬 {search_title} ({year})")
            if needed:
                logger.info(f"  📋 Нужно: {', '.join(needed)}")

            data = None
            if kp_id and hasattr(client, "get_by_id"):
                logger.info(f"  🎯 Прямой запрос по ID: {kp_id}")
                data = client.get_by_id(kp_id)

            if not data:
                logger.info(f"  🔍 Поиск через API по названию: {search_title}...")
                data = client.search_movie(search_title, year)

            if data:
                kp_rating = data.get("kp_rating", 0.0)
                imdb_rating = data.get("imdb_rating", 0.0)
                new_poster = data.get("poster_url", "")
                desc = data.get("description", "")
                new_imdb_id = data.get("imdb_id", "")

                db.fill_item_metadata(
                    item_id,
                    conn=conn,
                    kp_rating=kp_rating,
                    imdb_rating=imdb_rating,
                    poster_url=new_poster,
                    description=desc,
                    imdb_id=new_imdb_id,
                )

                check_item = db.get_item(item_id, conn=conn)
                if (
                    check_item
                    and check_item["kp_rating"] > 0
                    and check_item["imdb_rating"] > 0
                    and check_item["poster_url"]
                    and check_item["description"]
                ):
                    db.update_item(item_id, conn=conn, is_metadata_fixed=1)

                conn.commit()

                found_parts = []
                if kp_rating > 0 and (not item_data["kp_rating"]):
                    found_parts.append(f"✨ Рейтинг КП: {kp_rating}")
                if imdb_rating > 0 and (not item_data["imdb_rating"]):
                    found_parts.append(f"✨ Рейтинг IMDb: {imdb_rating}")
                if new_poster and (not item_data["poster_url"]):
                    found_parts.append("🖼️ Постер добавлен")
                if desc and (not item_data["description"]):
                    found_parts.append("📝 Описание получено")

                if found_parts:
                    for p in found_parts:
                        logger.info(f"    {p}")
                    logger.info("  ✅ Данные обновлены")
                else:
                    logger.info("  💎 Новых данных не найдено.")
            else:
                logger.info("  ❌ API не вернул данных.")

            if not client.is_limited:
                db.mark_checked(item_id, api_type, conn=conn)
                conn.commit()

            time.sleep(FIX_REQUEST_DELAY)

    total, no_p, no_r = db.get_db_stats(conn=conn)
    logger.info("\n=== ИТОГОВАЯ СТАТИСТИКА ===")
    logger.info(f"Всего видео: {total}")
    logger.info(f"Осталось БЕЗ постеров: {no_p}")
    logger.info(f"Осталось БЕЗ оценок: {no_r}")
    logger.info("===========================\n")

    conn.close()


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "tech"
    fix_metadata(mode)
