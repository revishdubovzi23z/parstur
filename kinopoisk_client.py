import time

from base_client import BaseMovieClient
from settings import settings

KINOPOISK_API_KEY = settings.kinopoisk_api_key


class KinopoiskClient(BaseMovieClient):
    rate_limit_codes = [429]
    payment_limit_codes = [402]

    def __init__(self):
        self.api_key = KINOPOISK_API_KEY
        self.base_url = "https://kinopoiskapiunofficial.tech/api"
        self.headers = self._build_headers()
        super().__init__()

    def _get_api_key(self):
        return self.api_key and self.api_key != "ТВОЙ_КЛЮЧ_СЮДА"

    def _build_headers(self):
        return {
            "X-API-KEY": self.api_key,
            "Content-Type": "application/json",
        }

    def _parse_result(self, result):
        return {
            "kp_rating": float(result.get("ratingKinopoisk", 0) or 0.0),
            "imdb_rating": float(result.get("ratingImdb", 0) or 0.0),
            "poster_url": result.get("posterUrlPreview", ""),
            "description": result.get("description", "") or result.get("shortDescription", ""),
            "release_date": str(result.get("year", "")),
            "title": result.get("nameRu", "") or result.get("nameEn", ""),
            "imdb_id": result.get("imdbId", ""),
        }

    def get_by_id(self, kp_id):
        url = f"{self.base_url}/v2.2/films/{kp_id}"
        try:
            result = self._request(url, timeout=20)
            if result:
                return self._parse_result(result)
        except Exception as e:
            print(f"Ошибка Kinopoisk Tech get_by_id ({kp_id}): {e}")
            self.network_error = True
        return None

    def search_movie(self, title, year=None, max_retries=3):
        url = f"{self.base_url}/v2.2/films"
        params = {"keyword": title, "page": 1}

        for attempt in range(max_retries):
            try:
                data = self._request(url, params=params, timeout=10)
                if data is None:
                    return None

                if data.get("items") and len(data["items"]) > 0:
                    films = data["items"]
                    if year:
                        year_match = [f for f in films if str(f.get("year", "")) == str(year)]
                        result = year_match[0] if year_match else films[0]
                    else:
                        result = films[0]

                    return self._parse_result(result)
                return None
            except Exception as e:
                print(f"Ошибка при запросе к Kinopoisk API (поиск): {e}")
                self.network_error = True
                time.sleep(1)

        return None


if __name__ == "__main__":
    client = KinopoiskClient()
    print(client.search_movie("Матрица", 1999))
