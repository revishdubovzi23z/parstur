import requests
from bs4 import BeautifulSoup
import re
import os
import time
import unicodedata
from dotenv import load_dotenv
from tmdb_client import TMDBClient
from app_core import normalize_title
from script_utils import load_config
from db import Database
from logger import setup_tee_logger

load_dotenv()

import sys


class UserSync:
    def __init__(self, db_path="app_data.db"):
        setup_tee_logger("user_sync", "user_sync_log.txt")
        self.db_path = db_path
        self.db = Database(db_path)
        self.tmdb = TMDBClient()
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        }
        self.db.check_and_migrate_schema()

    def lookup_missing_ids(self, title, year, external_id=None):
        try:
            if external_id and external_id.startswith("tt"):
                data = self.tmdb.find_by_imdb_id(external_id)
                if data:
                    return external_id, None

            data = self.tmdb.search_movie(title, year)
            if data:
                return data.get("imdb_id"), None
        except Exception:
            pass
        return external_id, None

    def sync_all_csvs(self, imdb_path=None, kp_path=None):
        _cfg = load_config().get("user_sync", {})
        if imdb_path is None:
            imdb_path = _cfg.get("imdb_csv", "IMDBOCENKI.csv")
        if kp_path is None:
            kp_path = _cfg.get("kp_csv", "kinopoiskocenki.csv")
        import csv

        merged_data = {}

        def get_key(title, year):
            t = normalize_title(title)
            if not t or not year:
                return None
            return (t, year)

        if os.path.exists(imdb_path):
            print(f"Загрузка IMDb: {imdb_path}")
            try:
                with open(imdb_path, "r", encoding="utf-8-sig") as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        imdb_id = row.get("Const")
                        title = row.get("Title")
                        orig_title = row.get("Original Title")
                        year = row.get("Year")
                        rating = row.get("Your Rating")

                        if not title or not rating:
                            continue
                        year_int = int(year) if year and str(year).isdigit() else None

                        item = {
                            "title": title,
                            "orig_title": orig_title,
                            "year": year_int,
                            "rating": int(float(rating)),
                            "imdb_id": imdb_id,
                            "kp_id": None,
                            "title_norm": normalize_title(title),
                            "orig_norm": normalize_title(orig_title),
                        }

                        keys = []
                        if item["title_norm"]:
                            keys.append((item["title_norm"], year_int))
                        if item["orig_norm"]:
                            keys.append((item["orig_norm"], year_int))

                        added = False
                        for k in keys:
                            if k not in merged_data:
                                merged_data[k] = item
                                added = True
                        if not added and imdb_id:
                            pass
            except Exception as e:
                print(f"Ошибка IMDb: {e}")

        if os.path.exists(kp_path):
            print(f"Загрузка Кинопоиска: {kp_path}")
            encodings = ["utf-8-sig", "utf-16", "cp1251", "utf-16le"]
            content = None
            for enc in encodings:
                try:
                    with open(kp_path, "r", encoding=enc) as f:
                        content = f.read()
                        break
                except Exception:
                    continue

            if content:
                try:
                    lines = content.splitlines()
                    delimiter = (
                        "\t" if "\t" in lines[0] else (";" if ";" in lines[0] else ",")
                    )
                    reader = csv.DictReader(lines, delimiter=delimiter)
                    for row in reader:
                        row = {
                            k.strip('"').strip(): v.strip('"').strip()
                            for k, v in row.items()
                            if k
                        }
                        row_l = {k.lower(): v for k, v in row.items()}

                        title = (
                            row.get("Title")
                            or row_l.get("name")
                            or row_l.get("название")
                        )
                        orig_title = row.get("Original Title") or row_l.get(
                            "original title"
                        )
                        kp_id = row.get("backup_id") or row_l.get("id")
                        year = row.get("Year") or row_l.get("год")
                        rating = (
                            row.get("My rating")
                            or row_l.get("rating")
                            or row_l.get("оценка")
                        )

                        if not title or not rating:
                            continue
                        try:
                            year_int = (
                                int(year) if year and str(year).isdigit() else None
                            )
                            rating_int = int(float(rating.replace(",", ".")))
                            if rating_int == 0:
                                continue

                            t_norm = normalize_title(title)
                            o_norm = normalize_title(orig_title)

                            match = None
                            for k in [(t_norm, year_int), (o_norm, year_int)]:
                                if k in merged_data:
                                    match = merged_data[k]
                                    break

                            if match:
                                if kp_id:
                                    match["kp_id"] = kp_id
                                if not match["title"] and title:
                                    match["title"] = title
                                if not match["orig_title"] and orig_title:
                                    match["orig_title"] = orig_title
                            else:
                                item = {
                                    "title": title,
                                    "orig_title": orig_title,
                                    "year": year_int,
                                    "rating": rating_int,
                                    "imdb_id": None,
                                    "kp_id": kp_id,
                                    "title_norm": t_norm,
                                    "orig_norm": o_norm,
                                }
                                if t_norm:
                                    merged_data[(t_norm, year_int)] = item
                                if o_norm:
                                    merged_data[(o_norm, year_int)] = item
                        except Exception:
                            pass
                except Exception as e:
                    print(f"Ошибка Кинопоиска: {e}")

        print(
            f"Всего уникальных записей после мерджа: {len(set(id(v) for v in merged_data.values()))}"
        )
        added = 0
        updated = 0

        unique_items = []
        seen_ids = set()
        for item in merged_data.values():
            if id(item) in seen_ids:
                continue
            seen_ids.add(id(item))
            unique_items.append(item)

        conn = self.db.get_connection()
        for item in unique_items:
            existing = self.db.upsert_user_rating(item, conn=conn)
            if existing is not None:
                updated += 1
            else:
                added += 1
        conn.commit()
        conn.close()

        print(f"Синхронизация завершена. Добавлено: {added}, Обновлено: {updated}")
        return added + updated


if __name__ == "__main__":
    sync = UserSync()
    sync.sync_all_csvs("IMDBOCENKI.csv", "kinopoiskocenki.csv")

    try:
        total_ratings = sync.db.get_user_ratings_count()
        print(f"\n==========================================")
        print(f"ИТОГО В ВАШЕЙ БАЗЕ СОХРАНЕНО: {total_ratings} ОЦЕНОК")
        print(f"==========================================\n")
    except Exception as e:
        print(f"Ошибка при подсчете оценок: {e}")
