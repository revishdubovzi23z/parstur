"""
Скрипт для исправления imdb_id в items:
Если у item в items есть imdb_id который не совпадает ни с одним external_id в user_ratings,
пробуем через TMDB найти "главный" IMDb ID сериала (часто id сезона != id сериала).
Запускать вручную при необходимости.
"""
import sqlite3
import requests
import os
import time
from dotenv import load_dotenv
load_dotenv()

TMDB_KEY = os.getenv("TMDB_API_KEY")

def get_main_imdb_id(imdb_id):
    """Для ID сезона/эпизода пробует найти главный ID сериала через TMDB"""
    if not TMDB_KEY or not imdb_id:
        return None
    try:
        resp = requests.get(
            f"https://api.themoviedb.org/3/find/{imdb_id}",
            params={"api_key": TMDB_KEY, "external_source": "imdb_id"},
            timeout=10
        )
        data = resp.json()
        # Если это сезон - в tv_season_results будет родительский id
        for result in data.get("tv_season_results", []):
            show_id = result.get("show_id")
            if show_id:
                # Получаем внешние ID шоу
                time.sleep(0.2)
                ext = requests.get(
                    f"https://api.themoviedb.org/3/tv/{show_id}/external_ids",
                    params={"api_key": TMDB_KEY},
                    timeout=10
                ).json()
                return ext.get("imdb_id")
        # Если это эпизод
        for result in data.get("tv_episode_results", []):
            show_id = result.get("show_id")
            if show_id:
                time.sleep(0.2)
                ext = requests.get(
                    f"https://api.themoviedb.org/3/tv/{show_id}/external_ids",
                    params={"api_key": TMDB_KEY},
                    timeout=10
                ).json()
                return ext.get("imdb_id")
    except Exception as e:
        print(f"  Ошибка TMDB: {e}")
    return None

conn = sqlite3.connect('app_data.db')
c = conn.cursor()

# Берем все external_id из user_ratings
c.execute("SELECT DISTINCT external_id FROM user_ratings WHERE external_id IS NOT NULL")
known_ids = set(row[0] for row in c.fetchall())
print(f"Known rated IDs: {len(known_ids)}")

# Находим items у которых imdb_id НЕ совпадает с ratings
c.execute("SELECT id, title, year, imdb_id FROM items WHERE imdb_id IS NOT NULL AND imdb_id != '' AND imdb_id NOT IN (SELECT external_id FROM user_ratings WHERE external_id IS NOT NULL)")
items = c.fetchall()
print(f"Items with unmatched imdb_id: {len(items)}")

fixed = 0
for item_id, title, year, imdb_id in items[:20]:  # Первые 20 для теста
    main_id = get_main_imdb_id(imdb_id)
    if main_id and main_id != imdb_id and main_id in known_ids:
        print(f"  FIXING: {title!r} ({year}): {imdb_id} -> {main_id}")
        c.execute("UPDATE items SET imdb_id = ? WHERE id = ?", (main_id, item_id))
        fixed += 1
        time.sleep(0.3)

conn.commit()
conn.close()
print(f"\nFixed {fixed} items")
