import json
import os
import re
import sys

import requests

from db import Database
from logger import setup_tee_logger
from poiskkino_client import PoiskKinoClient
from rezka_sync import search_rezka_for_item
from settings import settings
from tmdb_client import TMDBClient

RUTOR_MIRROR = settings.rutor_mirror.rstrip("/")


def report_progress(current, total, status_key="single_update"):
    try:
        p_file = os.path.join(settings.app_data_dir, f"progress_{status_key}.json")
        with open(p_file, "w") as f:
            json.dump({"current": current, "total": total}, f)
    except Exception:
        pass


def update_single_item(item_id):
    setup_tee_logger("single_update", "single_update_log.txt")
    db = Database()
    conn = db.get_connection()

    item = db.get_item(item_id, conn=conn)
    if not item:
        print(f"Item {item_id} not found.")
        conn.close()
        return

    print(f"=== ОБНОВЛЕНИЕ МЕТАДАННЫХ ДЛЯ ID {item_id} ===")
    print(f"🎬 {item['title']} ({item['year']})")

    kp_id = item["kp_id"]
    imdb_id = item["imdb_id"]

    if not kp_id or not imdb_id:
        rels = db.get_rutor_ids_for_item(item_id, conn=conn)
        for rutor_id in rels:
            if kp_id and imdb_id:
                break
            try:
                url = f"{RUTOR_MIRROR}/torrent/{rutor_id}"
                print(f"  🔍 Проверка Rutor: {url}")
                resp = requests.get(url, timeout=15)
                if resp.status_code == 200:
                    if not kp_id:
                        m = re.search(r"kinopoisk\.ru/rating/(\d+)\.gif", resp.text)
                        if not m:
                            m = re.search(r"kinopoisk\.ru/(?:film|series)/(\d+)", resp.text)
                        if m:
                            kp_id = m.group(1)
                            print(f"    ✅ Нашел KP ID: {kp_id}")
                    if not imdb_id:
                        m = re.search(r"imdb\.com/title/(tt\d+)", resp.text)
                        if m:
                            imdb_id = m.group(1)
                            print(f"    ✅ Нашел IMDb ID: {imdb_id}")
            except Exception:
                pass

    tmdb = TMDBClient()
    tmdb_data = None
    if imdb_id:
        tmdb_data = tmdb.find_by_imdb_id(imdb_id)
    if not tmdb_data:
        t_parts = item["title"].split(" / ")
        ru_part = re.sub(r"\s*\(\d{4}\)\s*", "", t_parts[0].split("/")[0]).strip()
        en_part = None
        if len(t_parts) > 1:
            en_part = re.sub(r"\s*\(\d{4}\)\s*", "", t_parts[1].split("/")[0]).strip()
        search_primary = en_part or ru_part
        search_alt = ru_part if en_part else None
        tmdb_data = tmdb.search_movie(search_primary, item["year"], alt_title=search_alt)

    if tmdb_data:
        print("  ✅ TMDB данные получены")
        tmdb_fields = {}
        if tmdb_data.get("poster_url"):
            tmdb_fields["poster_url"] = tmdb_data["poster_url"]
        if tmdb_data.get("description"):
            tmdb_fields["description"] = tmdb_data["description"]
        if not imdb_id and tmdb_data.get("imdb_id"):
            tmdb_fields["imdb_id"] = tmdb_data["imdb_id"]
            imdb_id = tmdb_data["imdb_id"]
        if kp_id:
            tmdb_fields["kp_id"] = kp_id
        if tmdb_fields:
            db.fill_item_metadata(item_id, conn=conn, **tmdb_fields)
            conn.commit()

    poisk = PoiskKinoClient()
    print("  🔍 Запрос к PoiskKino...")
    pk_data = None
    if kp_id:
        pk_data = poisk.get_by_id(kp_id)
    if not pk_data:
        pk_data = poisk.search_movie(item["title"].split(" / ")[0], item["year"])

    if pk_data:
        print("  ✅ PoiskKino данные получены")
        kp_rating = pk_data.get("kp_rating", 0.0)
        imdb_rating = pk_data.get("imdb_rating", 0.0)
        db.fill_item_metadata(item_id, conn=conn, kp_rating=kp_rating, imdb_rating=imdb_rating)
        conn.commit()

    print("  🔍 Поиск на Rezka...")
    r = search_rezka_for_item(
        title=item["title"],
        year=item["year"],
        kp_id=kp_id,
        imdb_id=imdb_id,
        kp_rating=item["kp_rating"] or 0,
        imdb_rating=item["imdb_rating"] or 0,
    )
    if r["found"]:
        print(f"  ✅ Rezka найдена: {r['rezka_url']} (Score: {r['score']})")
        db.fill_item_metadata(
            item_id,
            conn=conn,
            rezka_url=r["rezka_url"],
            kp_id=r["kp_id"],
            imdb_id=r["imdb_id"],
            kp_rating=r["kp_rating"],
            imdb_rating=r["imdb_rating"],
            poster_url=r["poster_url"],
            description=r["description"],
            latest_season=r.get("latest_season", 0),
            latest_episode=r.get("latest_episode", 0),
        )
        conn.commit()
        if not kp_id and r["kp_id"]:
            kp_id = r["kp_id"]
        if not imdb_id and r["imdb_id"]:
            imdb_id = r["imdb_id"]

    print("=== ОБНОВЛЕНИЕ ЗАВЕРШЕНО ===")
    conn.close()


if __name__ == "__main__":
    if len(sys.argv) > 1:
        update_single_item(int(sys.argv[1]))
    else:
        print("Usage: python single_item_update.py <item_id>")
