import os
import requests
import time
from dotenv import load_dotenv

load_dotenv()
KINOPOISK_API_KEY = os.getenv("KINOPOISK_API_KEY")

class KinopoiskClient:
    def __init__(self):
        self.api_key = KINOPOISK_API_KEY
        # Используем неофициальное API Кинопоиска (kinopoiskapiunofficial.tech)
        self.base_url = "https://kinopoiskapiunofficial.tech/api"
        self.headers = {
            "X-API-KEY": self.api_key,
            "Content-Type": "application/json",
        }
        self.is_limited = False # Флаг превышения лимита на сегодня

    def get_by_id(self, kp_id):
        """
        Получает данные фильма по ID Кинопоиска (через /v2.2/films/{id})
        """
        if self.is_limited or not self.api_key:
            return None

        url = f"{self.base_url}/v2.2/films/{kp_id}"
        
        try:
            time.sleep(0.3)
            response = requests.get(url, headers=self.headers, timeout=20)
            
            if response.status_code == 402:
                self.is_limited = True
                return None

            if response.status_code == 429:
                time.sleep(5)
                return self.get_by_id(kp_id)

            response.raise_for_status()
            result = response.json()
            
            if result:
                return {
                    "kp_rating": float(result.get("ratingKinopoisk", 0) or 0.0),
                    "imdb_rating": float(result.get("ratingImdb", 0) or 0.0),
                    "poster_url": result.get("posterUrlPreview", ""),
                    "description": result.get("description", "") or result.get("shortDescription", ""),
                    "release_date": str(result.get("year", "")),
                    "title": result.get("nameRu", "") or result.get("nameEn", ""),
                    "imdb_id": result.get("imdbId", "")
                }
        except Exception as e:
            print(f"Ошибка Kinopoisk Tech get_by_id ({kp_id}): {e}")
            
        return None

    def search_movie(self, title, year=None, max_retries=3):
        """
        Ищет фильм/сериал по названию и году.
        Возвращает словарь с рейтингами КП, IMDb, постером и описанием.
        """
        if self.is_limited:
            return None # Больше не пытаемся сегодня

        if not self.api_key or self.api_key == "ТВОЙ_КЛЮЧ_СЮДА":
            print("ВНИМАНИЕ: Ключ Kinopoisk не установлен!")
            return None

        # v2.2/films - поиск
        url = f"{self.base_url}/v2.2/films"
        params = {
            "keyword": title,
            "page": 1
        }

        for attempt in range(max_retries):
            try:
                # API разрешает до 20 запросов в секунду, но лучше делать паузы
                time.sleep(0.3)
                
                response = requests.get(url, headers=self.headers, params=params, timeout=10)
                
                if response.status_code == 402:
                    print("Kinopoisk API: Лимит запросов исчерпан (402). Прекращаем запросы на сегодня.")
                    self.is_limited = True
                    return None

                if response.status_code == 429:
                    print("Kinopoisk API: Слишком много запросов. Ждем 5 секунд...")
                    time.sleep(5)
                    continue
                    
                response.raise_for_status()
                data = response.json()
                
                if data.get("items") and len(data["items"]) > 0:
                    # Фильтруем по году, если передан
                    films = data["items"]
                    if year:
                        # Пытаемся найти точное совпадение по году
                        year_match = [f for f in films if str(f.get("year", "")) == str(year)]
                        if year_match:
                            result = year_match[0]
                        else:
                            result = films[0]
                    else:
                        result = films[0]
                        
                    return {
                        "kp_rating": float(result.get("ratingKinopoisk", 0) or 0.0),
                        "imdb_rating": float(result.get("ratingImdb", 0) or 0.0),
                        "poster_url": result.get("posterUrlPreview", ""),
                        "description": result.get("description", ""), # В v2.2/films может не быть описания, но пробуем
                        "release_date": str(result.get("year", "")),
                        "title": result.get("nameRu", "") or result.get("nameEn", "")
                    }
                return None
            except Exception as e:
                print(f"Ошибка при запросе к Kinopoisk API: {e}")
                time.sleep(1)
                
        return None

if __name__ == "__main__":
    client = KinopoiskClient()
    print(client.search_movie("Матрица", 1999))
