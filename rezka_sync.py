import asyncio
import json
import os
import random
import re
from collections import defaultdict

import aiohttp
from bs4 import BeautifulSoup

from app_core import clean_title_for_search, normalize_title
from db import Database
from logging_config import setup_logging
from script_utils import (
    clear_checkpoint,
    clear_stop_flag,
    load_checkpoint,
    load_config,
    save_checkpoint,
    should_stop,
)
from settings import settings

_config = load_config()
logger = setup_logging("parsclode.rezka", settings.log_file_path)


# Concurrency precedence (4.4): CLI flag (set in __main__) > env >
# config.yml > 6. The historical default of 3 was chosen to be safe
# against captcha; rezka tolerates 6 simultaneous requests in
# practice — measurable speedup on a 10 k-row catalog without
# triggering the WAF. Operators with a logged-in session can push
# higher via REZKA_CONCURRENCY env or `--concurrency`.
def _initial_concurrency() -> int:
    env = settings.rezka_concurrency
    if env:
        return env
    return int(_config.get("rezka", {}).get("concurrency", 2))


REZKA_CONCURRENCY = _initial_concurrency()
STATUS_KEY = settings.status_key
REZKA_ORIGIN = "https://rezka.ag"
REZKA_SEARCH_URL = f"{REZKA_ORIGIN}/engine/ajax/search.php"
REZKA_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/81.0.4044.138 Safari/537.36"
}
# Default anonymous cookies. The 'hdmbbs=1' flag flips the rezka mirror
# into the lower-friction layout. _login_cookies() (below) overrides
# these with a logged-in session whenever REZKA_EMAIL / REZKA_PASSWORD
# are configured — without that, each request fights the captcha and
# the search endpoint regularly returns empty results.
REZKA_COOKIES = {"hdmbbs": "1"}
REZKA_EMAIL = settings.rezka_email
REZKA_PASSWORD = settings.rezka_password

# 5.11 — score thresholds (previously hard-coded -150 / 70 / -50).
# Operators occasionally need to retune these when rezka changes how
# its search ranks results. Reading them from config.json gives a
# knob without code changes. Field meanings:
#   score_min_with_ids     — minimum score during initial sync when
#                            the source row already has a kp_id or
#                            imdb_id. The negative default lets weak
#                            title matches through because the id is
#                            the real evidence.
#   score_min_without_ids  — the strict floor when no external id is
#                            available; only solid title matches pass.
#   score_min_resync_with_ids — the floor on the periodic re-sync
#                            pass. Tighter than the initial pass
#                            because we already have a verified row
#                            and don't want a stale candidate to
#                            steal the slot.
_REZKA_CFG = _config.get("rezka", {})
SCORE_MIN_WITH_IDS = int(_REZKA_CFG.get("score_min_with_ids", -150))
SCORE_MIN_WITHOUT_IDS = int(_REZKA_CFG.get("score_min_without_ids", 70))
SCORE_MIN_RESYNC_WITH_IDS = int(_REZKA_CFG.get("score_min_resync_with_ids", -50))


def _login_cookies() -> dict:
    """Return cookies for a logged-in HdRezka session, falling back to
    anonymous defaults when no creds are present or login fails. The
    returned dict is safe to feed into aiohttp.ClientSession(cookies=...).
    """
    if not REZKA_EMAIL or not REZKA_PASSWORD:
        return dict(REZKA_COOKIES)
    try:
        from HdRezkaApi.session import HdRezkaSession  # type: ignore

        session = HdRezkaSession(REZKA_ORIGIN)
        session.login(REZKA_EMAIL, REZKA_PASSWORD)
        cookies = dict(REZKA_COOKIES)
        cookies.update(session.cookies)
        return cookies
    except Exception as e:
        logger.warning(
            f"[rezka] login failed ({type(e).__name__}: {e}); falling back to anonymous cookies"
        )
        return dict(REZKA_COOKIES)


def report_progress(current, total, status_key="rezka"):
    try:
        p_file = os.path.join(settings.app_data_dir, f"progress_{status_key}.json")
        with open(p_file, "w") as f:
            json.dump({"current": current, "total": total}, f)
    except Exception:
        pass


def _parse_title(title):
    parts = [p.strip() for p in title.split("/")]
    clean_parts = []
    for p in parts:
        p_clean = re.sub(r"\(.*?\)", "", p).strip()
        if p_clean and len(p_clean) > 1:
            clean_parts.append(p_clean)
    search_queries = []
    for p in clean_parts:
        q = clean_title_for_search(p)
        if q and len(q) > 1:
            search_queries.append(q)
    search_queries = sorted(search_queries, key=lambda x: (not x.isascii(), len(x)), reverse=True)
    return clean_parts, search_queries


def _score_candidates(all_results, clean_parts, year):
    results_with_scores = []
    norm_db_titles = [normalize_title(p) for p in clean_parts]

    for res in all_results:
        res_name = res["title"]
        res_url = res["url"]

        year_match = re.search(r"\((\d{4})\)", res_name)
        if not year_match:
            year_match = re.search(r"-(\d{4})", res_url)
        res_year = int(year_match.group(1)) if year_match else None

        res_clean_name = re.sub(r"\(.*?\)", "", res_name).replace(" / ", "/").strip()
        res_parts = [normalize_title(p.strip()) for p in res_clean_name.split("/")]

        score = 0
        match_found = False
        exact_name_match = False
        for db_norm in norm_db_titles:
            for res_norm in res_parts:
                if db_norm == res_norm:
                    score += 130
                    match_found = True
                    exact_name_match = True
                    break
                elif (len(db_norm) > 4 and db_norm in res_norm) or (
                    len(res_norm) > 4 and res_norm in db_norm
                ):
                    score += 50
                    match_found = True
                else:
                    import difflib

                    ratio = difflib.SequenceMatcher(None, db_norm, res_norm).ratio()
                    if ratio >= 0.85 and len(db_norm) > 6:
                        score += int(ratio * 90)
                        match_found = True
            if match_found:
                break

        if year and res_year:
            diff = abs(year - res_year)
            if diff == 0:
                score += 60
            elif diff == 1:
                score += 50
            elif diff <= 3:
                score -= 40
            else:
                score -= 80

        results_with_scores.append(
            {
                "res": res,
                "score": score,
                "year": res_year,
                "exact_name_match": exact_name_match,
            }
        )

    results_with_scores.sort(key=lambda x: x["score"], reverse=True)
    return results_with_scores


def _extract_kp_imdb_ids(soup):
    import base64
    from urllib.parse import unquote

    kp_id, imdb_id = None, None
    rate_blocks = soup.find_all(class_=re.compile(r"b-post__info_rates"))
    for block in rate_blocks:
        kp_link = block.find("a", href=re.compile(r"kinopoisk\.ru"))
        if kp_link:
            kp_m = re.search(r"/film/(\d+)|/series/(\d+)|/(\d+)/", str(kp_link.get("href", "")))
            if kp_m:
                kp_id = next((g for g in kp_m.groups() if g), None)
        imdb_link = block.find("a", href=re.compile(r"imdb\.com"))
        if imdb_link:
            imdb_m = re.search(r"/title/(tt\d+)", str(imdb_link.get("href", "")))
            if imdb_m:
                imdb_id = imdb_m.group(1)

    links = soup.find_all("a", href=re.compile(r"/help/"))
    for link in links:
        try:
            href = str(link.get("href", ""))
            if "/help/" not in href:
                continue
            b64_url = href.split("/help/")[1].split(".html")[0].strip("/")
            b64_url = unquote(b64_url)
            b64_url = re.sub(r"[^a-zA-Z0-9+/=]", "", b64_url)
            missing_padding = len(b64_url) % 4
            if missing_padding:
                b64_url += "=" * (4 - missing_padding)
            real_url = base64.b64decode(b64_url).decode("utf-8", errors="ignore")
            real_url = unquote(real_url)
            if "kinopoisk.ru" in real_url and not kp_id:
                kp_m = re.search(r"/(?:film|series)/(\d+)|/(\d+)/", real_url)
                if kp_m:
                    kp_id = next((g for g in kp_m.groups() if g), None)
            elif "imdb.com" in real_url and not imdb_id:
                imdb_m = re.search(r"/title/(tt\d+)", real_url)
                if imdb_m:
                    imdb_id = imdb_m.group(1)
        except Exception:
            pass
    return kp_id, imdb_id


def _extract_ratings_from_soup(soup):
    kp_rating, imdb_rating = 0.0, 0.0
    rate_blocks = soup.find_all(class_=re.compile(r"b-post__info_rates"))
    for block in rate_blocks:
        block_text = block.text.lower()
        val_tag = block.find(["span", "b"], class_=["num", "bold"])
        if not val_tag:
            val_tag = block.find("b")
        if val_tag:
            try:
                val = float(val_tag.text.strip().replace(",", "."))
                if "кинопоиск" in block_text or "kp" in block_text:
                    kp_rating = val
                elif "imdb" in block_text:
                    imdb_rating = val
            except Exception:
                pass
    return kp_rating, imdb_rating


def _evaluate_candidate(
    page_kp_id,
    page_imdb_id,
    page_year,
    scored_item,
    year,
    kp_id,
    imdb_id,
    *,
    print_id_check_line: bool = False,
):
    """Shared verdict logic.

    `_verify_candidate_soup` and `_verify_candidate` previously had two
    almost-identical copies of the score-adjustment + id-match decision
    block (3.21). Refactored: each wrapper just extracts `page_kp_id`,
    `page_imdb_id`, `page_year` from its source object and delegates here.
    """
    score = scored_item["score"]
    current_score = score
    if not scored_item.get("year") and page_year and year:
        diff = abs(year - page_year)
        if diff == 0:
            current_score += 60
        elif diff == 1:
            current_score += 50
        elif diff <= 3:
            current_score -= 40
        else:
            current_score -= 80

    if page_year:
        logger.debug(f"      [*] Год на странице: {page_year}")

    id_match = False
    id_conflict = False

    if kp_id and page_kp_id:
        if str(kp_id) == str(page_kp_id):
            logger.debug(f"      [+] MATCH KP ID: {kp_id}")
            id_match = True
        else:
            logger.debug(f"      [-] CONFLICT KP ID: {kp_id} != {page_kp_id}")
            id_conflict = True

    if imdb_id and page_imdb_id:
        if str(imdb_id) == str(page_imdb_id):
            logger.debug(f"      [+] MATCH IMDb ID: {imdb_id}")
            id_match = True
        else:
            logger.debug(f"      [-] CONFLICT IMDb ID: {imdb_id} != {page_imdb_id}")
            id_conflict = True

    if print_id_check_line:
        logger.debug(
            f"      [?] ID Check: DB({kp_id or '-'}, {imdb_id or '-'}) vs "
            f"Rezka({page_kp_id or '-'}, {page_imdb_id or '-'})"
        )

    if id_conflict:
        return False, page_kp_id, page_imdb_id, current_score, "conflict"

    is_valid = False
    reason = ""
    if id_match:
        is_valid = True
        reason = "id_match"
    elif (kp_id or imdb_id) and not (page_kp_id or page_imdb_id):
        if current_score >= 90:
            if print_id_check_line:
                logger.debug(f"      [+] Trusting by title (Score: {current_score})")
            is_valid = True
            reason = "trust_by_title"
        else:
            if print_id_check_line:
                logger.debug(f"      [-] Not enough data (Score: {current_score})")
    elif not (kp_id or imdb_id) and current_score >= 90:
        is_valid = True
        reason = "high_score"

    if not (kp_id or imdb_id) and (page_kp_id or page_imdb_id) and current_score >= 110:
        is_valid = True
        reason = "page_ids_high_score"

    return is_valid, page_kp_id, page_imdb_id, current_score, reason


def _verify_candidate_soup(soup, scored_item, year, kp_id, imdb_id):
    page_kp_id, page_imdb_id = _extract_kp_imdb_ids(soup)

    page_year = scored_item.get("year")
    if not page_year:
        y_m = re.search(r"(?:Год|Дата выхода):.*?(\d{4})", str(soup), re.S | re.I)
        if y_m:
            page_year = int(y_m.group(1))
        else:
            year_link = soup.select_one('.b-content__main .b-post__info a[href*="/year/"]')
            if year_link:
                ym2 = re.search(r"\d{4}", str(year_link.get("href", "")))
                if ym2:
                    page_year = int(ym2.group(0))

    return _evaluate_candidate(
        page_kp_id, page_imdb_id, page_year, scored_item, year, kp_id, imdb_id
    )


def _extract_metadata_from_soup(soup, kp_rating, imdb_rating):
    kp_r, imdb_r = _extract_ratings_from_soup(soup)
    if kp_r == 0.0:
        kp_r = kp_rating
    if imdb_r == 0.0:
        imdb_r = imdb_rating

    found_poster = None
    og_image = soup.find("meta", property="og:image")
    if og_image and og_image.get("content"):
        found_poster = str(og_image["content"])
    if not found_poster:
        itemprop_img = soup.find("img", itemprop="image")
        if itemprop_img:
            found_poster = itemprop_img.get("src") or itemprop_img.get("data-src")
    if found_poster and str(found_poster).startswith("//"):
        found_poster = "https:" + str(found_poster)

    found_description = ""
    desc_div = soup.find("div", class_="b-post__description_text")
    if desc_div:
        found_description = desc_div.get_text().strip()

    return kp_r, imdb_r, found_poster, found_description


def _verify_candidate(rezka_obj, scored_item, year, kp_id, imdb_id):
    page_kp_id, page_imdb_id = None, None
    if rezka_obj.soup:
        page_kp_id, page_imdb_id = _extract_kp_imdb_ids(rezka_obj.soup)

    page_year = None
    if rezka_obj.releaseYear:
        try:
            page_year = int(rezka_obj.releaseYear)
        except (ValueError, TypeError):
            pass
    if not page_year:
        page_year = scored_item.get("year")

    return _evaluate_candidate(
        page_kp_id,
        page_imdb_id,
        page_year,
        scored_item,
        year,
        kp_id,
        imdb_id,
        print_id_check_line=True,
    )


def _extract_series_info(rezka_obj):
    from HdRezkaApi.types import TVSeries

    if rezka_obj.type != TVSeries or not rezka_obj.seriesInfo:
        return 0, 0

    max_season = 0
    max_episode = 0
    for tid, info in rezka_obj.seriesInfo.items():
        if info.get("premium"):
            continue
        for s_num in info.get("seasons", {}).keys():
            s = int(s_num)
            if s > max_season:
                max_season = s
            eps = info.get("episodes", {}).get(s_num, {})
            for e_num in eps.keys():
                e = int(e_num)
                if s == max_season and e > max_episode:
                    max_episode = e
    return max_season, max_episode


def _extract_metadata_from_rezka(rezka_obj, kp_rating, imdb_rating):
    found_kp_rating = kp_rating
    found_imdb_rating = imdb_rating
    if rezka_obj.soup:
        found_kp_rating, found_imdb_rating = _extract_ratings_from_soup(rezka_obj.soup)

    found_poster = None
    if rezka_obj.thumbnailHQ:
        found_poster = str(rezka_obj.thumbnailHQ)
    elif rezka_obj.thumbnail:
        found_poster = str(rezka_obj.thumbnail)
    if found_poster and found_poster.startswith("//"):
        found_poster = "https:" + found_poster

    found_description = rezka_obj.description or ""

    return found_kp_rating, found_imdb_rating, found_poster, found_description


def _sync_search(query):
    import time

    from HdRezkaApi.search import HdRezkaSearch

    time.sleep(random.uniform(4.0, 8.0))
    try:
        results = HdRezkaSearch(REZKA_ORIGIN)(query)
        return [
            {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "rating": r.get("rating"),
            }
            for r in results
        ]
    except Exception:
        pass
    return []


def _sync_load_rezka(url):
    import time

    from HdRezkaApi import HdRezkaApi

    time.sleep(random.uniform(4.0, 8.0))
    try:
        rezka = HdRezkaApi(url)
        if rezka.ok:
            return rezka
    except Exception:
        pass
    return None


def search_rezka_for_item(title, year, kp_id=None, imdb_id=None, kp_rating=0, imdb_rating=0):
    result = {
        "found": False,
        "rezka_url": None,
        "kp_id": None,
        "imdb_id": None,
        "kp_rating": None,
        "imdb_rating": None,
        "poster_url": None,
        "description": None,
        "score": 0,
        "latest_season": 0,
        "latest_episode": 0,
    }

    clean_parts, search_queries = _parse_title(title)

    logger.info(f"    🔍 Поиск на Rezka: {title} ({year})")
    if kp_id or imdb_id:
        logger.info(f"    📋 Имеем ID: KP:{kp_id or '-'}, IMDb:{imdb_id or '-'}")

    all_results = []
    seen_urls = set()

    for s_title in search_queries:
        try:
            res_with_year = _sync_search(f"{s_title} {year}")

            need_no_year = True
            if res_with_year:
                for r in res_with_year:
                    res_name_norm = normalize_title(re.sub(r"\(.*?\)", "", r["title"]))
                    name_match = any(
                        res_name_norm == db_norm
                        for db_norm in [normalize_title(p) for p in clean_parts]
                    )
                    if name_match:
                        res_year_m = re.search(r"\((\d{4})\)", r["title"])
                        if not res_year_m:
                            res_year_m = re.search(r"-(\d{4})", r["url"])
                        res_year = int(res_year_m.group(1)) if res_year_m else None
                        if res_year and res_year == year:
                            need_no_year = False
                            break

            res_no_year = []
            if need_no_year:
                res_no_year = _sync_search(s_title)

            for r in res_with_year + res_no_year:
                if r["url"] not in seen_urls:
                    all_results.append(r)
                    seen_urls.add(r["url"])
        except Exception as e:
            logger.warning(f"    ⚠️ Ошибка поиска по '{s_title}': {e}")

    if not all_results and clean_parts:
        try:
            logger.info(f"    [?] Fallback search for '{clean_parts[0]}'...")
            res_fallback = _sync_search(clean_parts[0])
            for r in res_fallback:
                if r["url"] not in seen_urls:
                    all_results.append(r)
                    seen_urls.add(r["url"])
        except Exception:
            pass

    if not all_results:
        logger.info("    [-] Ничего не найдено на Rezka.")
        return result

    logger.info(f"    [*] Найдено {len(all_results)} результатов.")

    candidates = _score_candidates(all_results, clean_parts, year)

    has_ids = bool(kp_id or imdb_id)
    min_score = SCORE_MIN_WITH_IDS if has_ids else SCORE_MIN_WITHOUT_IDS

    final_res = None
    final_data = {}

    passes = ["id_match"] if has_ids else []
    passes.append("fallback")

    for current_pass in passes:
        for scored_item in candidates:
            score = scored_item["score"]
            exact_name_match = scored_item.get("exact_name_match", False)

            if score < min_score and not exact_name_match:
                continue

            res = scored_item["res"]

            rezka_obj = _sync_load_rezka(res["url"])
            if not rezka_obj:
                continue

            is_valid, page_kp_id, page_imdb_id, current_score, reason = _verify_candidate(
                rezka_obj, scored_item, year, kp_id, imdb_id
            )

            if current_pass == "id_match" and reason != "id_match":
                continue
            if current_pass == "fallback" and reason == "id_match":
                continue

            if is_valid:
                logger.info(f"    [?] {res['title']} (Score: {score}) -> {reason}")
                kp_r, imdb_r, poster, desc = _extract_metadata_from_rezka(
                    rezka_obj, kp_rating, imdb_rating
                )
                latest_season, latest_episode = _extract_series_info(rezka_obj)
                final_res = res
                final_data = {
                    "kp_id": page_kp_id or kp_id,
                    "imdb_id": page_imdb_id or imdb_id,
                    "score": current_score,
                    "kp_rating": kp_r,
                    "imdb_rating": imdb_r,
                    "poster_url": poster,
                    "description": desc,
                    "latest_season": latest_season,
                    "latest_episode": latest_episode,
                }
                break
            elif reason == "conflict":
                continue

        if final_res:
            break

    if not final_res:
        logger.info("    [-] Подходящий результат не найден.")
        return result

    logger.info(f"    [+] ПОДТВЕРЖДЕНО: {final_res['title']} (Score: {final_data['score']})")

    result["found"] = True
    result["rezka_url"] = final_res["url"]
    result["kp_id"] = final_data["kp_id"]
    result["imdb_id"] = final_data["imdb_id"]
    result["kp_rating"] = final_data["kp_rating"]
    result["imdb_rating"] = final_data["imdb_rating"]
    result["poster_url"] = final_data["poster_url"]
    result["description"] = final_data["description"]
    result["score"] = final_data["score"]
    result["latest_season"] = final_data.get("latest_season", 0)
    result["latest_episode"] = final_data.get("latest_episode", 0)
    return result


# Per-batch error aggregation. Populated by the async helpers below
# (3.14): instead of silently swallowing every transport error, each
# call records (kind, key, error-class) so the caller can print a
# rolled-up summary at the end of a phase.
_BATCH_ERRORS: list[tuple[str, str, str]] = []


def _reset_batch_errors() -> None:
    _BATCH_ERRORS.clear()


def _print_batch_errors(label: str) -> None:
    if not _BATCH_ERRORS:
        return
    by_class: dict[str, int] = {}
    for _kind, _key, cls in _BATCH_ERRORS:
        by_class[cls] = by_class.get(cls, 0) + 1
    logger.info(
        f"  [Phase {label}] aggregated transport errors: total={len(_BATCH_ERRORS)} "
        + ", ".join(f"{k}={v}" for k, v in sorted(by_class.items()))
    )


async def _async_search(session, query, semaphore):
    async with semaphore:
        await asyncio.sleep(random.uniform(2.5, 5.0))
        try:
            async with session.post(
                REZKA_SEARCH_URL,
                data={"q": query},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 200:
                    content = await resp.read()
                    soup = BeautifulSoup(content, "html.parser")
                    results = []
                    for item in soup.select(".b-search__section_list li"):
                        title_el = item.find("span", class_="enty")
                        link_el = item.find("a")
                        if not title_el or not link_el:
                            continue
                        t = title_el.get_text().strip()
                        url = link_el.attrs["href"]
                        rating_span = item.find("span", class_="rating")
                        rating = float(rating_span.get_text()) if rating_span else None
                        results.append({"title": t, "url": url, "rating": rating})
                    return query, results
                # Non-200 also worth aggregating — they often indicate
                # the search endpoint is shadow-blocking us.
                _BATCH_ERRORS.append(("search", query, f"HTTP {resp.status}"))
                return query, []
        except Exception as e:
            _BATCH_ERRORS.append(("search", query, type(e).__name__))
            return query, []


async def _async_load_page(session, url, semaphore):
    async with semaphore:
        await asyncio.sleep(random.uniform(2.5, 5.0))
        try:
            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=20),
                allow_redirects=True,
            ) as resp:
                if resp.status == 200:
                    content = await resp.read()
                    soup = BeautifulSoup(content, "html.parser")
                    return url, soup
                _BATCH_ERRORS.append(("page", url, f"HTTP {resp.status}"))
                return url, None
        except Exception as e:
            _BATCH_ERRORS.append(("page", url, type(e).__name__))
            return url, None


def _check_rezka_limits() -> bool:
    if not _BATCH_ERRORS:
        return False

    terminals = {"HTTP 401", "HTTP 403", "HTTP 429"}
    error_count = 0
    for kind, key, err in _BATCH_ERRORS:
        if err in terminals:
            logger.error(f"[LIMIT_EXHAUSTED] Терминальная ошибка API HDRezka: {err}")
            return True
        if "Timeout" in err or "Client" in err or "Connection" in err or "HTTP" in err:
            error_count += 1

    if error_count >= 5:
        logger.error(
            f"[LIMIT_EXHAUSTED] Слишком много сетевых ошибок ({error_count}) в одном пакете HDRezka."
        )
        return True

    return False


async def _search_rezka_batch(items, db, conn, offset: int = 0, overall_total: int | None = None):
    total = len(items)
    logger.info(f"[rezka] concurrency={REZKA_CONCURRENCY}")
    semaphore = asyncio.Semaphore(REZKA_CONCURRENCY)
    _reset_batch_errors()

    # 3.15: prefer cookies from a logged-in HdRezka session — anonymous
    # access is rate-limited and often returns empty search payloads.
    async with aiohttp.ClientSession(
        headers=REZKA_HEADERS,
        cookies=_login_cookies(),
    ) as session:
        item_infos = []
        for row in items:
            clean_parts, search_queries = _parse_title(row["title"])
            item_infos.append(
                {
                    "row": row,
                    "clean_parts": clean_parts,
                    "search_queries": search_queries,
                }
            )

        # ── PHASE 1a: "With year" searches ──
        with_year_queries = set()
        for info in item_infos:
            year = info["row"]["year"]
            for s_title in info["search_queries"]:
                with_year_queries.add(f"{s_title} {year}")

        logger.info(
            f"  [Phase 1a] {len(with_year_queries)} unique 'with year' queries for {total} items"
        )
        coros = [_async_search(session, q, semaphore) for q in with_year_queries]
        raw = await asyncio.gather(*coros)
        with_year_results = {}
        for q, results in raw:
            with_year_results[q] = results
        _print_batch_errors("1a")

        if _check_rezka_limits():
            _reset_batch_errors()
            return 0, 0, True
        _reset_batch_errors()

        if should_stop(STATUS_KEY):
            return 0, 0, False

        # ── PHASE 1b: Conditional "without year" searches ──
        item_no_year_queries = defaultdict(list)
        no_year_queries = set()

        for idx, info in enumerate(item_infos):
            year = info["row"]["year"]
            norm_db_titles = [normalize_title(p) for p in info["clean_parts"]]

            for si, s_title in enumerate(info["search_queries"]):
                q_wy = f"{s_title} {year}"
                results = with_year_results.get(q_wy, [])

                need_no_year = True
                for r in results:
                    res_name_norm = normalize_title(re.sub(r"\(.*?\)", "", r["title"]))
                    name_match = any(res_name_norm == db_norm for db_norm in norm_db_titles)
                    if name_match:
                        res_year_m = re.search(r"\((\d{4})\)", r["title"])
                        if not res_year_m:
                            res_year_m = re.search(r"-(\d{4})", r["url"])
                        res_year = int(res_year_m.group(1)) if res_year_m else None
                        if res_year and res_year == year:
                            need_no_year = False
                            break

                if need_no_year:
                    item_no_year_queries[idx].append((si, s_title))
                    no_year_queries.add(s_title)

        no_year_results = {}
        if no_year_queries:
            logger.info(f"  [Phase 1b] {len(no_year_queries)} unique 'without year' queries")
            coros = [_async_search(session, q, semaphore) for q in no_year_queries]
            raw = await asyncio.gather(*coros)
            for q, results in raw:
                no_year_results[q] = results
            _print_batch_errors("1b")
            if _check_rezka_limits():
                _reset_batch_errors()
                return 0, 0, True
            _reset_batch_errors()

        if should_stop(STATUS_KEY):
            return 0, 0, False

        # ── PHASE 1c: Fallback searches ──
        fallback_queries = set()
        item_fallback_query = {}

        for idx, info in enumerate(item_infos):
            year = info["row"]["year"]
            has_any = False

            for si, s_title in enumerate(info["search_queries"]):
                q_wy = f"{s_title} {year}"
                if with_year_results.get(q_wy):
                    has_any = True
                    break

            if not has_any:
                for _, q_ny in item_no_year_queries.get(idx, []):
                    if no_year_results.get(q_ny):
                        has_any = True
                        break

            if not has_any and info["clean_parts"]:
                q = info["clean_parts"][0]
                fallback_queries.add(q)
                item_fallback_query[idx] = q

        fallback_results = {}
        if fallback_queries:
            logger.info(f"  [Phase 1c] {len(fallback_queries)} fallback queries")
            coros = [_async_search(session, q, semaphore) for q in fallback_queries]
            raw = await asyncio.gather(*coros)
            for q, results in raw:
                fallback_results[q] = results
            _print_batch_errors("1c")
            if _check_rezka_limits():
                _reset_batch_errors()
                return 0, 0, True
            _reset_batch_errors()

        # ── PHASE 1d: Collect & score ──
        logger.info(f"  [Phase 1d] Scoring candidates for {total} items...")
        item_candidates = {}

        for idx, info in enumerate(item_infos):
            year = info["row"]["year"]
            clean_parts = info["clean_parts"]
            all_results = []
            seen_urls = set()

            for si, s_title in enumerate(info["search_queries"]):
                q_wy = f"{s_title} {year}"
                for r in with_year_results.get(q_wy, []):
                    if r["url"] not in seen_urls:
                        all_results.append(r)
                        seen_urls.add(r["url"])

            for _, q_ny in item_no_year_queries.get(idx, []):
                for r in no_year_results.get(q_ny, []):
                    if r["url"] not in seen_urls:
                        all_results.append(r)
                        seen_urls.add(r["url"])

            if idx in item_fallback_query:
                q_fb = item_fallback_query[idx]
                for r in fallback_results.get(q_fb, []):
                    if r["url"] not in seen_urls:
                        all_results.append(r)
                        seen_urls.add(r["url"])

            if all_results:
                candidates = _score_candidates(all_results, clean_parts, year)
                has_ids = bool(info["row"]["kp_id"] or info["row"]["imdb_id"])
                min_score = SCORE_MIN_RESYNC_WITH_IDS if has_ids else SCORE_MIN_WITHOUT_IDS
                viable = [
                    c for c in candidates if c["score"] >= min_score or c.get("exact_name_match")
                ]
                if viable:
                    item_candidates[idx] = viable

        viable_count = len(item_candidates)
        logger.info(f"  [*] {viable_count}/{total} items have viable candidates")

        # ── PHASE 2: Load pages & verify ──
        page_soup_cache = {}
        item_results = {}

        all_candidate_urls = set()
        for idx, cands in item_candidates.items():
            for c in cands:
                all_candidate_urls.add(c["res"]["url"])

        if all_candidate_urls:
            urls_to_load = [u for u in all_candidate_urls if u not in page_soup_cache]
            if urls_to_load:
                logger.info(f"  [Phase 2] Loading {len(urls_to_load)} candidate pages...")
                sem = asyncio.Semaphore(REZKA_CONCURRENCY)
                coros = [_async_load_page(session, u, sem) for u in urls_to_load]
                raw = await asyncio.gather(*coros)
                for url, soup in raw:
                    page_soup_cache[url] = soup
                _print_batch_errors("Phase 2")
                if _check_rezka_limits():
                    _reset_batch_errors()
                    return 0, 0, True
                _reset_batch_errors()

            if should_stop(STATUS_KEY):
                return 0, 0, False

        for idx, candidates in item_candidates.items():
            row = item_infos[idx]["row"]
            has_ids = bool(row["kp_id"] or row["imdb_id"])

            id_match_result = None
            fallback_result = None

            for candidate in candidates:
                url = candidate["res"]["url"]
                soup = page_soup_cache.get(url)
                if soup is None:
                    continue

                is_valid, page_kp_id, page_imdb_id, current_score, reason = _verify_candidate_soup(
                    soup, candidate, row["year"], row["kp_id"], row["imdb_id"]
                )

                if not is_valid:
                    continue

                kp_r, imdb_r, poster, desc = _extract_metadata_from_soup(
                    soup, row["kp_rating"] or 0, row["imdb_rating"] or 0
                )
                r_data = {
                    "found": True,
                    "rezka_url": url,
                    "kp_id": page_kp_id or row["kp_id"],
                    "imdb_id": page_imdb_id or row["imdb_id"],
                    "kp_rating": kp_r,
                    "imdb_rating": imdb_r,
                    "poster_url": poster,
                    "description": desc,
                    "score": current_score,
                    "latest_season": 0,
                    "latest_episode": 0,
                }

                if reason == "id_match":
                    id_match_result = (candidate, r_data, reason, current_score)
                    break
                elif fallback_result is None:
                    fallback_result = (candidate, r_data, reason, current_score)

            chosen = id_match_result or fallback_result
            if chosen:
                candidate, r_data, reason, current_score = chosen
                item_results[idx] = r_data
                logger.info(f"    [+] {row['title']}: CONFIRMED ({reason}, score: {current_score})")
                if id_match_result and fallback_result and id_match_result is not fallback_result:
                    logger.info("    ℹ️  ID-match preferred over trust_by_title")
            else:
                logger.info(f"    [-] {row['title']}: no suitable candidate")

        # ── PHASE 3: Write results ──
        logger.info("\n  [Phase 3] Writing results to database...")
        found_count = 0

        for idx, row in enumerate(items):
            item_id = row["id"]
            report_progress(offset + idx + 1, overall_total or total)

            if idx in item_results and item_results[idx]["found"]:
                r = item_results[idx]
                db.fill_item_metadata(
                    item_id,
                    conn=conn,
                    rezka_url=r["rezka_url"],
                    kp_rating=r["kp_rating"],
                    imdb_rating=r["imdb_rating"],
                    kp_id=r["kp_id"],
                    imdb_id=r["imdb_id"],
                    poster_url=r["poster_url"],
                    description=r["description"],
                    checked_rezka=1,
                    latest_season=r.get("latest_season", 0),
                    latest_episode=r.get("latest_episode", 0),
                )
                found_count += 1
            else:
                db.mark_checked(item_id, "rezka", conn=conn)

        conn.commit()
        not_found_count = total - found_count
        logger.info(f"  Found: {found_count}, Not found: {not_found_count}")
        return found_count, not_found_count, False


def search_rezka_metadata():
    global logger
    logger = setup_logging("parsclode.rezka", "rezka_log.txt")
    clear_stop_flag(STATUS_KEY)
    db = Database()
    conn = db.get_connection()
    cursor = conn.cursor()

    checkpoint = load_checkpoint(STATUS_KEY)
    if checkpoint and checkpoint.get("interrupted"):
        logger.info("[RESUME] Обнаружен прерванный чекпоинт Rezka. Продолжаем поиск...")

    video_cats = "(1, 4, 5, 16, 7)"
    cursor.execute(f"""
        SELECT id, title, year, kp_id, imdb_id, kp_rating, imdb_rating, rezka_url, poster_url
        FROM items
        WHERE category_id IN {video_cats}
        AND (
            kp_id IS NULL OR kp_id = '' OR
            imdb_id IS NULL OR imdb_id = '' OR
            kp_rating = 0 OR kp_rating IS NULL OR
            imdb_rating = 0 OR imdb_rating IS NULL OR
            rezka_url IS NULL OR rezka_url = ''
        )
        AND is_ignored = 0
        AND checked_rezka = 0
        ORDER BY id DESC
    """)
    items = cursor.fetchall()

    total_count = len(items)
    logger.info(f"=== REZKA SYNC (Total: {total_count}) ===")

    if total_count > 0:
        batch_size = 30
        overall_found = 0
        overall_not_found = 0

        for offset in range(0, total_count, batch_size):
            if should_stop(STATUS_KEY):
                logger.info("[STOP] Обнаружен флаг остановки. Сохраняем чекпоинт и выходим.")
                save_checkpoint(STATUS_KEY, {"interrupted": True})
                break

            batch = items[offset : offset + batch_size]
            logger.info(
                f"\n--- Обработка пакета {offset // batch_size + 1} ({offset + 1} - {min(offset + batch_size, total_count)} из {total_count}) ---"
            )

            found, not_found, is_limited = asyncio.run(
                _search_rezka_batch(batch, db, conn, offset=offset, overall_total=total_count)
            )

            overall_found += found
            overall_not_found += not_found

            if is_limited:
                logger.error(
                    "[LIMIT_EXHAUSTED] [rezka] Лимит запросов HDRezka или IP-блокировка обнаружена! Приостановка синхронизации..."
                )
                save_checkpoint(STATUS_KEY, {"interrupted": True})
                conn.close()
                import sys

                sys.exit(2)

            if should_stop(STATUS_KEY):
                logger.info(
                    "[STOP] Обнаружен флаг остановки во время выполнения пакета. Сохраняем чекпоинт и выходим."
                )
                save_checkpoint(STATUS_KEY, {"interrupted": True})
                break
        else:
            clear_checkpoint(STATUS_KEY)

        logger.info(f"\n=== RESULTS: Found={overall_found}, Not found={overall_not_found} ===")
    else:
        logger.info("No items to process.")
        clear_checkpoint(STATUS_KEY)

    conn.close()
    logger.info("\n=== FINISHED ===")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Sync metadata from HDRezka")
    parser.add_argument(
        "--concurrency",
        type=int,
        default=None,
        help=(
            "Override the number of concurrent rezka requests "
            "(takes precedence over REZKA_CONCURRENCY env and config.yml)"
        ),
    )
    args = parser.parse_args()
    if args.concurrency is not None:
        if args.concurrency < 1:
            parser.error("--concurrency must be >= 1")
        REZKA_CONCURRENCY = args.concurrency
    search_rezka_metadata()
