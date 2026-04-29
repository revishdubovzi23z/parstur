import os
import requests
import time
from dotenv import load_dotenv

load_dotenv()
KINOPOISK_UZ_API_KEY = os.getenv("KINOPOISK_UZ_API_KEY")

class KinopoiskUzClient:
    def __init__(self):
        self.api_key = KINOPOISK_UZ_API_KEY
        self.base_url = "https://api.kinopoiskapi.uz/v1"
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "accept": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36",
            "Content-Type": "application/json",
        }
        self.is_limited = False
        self.network_error = False

    def search_movie(self, title, year=None, max_retries=3):
        """
        Ищет фильм/сериал на kinopoiskapi.uz
        """
        if self.is_limited:
            return None

        if not self.api_key:
            return None

        url = f"{self.base_url}/kinopoisk/movie/search"
        params = {
            "name": title,
            "page": 1,
            "limit": 5
        }

        for attempt in range(max_retries):
            try:
                self.network_error = False
                time.sleep(0.3)
                response = requests.get(url, headers=self.headers, params=params, timeout=10)
                
                # Обычно возвращают 401/403 если ключ не тот или лимит
                if response.status_code in [401, 402, 403, 429]:
                    if response.status_code == 429:
                        print("Kinopoisk UZ API: Превышен лимит запросов в секунду. Ждем...")
                        time.sleep(2)
                        continue
                    print(f"Kinopoisk UZ API: Доступ ограничен ({response.status_code}).")
                    self.is_limited = True
                    return None

                response.raise_for_status()
                data = response.json()
                
                # Структура: data.data или data.items
                items = []
                if isinstance(data, dict):
                    items = data.get("data", []) or data.get("items", [])
                elif isinstance(data, list):
                    items = data

                if items:
                    # Фильтруем по году
                    result = items[0]
                    if year:
                        year_match = [f for f in items if str(f.get("year_production", "")) == str(year)]
                        if year_match:
                            result = year_match[0]

                    return {
                        "kp_rating": float(result.get("kino_poisk_rating", 0) or 0.0),
                        "imdb_rating": float(result.get("imdb_rating", 0) or 0.0),
                        "poster_url": result.get("poster", ""),
                        "description": result.get("description", ""),
                        "release_date": str(result.get("year_production", "")),
                        "title": result.get("name_ru", "") or result.get("name_original", "")
                    }
                return None
            except Exception as e:
                print(f"Ошибка при запросе к Kinopoisk UZ API: {e}")
                self.network_error = True
                time.sleep(1)
                
        return None

if __name__ == "__main__":
    # Тест
    client = KinopoiskUzClient()
    print(client.search_movie("Матрица", 1999))
