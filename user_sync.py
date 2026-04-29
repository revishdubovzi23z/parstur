import requests
from bs4 import BeautifulSoup
import re
import sqlite3
import os
import time
import unicodedata
from dotenv import load_dotenv
from tmdb_client import TMDBClient
from app_core import normalize_title

load_dotenv()

import sys


class Logger:
    def __init__(self, filename="user_sync_log.txt"):
        self.terminal = sys.stdout
        self.log = open(filename, "w", encoding="utf-8")

    def write(self, message):
        self.terminal.write(message)
        self.log.write(message)
        self.log.flush()

    def flush(self):
        self.terminal.flush()
        self.log.flush()


class UserSync:
    def __init__(self, db_path="app_data.db"):
        sys.stdout = Logger("user_sync_log.txt")
        self.db_path = db_path
        self.tmdb = TMDBClient()
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        }
        self._check_schema()

    def _check_schema(self):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cols = [
                col[1]
                for col in cursor.execute("PRAGMA table_info(user_ratings)").fetchall()
            ]
            if "original_title" not in cols:
                cursor.execute(
                    "ALTER TABLE user_ratings ADD COLUMN original_title TEXT"
                )
            if "title_norm" not in cols:
                cursor.execute("ALTER TABLE user_ratings ADD COLUMN title_norm TEXT")
            if "original_title_norm" not in cols:
                cursor.execute(
                    "ALTER TABLE user_ratings ADD COLUMN original_title_norm TEXT"
                )
            if "imdb_id" not in cols:
                cursor.execute("ALTER TABLE user_ratings ADD COLUMN imdb_id TEXT")
            if "kp_id" not in cols:
                cursor.execute("ALTER TABLE user_ratings ADD COLUMN kp_id TEXT")

            # Индексы для ускорения
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_ratings_title_norm ON user_ratings(title_norm)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_ratings_imdb_id ON user_ratings(imdb_id)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_ratings_kp_id ON user_ratings(kp_id)"
            )

            # Убедимся что в items тоже есть title_norm
            items_cols = [
                col[1] for col in cursor.execute("PRAGMA table_info(items)").fetchall()
            ]
            if "title_norm" not in items_cols:
                cursor.execute("ALTER TABLE items ADD COLUMN title_norm TEXT")
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_items_title_norm ON items(title_norm)"
            )

            # Таблица для быстрого поиска по нескольким названиям
            cursor.execute(
                "CREATE TABLE IF NOT EXISTS item_search_names (item_id INTEGER, name_norm TEXT)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_search_names_item ON item_search_names(item_id)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_search_names_name ON item_search_names(name_norm)"
            )

            conn.commit()


    def lookup_missing_ids(self, title, year, external_id=None):
        """Пытается найти недостающие ID через TMDB"""
        try:
            if external_id and external_id.startswith("tt"):  # IMDb ID
                data = self.tmdb.find_by_imdb_id(external_id)
                if data:
                    return (
                        external_id,
                        None,
                    )  # TMDB find не всегда дает KP ID, но подтверждает IMDb

            # Поиск по названию
            data = self.tmdb.search_movie(title, year)
            if data:
                return data.get("imdb_id"), None  # TMDB обычно дает только IMDb ID
        except:
            pass
        return external_id, None

    def sync_all_csvs(self, imdb_path="IMDBOCENKI.csv", kp_path="kinopoiskocenki.csv"):
        import csv
        merged_data = {} # key: (title_norm, year) or (orig_title_norm, year)

        def get_key(title, year):
            t = normalize_title(title)
            if not t or not year: return None
            return (t, year)

        # 1. Загружаем IMDb
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
                        
                        if not title or not rating: continue
                        year_int = int(year) if year and str(year).isdigit() else None
                        
                        item = {
                            "title": title,
                            "orig_title": orig_title,
                            "year": year_int,
                            "rating": int(float(rating)),
                            "imdb_id": imdb_id,
                            "kp_id": None,
                            "title_norm": normalize_title(title),
                            "orig_norm": normalize_title(orig_title)
                        }
                        
                        # Ключи для мерджа
                        keys = []
                        if item["title_norm"]: keys.append((item["title_norm"], year_int))
                        if item["orig_norm"]: keys.append((item["orig_norm"], year_int))
                        
                        # Сохраняем под всеми ключами
                        added = False
                        for k in keys:
                            if k not in merged_data:
                                merged_data[k] = item
                                added = True
                        if not added and imdb_id:
                            # Если ключ уже есть, проверим не тот же ли это фильм по ID
                            pass 
            except Exception as e:
                print(f"Ошибка IMDb: {e}")

        # 2. Загружаем Кинопоиск (Кинориум)
        if os.path.exists(kp_path):
            print(f"Загрузка Кинопоиска: {kp_path}")
            encodings = ["utf-8-sig", "utf-16", "cp1251", "utf-16le"]
            content = None
            for enc in encodings:
                try:
                    with open(kp_path, "r", encoding=enc) as f:
                        content = f.read()
                        break
                except: continue
            
            if content:
                try:
                    lines = content.splitlines()
                    delimiter = "\t" if "\t" in lines[0] else (";" if ";" in lines[0] else ",")
                    reader = csv.DictReader(lines, delimiter=delimiter)
                    for row in reader:
                        row = {k.strip('"').strip(): v.strip('"').strip() for k, v in row.items() if k}
                        row_l = {k.lower(): v for k, v in row.items()}
                        
                        title = row.get("Title") or row_l.get("name") or row_l.get("название")
                        orig_title = row.get("Original Title") or row_l.get("original title")
                        kp_id = row.get("backup_id") or row_l.get("id")
                        year = row.get("Year") or row_l.get("год")
                        rating = row.get("My rating") or row_l.get("rating") or row_l.get("оценка")
                        
                        if not title or not rating: continue
                        try:
                            year_int = int(year) if year and str(year).isdigit() else None
                            rating_int = int(float(rating.replace(",", ".")))
                            if rating_int == 0: continue
                            
                            t_norm = normalize_title(title)
                            o_norm = normalize_title(orig_title)
                            
                            match = None
                            # Ищем в уже загруженных из IMDb
                            for k in [(t_norm, year_int), (o_norm, year_int)]:
                                if k in merged_data:
                                    match = merged_data[k]
                                    break
                            
                            if match:
                                # Обновляем существующий (добавляем KP ID)
                                if kp_id: match["kp_id"] = kp_id
                                # Можно также обновить названия если они полнее
                                if not match["title"] and title: match["title"] = title
                                if not match["orig_title"] and orig_title: match["orig_title"] = orig_title
                            else:
                                # Создаем новый
                                item = {
                                    "title": title,
                                    "orig_title": orig_title,
                                    "year": year_int,
                                    "rating": rating_int,
                                    "imdb_id": None,
                                    "kp_id": kp_id,
                                    "title_norm": t_norm,
                                    "orig_norm": o_norm
                                }
                                if t_norm: merged_data[(t_norm, year_int)] = item
                                if o_norm: merged_data[(o_norm, year_int)] = item
                        except: pass
                except Exception as e:
                    print(f"Ошибка Кинопоиска: {e}")

        # 3. Сохраняем в базу
        print(f"Всего уникальных записей после мерджа: {len(set(id(v) for v in merged_data.values()))}")
        added = 0
        updated = 0
        
        unique_items = []
        seen_ids = set()
        for item in merged_data.values():
            if id(item) in seen_ids: continue
            seen_ids.add(id(item))
            unique_items.append(item)

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            for item in unique_items:
                # Проверяем наличие в базе по ID или Названию+Году
                cursor.execute("""
                    SELECT rowid FROM user_ratings 
                    WHERE (imdb_id IS NOT NULL AND imdb_id = ?) 
                       OR (kp_id IS NOT NULL AND kp_id = ?)
                       OR (title_norm = ? AND item_year = ?)
                       OR (original_title_norm = ? AND item_year = ?)
                """, (item["imdb_id"], item["kp_id"], item["title_norm"], item["year"], item["orig_norm"], item["year"]))
                
                existing = cursor.fetchone()
                if existing:
                    cursor.execute("""
                        UPDATE user_ratings SET 
                            imdb_id = COALESCE(imdb_id, ?),
                            kp_id = COALESCE(kp_id, ?),
                            rating = ?,
                            original_title = COALESCE(original_title, ?),
                            original_title_norm = COALESCE(original_title_norm, ?)
                        WHERE rowid = ?
                    """, (item["imdb_id"], item["kp_id"], item["rating"], item["orig_title"], item["orig_norm"], existing[0]))
                    updated += 1
                else:
                    cursor.execute("""
                        INSERT INTO user_ratings (item_title, original_title, item_year, rating, service, imdb_id, kp_id, title_norm, original_title_norm)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (item["title"], item["orig_title"], item["year"], item["rating"], "merged", item["imdb_id"], item["kp_id"], item["title_norm"], item["orig_norm"]))
                    added += 1
            conn.commit()
            
        print(f"Синхронизация завершена. Добавлено: {added}, Обновлено: {updated}")
        return added + updated


if __name__ == "__main__":
    sync = UserSync()

    # Синхронизация через оба файла сразу
    sync.sync_all_csvs("IMDBOCENKI.csv", "kinopoiskocenki.csv")

    # Выводим общее количество оценок в базе
    try:
        with sqlite3.connect("app_data.db") as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM user_ratings")
            total_ratings = cursor.fetchone()[0]
            print(f"\n==========================================")
            print(f"ИТОГО В ВАШЕЙ БАЗЕ СОХРАНЕНО: {total_ratings} ОЦЕНОК")
            print(f"==========================================\n")
    except Exception as e:
        print(f"Ошибка при подсчете оценок: {e}")
