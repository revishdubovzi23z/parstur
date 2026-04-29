import os
import requests
import time
from dotenv import load_dotenv
from api_cache import get_cached_session

load_dotenv()
TMDB_API_KEY = os.getenv("TMDB_API_KEY")


class TMDBClient:
    def __init__(self):
        self.api_key = TMDB_API_KEY
        self.base_url = "https://api.themoviedb.org/3"
        self.image_base_url = "https://image.tmdb.org/t/p/w500"
        self.headers = {"accept": "application/json"}
        self.is_limited = False
        self.session = get_cached_session()

    def get_external_ids(self, media_type, tmdb_id):
        """Получает внешние ID (IMDb) для объекта"""
        if self.is_limited:
            return None
        url = f"{self.base_url}/{media_type}/{tmdb_id}/external_ids"
        params = {"api_key": self.api_key}
        try:
            time.sleep(0.1)
            resp = self.session.get(url, params=params, timeout=5)
            if resp.status_code == 200:
                return resp.json().get("imdb_id")
        except:
            pass
        return None

    def find_by_imdb_id(self, imdb_id):
        """Находит объект в TMDB по IMDb ID"""
        if not imdb_id or self.is_limited:
            return None
        url = f"{self.base_url}/find/{imdb_id}"
        params = {
            "api_key": self.api_key,
            "external_source": "imdb_id",
            "language": "ru-RU",
        }
        try:
            time.sleep(0.1)
            response = self.session.get(url, params=params, timeout=10)
            if response.status_code in [401, 403]:
                self.is_limited = True
                return None
            if response.status_code == 200:
                data = response.json()
                # find возвращает списки: movie_results, tv_results и т.д.
                results = data.get("movie_results") or data.get("tv_results") or []
                if results:
                    result = results[0]
                    poster_path = result.get("poster_path")
                    poster_url = (
                        f"{self.image_base_url}{poster_path}" if poster_path else ""
                    )
                    release_date = (
                        result.get("release_date") or result.get("first_air_date") or ""
                    )

                    return {
                        "title": result.get("title") or result.get("name") or "",
                        "original_title": result.get("original_title")
                        or result.get("original_name")
                        or "",
                        "rating": result.get("vote_average", 0.0),
                        "poster_url": poster_url,
                        "description": result.get("overview", ""),
                        "release_date": release_date,
                        "imdb_id": imdb_id,
                    }
        except Exception as e:
            print(f"Ошибка TMDB find_by_imdb_id ({imdb_id}): {e}")
        return None

    def search_movie(self, title, year=None, max_retries=3):
        if not self.api_key or self.api_key == "ТВОЙ_КЛЮЧ_СЮДА" or self.is_limited:
            print("ВНИМАНИЕ: Ключ TMDB не установлен!")
            return None

        url = f"{self.base_url}/search/multi"
        params = {"api_key": self.api_key, "query": title, "language": "ru-RU"}
        if year:
            params["year"] = year

        for attempt in range(max_retries):
            try:
                time.sleep(0.3)
                response = self.session.get(url, params=params, timeout=10)

                if response.status_code == 429:
                    time.sleep(5)
                    continue

                response.raise_for_status()
                data = response.json()

                if data.get("results"):
                    result = data["results"][0]
                    media_type = result.get("media_type", "movie")
                    tmdb_id = result.get("id")

                    # Пытаемся получить IMDb ID
                    imdb_id = self.get_external_ids(media_type, tmdb_id)

                    poster_path = result.get("poster_path")
                    poster_url = (
                        f"{self.image_base_url}{poster_path}" if poster_path else ""
                    )
                    release_date = (
                        result.get("release_date") or result.get("first_air_date") or ""
                    )

                    return {
                        "title": result.get("title") or result.get("name") or "",
                        "original_title": result.get("original_title")
                        or result.get("original_name")
                        or "",
                        "rating": result.get("vote_average", 0.0),
                        "poster_url": poster_url,
                        "description": result.get("overview", ""),
                        "release_date": release_date,
                        "imdb_id": imdb_id,
                    }
                return None
            except Exception as e:
                print(f"Ошибка при запросе к TMDB: {e}")
                time.sleep(1)
        return None


if __name__ == "__main__":
    import sys
    import codecs

    if sys.stdout.encoding != "utf-8":
        sys.stdout = codecs.getwriter("utf-8")(sys.stdout.buffer, "strict")

    client = TMDBClient()
    print("Ищем фильм 'Dune: Part Two' (2024)...")
    result = client.search_movie("Dune: Part Two", 2024)

    if result:
        print("\nФильм найден!")
        print(f"Название: {result['title']}")
        print(f"Оценка: {result['rating']}/10")
        print(f"Описание: {result['description'][:100]}...")
        print(f"Постер: {result['poster_url']}")
    else:
        print("\nФильм не найден или неверный ключ API.")
