import re
from difflib import SequenceMatcher

from db import Database
from logging_config import setup_logging
from script_utils import load_config

logger = setup_logging("parsclode.cleanup", "cleanup_log.txt")

# 5.11 — tunables loaded from config.json (with the historical
# hard-coded values as fallbacks). Operators can now tune these
# without editing source. Field meanings:
#
#   name_similarity_strict — required when only the name+year
#     heuristic flagged a pair (no shared external id). Also the
#     floor required when KP/IMDb ids match but years differ by
#     more than one.
#   name_similarity_fuzzy — looser bound accepted when KP/IMDb
#     ids match AND years are within one of each other; the shared
#     id is strong evidence and the title comparison is just a
#     sanity check.
#   name_group_warn / name_group_max — soft and hard caps on the
#     O(N^2) name+year merge bundle. A healthy bundle is < 30
#     items; anything above _warn is logged so the operator can
#     investigate normalisation regressions, anything above _max is
#     skipped entirely to keep the script bounded.
_cleanup_cfg = load_config().get("cleanup", {})
NAME_SIMILARITY_STRICT = float(_cleanup_cfg.get("name_similarity_strict", 0.85))
NAME_SIMILARITY_FUZZY = float(_cleanup_cfg.get("name_similarity_fuzzy", 0.6))
NAME_GROUP_WARN = int(_cleanup_cfg.get("name_group_warn", 50))
NAME_GROUP_MAX = int(_cleanup_cfg.get("name_group_max", 200))


def clean_t(t):
    if not t:
        return ""
    t = t.split(" / ")[0].split("/")[0]
    t = re.sub(r"\(?\d{4}\)?", "", t)
    t = re.sub(r"\(.*?\)", "", t)
    t = re.sub(r"\[.*?\]", "", t)
    t = re.sub(r"(?i)SATRip|Web-DL|BDRip|1080p|720p|4K|HDR|HEVC|AVC|MVO|DUB|L1|VO", "", t)
    t = t.replace(".", " ").replace("_", " ")
    t = t.replace("x", "х").replace("X", "Х")
    return t.strip().lower()


def get_item_score(item):
    score = 0
    if item["poster_url"]:
        score += 10
    if item["description"]:
        score += 5
    if item["kp_rating"] and item["kp_rating"] > 0:
        score += 5
    if item["imdb_rating"] and item["imdb_rating"] > 0:
        score += 5
    if item["year"] and item["year"] > 0:
        score += 3
    if item["kp_id"]:
        score += 2
    return score


def merge_duplicates():
    logger = setup_logging("parsclode.cleanup", "cleanup_log.txt")
    logger.info("\n" + "=" * 50)
    logger.info("=== ЗАПУСК ГЛУБОКОЙ ОЧИСТКИ ДУБЛИКАТОВ ===")
    logger.info("=" * 50)

    db = Database()
    conn = db.get_connection()
    cursor = conn.cursor()

    all_items = db.get_items(conn=conn)

    name_groups = {}
    kp_groups = {}
    imdb_groups = {}
    rezka_groups = {}

    for it in all_items:
        t_clean = clean_t(it["title"])
        if t_clean:
            key = (t_clean, it["category_id"])
            if key not in name_groups:
                name_groups[key] = []
            name_groups[key].append(it)

        if it["kp_id"]:
            k = it["kp_id"]
            if k not in kp_groups:
                kp_groups[k] = []
            kp_groups[k].append(it)

        if it["imdb_id"]:
            i = it["imdb_id"]
            if i not in imdb_groups:
                imdb_groups[i] = []
            imdb_groups[i].append(it)

        if it["rezka_url"]:
            r = it["rezka_url"]
            if r not in rezka_groups:
                rezka_groups[r] = []
            rezka_groups[r].append(it)

    merged_ids = set()
    merged_total = 0

    def do_merge(items_to_merge, reason):
        nonlocal merged_total
        if len(items_to_merge) < 2:
            return

        items_to_merge.sort(key=get_item_score, reverse=True)
        master = items_to_merge[0]
        master_id = master["id"]

        if master_id in merged_ids:
            return

        for i in range(1, len(items_to_merge)):
            dup = items_to_merge[i]
            dup_id = dup["id"]
            if dup_id == master_id or dup_id in merged_ids:
                continue

            t1 = clean_t(master["title"])
            t2 = clean_t(dup["title"])
            similarity = SequenceMatcher(None, t1, t2).ratio()

            y_master = master["year"] or 0
            y_dup = dup["year"] or 0
            year_known = y_master and y_dup
            same_year = year_known and abs(y_master - y_dup) <= 1

            if reason in ("Kinopoisk ID", "IMDb ID", "Rezka URL"):
                # External id matches: strong signal, but if years are
                # known and differ by more than one, demand a strict
                # differ by more than one, demand a strict
                # similarity bar before merging — different remakes /
                # spin-offs can share an external id by accident.
                if year_known and not same_year:
                    if similarity < NAME_SIMILARITY_STRICT:
                        logger.info(
                            f"  [ПРОПУСК ({reason}, разные годы)] "
                            f"'{dup['title']}' ({dup['year']}) != "
                            f"'{master['title']}' ({master['year']}) "
                            f"[sim={similarity:.2f}]"
                        )
                        continue
                elif similarity < NAME_SIMILARITY_FUZZY:
                    logger.info(
                        f"  [ПРОПУСК ({reason})] "
                        f"'{dup['title']}' ({dup['year']}) != "
                        f"'{master['title']}' ({master['year']}) "
                        f"[sim={similarity:.2f}]"
                    )
                    continue
            else:
                # Name-and-year grouping: no external id to back us up,
                # so demand the strict bar OR same year + the loose bar.
                if similarity < NAME_SIMILARITY_STRICT and not (
                    same_year and similarity >= NAME_SIMILARITY_FUZZY
                ):
                    continue

            logger.info(
                f"  [СЛИЯНИЕ ({reason})] '{dup['title']}' ({dup['year']}) -> '{master['title']}' ({master['year']})"
            )

            db.reassign_releases(dup_id, master_id, conn=conn)
            db.merge_collection_items(dup_id, master_id, conn=conn)
            db.delete_collection_items_by_item(dup_id, conn=conn)

            if dup["is_ignored"]:
                db.update_item(master_id, conn=conn, is_ignored=1)

            db.reassign_search_names(dup_id, master_id, conn=conn)
            db.delete_search_names_by_item(dup_id, conn=conn)
            db.delete_item(dup_id, conn=conn)
            merged_ids.add(dup_id)
            merged_total += 1

    logger.info("--- Поиск по Kinopoisk ID ---")
    for kp, items in kp_groups.items():
        do_merge(items, "Kinopoisk ID")

    logger.info("--- Поиск по IMDb ID ---")
    for imdb, items in imdb_groups.items():
        do_merge(items, "IMDb ID")

    logger.info("--- Поиск по Rezka URL ---")
    for url, items in rezka_groups.items():
        do_merge(items, "Rezka URL")

    logger.info("--- Поиск по названию и году ---")
    for key, items in name_groups.items():
        active_items = [it for it in items if it["id"] not in merged_ids]
        if len(active_items) < 2:
            continue

        # Anomaly guard (4.2). A name+category bundle this large
        # almost always means clean_t() collapsed too much (e.g.
        # title becomes "the" after stripping every common word) or
        # category_id is wrong upstream. Both are bugs, not real
        # duplicate runs — refuse to do an O(N^2) merge over them.
        if len(active_items) > NAME_GROUP_MAX:
            sample = ", ".join(repr(it["title"]) for it in active_items[:3])
            logger.warning(
                f"[CLEANUP] skipping name group {key!r}: "
                f"size={len(active_items)} > {NAME_GROUP_MAX} (sample: {sample})"
            )
            continue
        if len(active_items) >= NAME_GROUP_WARN:
            logger.info(
                f"[CLEANUP] large name group {key!r}: size={len(active_items)} (proceeding)"
            )

        active_items.sort(key=get_item_score, reverse=True)

        master_list = []
        for it in active_items:
            placed = False
            for master_bundle in master_list:
                master = master_bundle[0]
                y1 = it["year"] or 0
                y2 = master["year"] or 0
                if y1 == y2 or y1 == 0 or y2 == 0 or abs(y1 - y2) <= 1:
                    master_bundle[1].append(it)
                    placed = True
                    break
            if not placed:
                master_list.append((it, []))

        for master, duplicates in master_list:
            if duplicates:
                do_merge([master] + duplicates, "Название/Год")

    conn.commit()
    conn.close()
    logger.info("=" * 50)
    logger.info(f"=== ГОТОВО! Удалено {merged_total} дубликатов. ===")
    logger.info("=" * 50)


if __name__ == "__main__":
    merge_duplicates()
