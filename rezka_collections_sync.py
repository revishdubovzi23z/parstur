import os
import re

from dotenv import load_dotenv

from app_core import normalize_title
from db import Database
from logger import setup_tee_logger

load_dotenv()
REZKA_EMAIL = os.getenv("REZKA_EMAIL", "")
REZKA_PASSWORD = os.getenv("REZKA_PASSWORD", "")
REZKA_ORIGIN = "https://rezka.ag"
REZKA_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/81.0.4044.138 Safari/537.36",
    "X-Requested-With": "XMLHttpRequest",
}
REZKA_PAGE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/81.0.4044.138 Safari/537.36",
}

REZKA_PATH_TO_CATEGORY = {
    "films": 1,
    "series": 4,
    "cartoons": 7,
    "animation": 10,
    "show": 6,
}


def _login():
    from HdRezkaApi import HdRezkaSession

    session = HdRezkaSession(REZKA_ORIGIN)
    session.login(REZKA_EMAIL, REZKA_PASSWORD)
    print("  [+] Rezka login OK")
    return session


def _get_folders(session):
    import requests
    from bs4 import BeautifulSoup

    r = requests.get(
        f"{REZKA_ORIGIN}/favorites/",
        headers=REZKA_PAGE_HEADERS,
        cookies=session.cookies,
        timeout=20,
    )
    soup = BeautifulSoup(r.content, "html.parser")
    sidebar = soup.find("div", class_="b-favorites_content__sidebarbar")
    if not sidebar:
        sidebar = soup.find("div", class_="b-favorites_content__sidebar")
    if not sidebar:
        return []

    folders = []
    for a in sidebar.find_all("a", href=True):
        href = a["href"]
        if "javascript" in href:
            continue
        text = a.text.strip()
        m = re.search(r"\((\d+)\)", text)
        count = int(m.group(1)) if m else 0
        name = re.sub(r"\s*\(\d+\)", "", text).strip()
        m2 = re.search(r"/favorites/(\d+)/", href)
        folder_id = m2.group(1) if m2 else None
        if folder_id:
            folders.append({"id": folder_id, "name": name, "url": href, "count": count})
    return folders


def _get_folder_items(url, session):
    import requests
    from bs4 import BeautifulSoup

    all_urls = []
    page = 0
    while True:
        page_url = url if page == 0 else f"{url}page/{page}/"
        r = requests.get(
            page_url,
            headers=REZKA_PAGE_HEADERS,
            cookies=session.cookies,
            timeout=20,
        )
        if r.status_code != 200:
            break
        soup = BeautifulSoup(r.content, "html.parser")
        items = soup.select(".b-content__inline_item")
        if not items:
            break
        for item in items:
            link = item.find("a", href=re.compile(r"rezka\.ag"))
            if link:
                all_urls.append(link["href"])
        pagination = soup.find("a", class_=re.compile(r"pag.*next"))
        if not pagination:
            break
        page += 1
    return all_urls


def _add_to_rezka_folder(post_id, cat_id, session):
    import requests

    r = requests.post(
        f"{REZKA_ORIGIN}/ajax/favorites/",
        data={"post_id": str(post_id), "cat_id": str(cat_id), "action": "add_post"},
        headers=REZKA_HEADERS,
        cookies=session.cookies,
        timeout=10,
    )
    return r.json().get("success", False)


def _extract_post_id(url):
    m = re.search(r"/(?:films|series|cartoons|animation|show|telecasts)/[^/]+/(\d+)-", url)
    return m.group(1) if m else None


def _infer_category_from_url(url):
    for path, cat_id in REZKA_PATH_TO_CATEGORY.items():
        if f"/{path}/" in url:
            return cat_id
    return 1


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


def _parse_rezka_page(url, session):
    try:
        rezka = session.get(url)
        if not rezka or not rezka.ok:
            return None
    except Exception:
        return None

    title = rezka.name or ""
    original_title = rezka.origName or ""
    description = rezka.description or ""
    poster = rezka.thumbnailHQ or rezka.thumbnail or None
    if poster and str(poster).startswith("//"):
        poster = "https:" + str(poster)
    year = rezka.releaseYear
    if year:
        try:
            year = int(year)
        except (ValueError, TypeError):
            year = None

    kp_id, imdb_id = None, None
    kp_rating, imdb_rating = 0.0, 0.0
    if rezka.soup:
        kp_id, imdb_id = _extract_kp_imdb_ids(rezka.soup)
        kp_rating, imdb_rating = _extract_ratings_from_soup(rezka.soup)

    category_id = _infer_category_from_url(url)

    names = [n.strip() for n in title.split("/")]
    display_title = title
    if original_title and original_title.lower() not in [n.lower() for n in names]:
        display_title = f"{title} / {original_title}"
    if year and str(year) not in display_title:
        display_title += f" ({year})"

    return {
        "title": display_title,
        "original_title": original_title,
        "year": year,
        "category_id": category_id,
        "poster_url": poster,
        "description": description,
        "kp_id": kp_id,
        "imdb_id": imdb_id,
        "kp_rating": kp_rating,
        "imdb_rating": imdb_rating,
        "rezka_url": url,
        "checked_rezka": 1,
        "title_norm": normalize_title(display_title),
    }


def _search_rezka_url(title, year, session):
    from app_core import clean_title_for_search

    parts = [p.strip() for p in title.split("/")]
    search_queries = []
    for p in parts:
        p_clean = re.sub(r"\(\d{4}\)", "", p).strip()
        q = clean_title_for_search(p_clean)
        if q and len(q) > 1:
            search_queries.append(q)
    search_queries = sorted(search_queries, key=lambda x: (not x.isascii(), len(x)), reverse=True)

    for q in search_queries:
        for suffix in [f" {year}", ""]:
            try:
                results = session.search(q + suffix)
                for result in results:
                    res_title = result.get("title", "")
                    res_url = result.get("url", "")

                    res_year_m = re.search(r"\((\d{4})\)", res_title)
                    res_year = int(res_year_m.group(1)) if res_year_m else None

                    res_name_norm = normalize_title(re.sub(r"\(.*?\)", "", res_title))
                    for p in parts:
                        p_clean = re.sub(r"\(\d{4}\)", "", p).strip()
                        p_norm = normalize_title(p_clean)
                        if res_name_norm == p_norm:
                            if res_year and year and abs(res_year - year) <= 1:
                                return res_url
                            if not year or not res_year:
                                return res_url
            except Exception:
                pass
    return None


def _match_folder_to_collection(folder_name, collections):
    folder_norm = normalize_title(folder_name)
    for coll in collections:
        if normalize_title(coll["name"]) == folder_norm:
            return coll
    best = None
    best_ratio = 0
    for coll in collections:
        coll_norm = normalize_title(coll["name"])
        shorter = min(len(folder_norm), len(coll_norm))
        if shorter < 3:
            continue
        overlap = sum(1 for a, b in zip(folder_norm, coll_norm) if a == b)
        ratio = overlap / max(len(folder_norm), len(coll_norm))
        if ratio > best_ratio and ratio >= 0.7:
            best_ratio = ratio
            best = coll
    return best


def sync_rezka_collections():
    setup_tee_logger("rezka_collections", "rezka_collections_log.txt")

    if not REZKA_EMAIL or not REZKA_PASSWORD:
        print("[-] REZKA_EMAIL/REZKA_PASSWORD not set in .env")
        return

    print("=== REZKA COLLECTIONS BIDIRECTIONAL SYNC ===")

    session = _login()
    folders = _get_folders(session)

    if not folders:
        print("[-] No folders found on Rezka")
        return

    print(f"[*] Found {len(folders)} Rezka folders:")
    for f in folders:
        print(f"    {f['name']} ({f['count']}) [id={f['id']}]")

    db = Database()
    conn = db.get_connection()
    c = conn.cursor()
    collections = db.get_collections()

    c.execute(
        "SELECT id, rezka_url, title, year FROM items WHERE rezka_url IS NOT NULL AND rezka_url != ''"
    )
    url_to_item = {}
    for row in c.fetchall():
        url_to_item[row["rezka_url"]] = row["id"]

    all_items = {}
    c.execute("SELECT id, title, year, rezka_url FROM items")
    for row in c.fetchall():
        all_items[row["id"]] = {
            "title": row["title"],
            "year": row["year"],
            "rezka_url": row["rezka_url"],
        }

    total_rezka_to_project = 0
    total_project_to_rezka = 0
    total_new_items = 0
    total_new_urls = 0

    for folder in folders:
        coll = _match_folder_to_collection(folder["name"], collections)

        if not coll:
            print(f"\n  [+] Creating new collection '{folder['name']}'")
            db.create_collection(folder["name"])
            conn.commit()
            collections = db.get_collections()
            coll = _match_folder_to_collection(folder["name"], collections)

        if not coll:
            continue

        coll_id = coll["id"]
        coll_name = coll["name"]
        cat_id = folder["id"]
        label = (
            folder["name"] if folder["name"] == coll_name else f"{folder['name']} -> {coll_name}"
        )
        print(f"\n  [sync] '{label}' (cat_id={cat_id}, coll_id={coll_id})")

        rezka_urls = _get_folder_items(folder["url"], session) if folder["count"] > 0 else []
        rezka_item_ids = set()
        new_items_from_rezka = []

        for rz_url in rezka_urls:
            item_id = url_to_item.get(rz_url)
            if item_id:
                rezka_item_ids.add(item_id)
            else:
                new_items_from_rezka.append(rz_url)

        c.execute("SELECT item_id FROM collection_items WHERE collection_id = ?", (coll_id,))
        project_item_ids = {r[0] for r in c.fetchall()}

        for rz_url in new_items_from_rezka:
            print(f"    [new] Parsing Rezka page to create card: {rz_url}")
            parsed = _parse_rezka_page(rz_url, session)
            if not parsed:
                print("      [-] Failed to parse page")
                continue

            item_id = db.insert_item(parsed, conn=conn)
            if item_id:
                conn.commit()
                url_to_item[rz_url] = item_id
                all_items[item_id] = {
                    "title": parsed["title"],
                    "year": parsed["year"],
                    "rezka_url": rz_url,
                }
                rezka_item_ids.add(item_id)
                total_new_items += 1
                print(f"      [+] Created card id={item_id}: {parsed['title']}")
            else:
                print(f"      [?] Could not create card for {rz_url}")

        only_on_rezka = rezka_item_ids - project_item_ids
        only_on_project = project_item_ids - rezka_item_ids

        for item_id in only_on_rezka:
            c.execute(
                "INSERT OR IGNORE INTO collection_items (collection_id, item_id) VALUES (?, ?)",
                (coll_id, item_id),
            )
        if only_on_rezka:
            conn.commit()
            total_rezka_to_project += len(only_on_rezka)
        print(f"    Rezka -> Project: +{len(only_on_rezka)} items")

        pushed = 0
        searched = 0
        for item_id in only_on_project:
            info = all_items.get(item_id)
            if not info:
                continue

            rz_url = info.get("rezka_url")
            if not rz_url:
                print(f"    [search] Looking for Rezka URL: {info['title']} ({info['year']})")
                rz_url = _search_rezka_url(info["title"], info["year"], session)
                if rz_url:
                    db.fill_item_metadata(item_id, conn=conn, rezka_url=rz_url, checked_rezka=1)
                    conn.commit()
                    url_to_item[rz_url] = item_id
                    all_items[item_id]["rezka_url"] = rz_url
                    searched += 1
                    total_new_urls += 1
                    print(f"      [+] Found: {rz_url}")
                else:
                    print("      [-] Not found on Rezka")
                    continue

            post_id = _extract_post_id(rz_url)
            if post_id:
                ok = _add_to_rezka_folder(post_id, cat_id, session)
                if ok:
                    pushed += 1
                else:
                    print(f"      [!] Failed to push post {post_id} to Rezka")

        total_project_to_rezka += pushed
        print(
            f"    Project -> Rezka: +{pushed} items pushed ({searched} Rezka URLs found by search)"
        )

        final_count = len(rezka_item_ids & (project_item_ids | only_on_rezka))
        print(f"    Collection '{coll_name}' total: {final_count + pushed} items")

    print("\n=== SYNC COMPLETE ===")
    print(f"  Rezka -> Project: +{total_rezka_to_project} items added to collections")
    print(f"  Project -> Rezka: +{total_project_to_rezka} items pushed to Rezka")
    print(f"  New cards created from Rezka: {total_new_items}")
    print(f"  New Rezka URLs found by search: {total_new_urls}")
    conn.close()


if __name__ == "__main__":
    sync_rezka_collections()
