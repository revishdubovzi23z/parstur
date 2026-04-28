import os
import requests
import time
from dotenv import load_dotenv

load_dotenv()
POISKKINO_API_KEY = os.getenv("POISKKINO_API_KEY")

class PoiskKinoClient:
    def __init__(self):
        self.api_key = POISKKINO_API_KEY
        self.base_url = "https://api.poiskkino.dev/v1.4"
        self.headers = {
            "X-API-KEY": self.api_key,
            "accept": "application/json",
            "Content-Type": "application/json",
        }
        self.is_limited = False

    def get_by_id(self, kp_id):
        """
        Получает данные фильма по ID Кинопоиска
        """
        if self.is_limited or not self.api_key:
            return None

        url = f"{self.base_url}/movie/{kp_id}"
        
        try:
            time.sleep(0.3)
            response = requests.get(url, headers=self.headers, timeout=20)
            
            if response.status_code in [401, 403, 429]:
                self.is_limited = True
                return None

            response.raise_for_status()
            result = response.json()
            
            if result:
                rating = result.get("rating") or {}
                poster = result.get("poster") or {}
                ext_ids = result.get("externalId") or {}
                
                return {
                    "kp_rating": float(rating.get("kp", 0) or 0.0),
                    "imdb_rating": float(rating.get("imdb", 0) or 0.0),
                    "poster_url": poster.get("url") or poster.get("previewUrl", ""),
                    "description": result.get("description", "") or result.get("shortDescription", ""),
                    "release_date": str(result.get("year", "")),
                    "title": result.get("name") or result.get("alternativeName") or result.get("enName", ""),
                    "imdb_id": ext_ids.get("imdb", "")
                }
        except Exception as e:
            print(f"Ошибка PoiskKino get_by_id ({kp_id}): {e}")
            
        return None

    def search_movie(self, title, year=None, max_retries=3):
        """
        Ищет фильм/сериал на poiskkino.dev
        """
        if self.is_limited or not self.api_key:
            return None

        # /v1.4/movie/search?query=...
        url = f"{self.base_url}/movie/search"
        params = {
            "query": title,
            "page": 1,
            "limit": 5
        }

        for attempt in range(max_retries):
            try:
                time.sleep(0.3)
                response = requests.get(url, headers=self.headers, params=params, timeout=20)
                
                # 403 / 401 - проблемы с ключом или лимитом
                if response.status_code in [401, 403, 429]:
                    print(f"PoiskKino API: Доступ ограничен или лимит исчерпан ({response.status_code}).")
                    self.is_limited = True
                    return None

                response.raise_for_status()
                data = response.json()
                
                # Структура: { "docs": [...] }
                items = data.get("docs", [])
                if items:
                    # Фильтруем по году
                    result = items[0]
                    if year:
                        year_match = [f for f in items if str(f.get("year", "")) == str(year)]
                        if year_match:
                            result = year_match[0]

                    # Извлекаем рейтинги и постер (с защитой от None)
                    rating = result.get("rating") or {}
                    poster = result.get("poster") or {}
                    ext_ids = result.get("externalId") or {}
                    
                    return {
                        "kp_rating": float(rating.get("kp", 0) or 0.0),
                        "imdb_rating": float(rating.get("imdb", 0) or 0.0),
                        "poster_url": poster.get("url") or poster.get("previewUrl", ""),
                        "description": result.get("description", "") or result.get("shortDescription", ""),
                        "release_date": str(result.get("year", "")),
                        "title": result.get("name") or result.get("alternativeName") or result.get("enName", ""),
                        "imdb_id": ext_ids.get("imdb", "")
                    }
                return None
            except Exception as e:
                print(f"Ошибка при запросе к PoiskKino API: {e}")
                time.sleep(1)
                
        return None

if __name__ == "__main__":
    client = PoiskKinoClient()
    print(client.search_movie("Матрица", 1999))
