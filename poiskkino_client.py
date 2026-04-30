import os
import time
from dotenv import load_dotenv
from base_client import BaseMovieClient

load_dotenv()
POISKKINO_API_KEY = os.getenv("POISKKINO_API_KEY")


class PoiskKinoClient(BaseMovieClient):
    rate_limit_codes = [401, 403, 429]
    payment_limit_codes = []

    def __init__(self):
        self.api_key = POISKKINO_API_KEY
        self.base_url = "https://api.poiskkino.dev/v1.4"
        self.headers = self._build_headers()
        super().__init__()

    def _get_api_key(self):
        return bool(self.api_key)

    def _build_headers(self):
        return {
            "X-API-KEY": self.api_key,
            "accept": "application/json",
            "Content-Type": "application/json",
        }

    def _parse_result(self, result):
        rating = result.get("rating") or {}
        poster = result.get("poster") or {}
        ext_ids = result.get("externalId") or {}

        return {
            "kp_rating": float(rating.get("kp", 0) or 0.0),
            "imdb_rating": float(rating.get("imdb", 0) or 0.0),
            "poster_url": poster.get("url") or poster.get("previewUrl", ""),
            "description": result.get("description", "")
            or result.get("shortDescription", ""),
            "release_date": str(result.get("year", "")),
            "title": result.get("name")
            or result.get("alternativeName")
            or result.get("enName", ""),
            "imdb_id": ext_ids.get("imdb", ""),
        }

    def get_by_id(self, kp_id):
        url = f"{self.base_url}/movie/{kp_id}"
        try:
            result = self._request(url, timeout=20)
            if result:
                return self._parse_result(result)
        except Exception as e:
            print(f"Ошибка PoiskKino get_by_id ({kp_id}): {e}")
            self.network_error = True
        return None

    def search_movie(self, title, year=None, max_retries=3):
        url = f"{self.base_url}/movie/search"
        params = {"query": title, "page": 1, "limit": 5}

        for attempt in range(max_retries):
            try:
                data = self._request(url, params=params, timeout=20)
                if data is None:
                    return None

                items = data.get("docs", [])
                if items:
                    result = items[0]
                    if year:
                        year_match = [
                            f for f in items if str(f.get("year", "")) == str(year)
                        ]
                        if year_match:
                            result = year_match[0]

                    return self._parse_result(result)
                return None
            except Exception as e:
                print(f"Ошибка при запросе к PoiskKino API: {e}")
                self.network_error = True
                time.sleep(1)

        return None


if __name__ == "__main__":
    client = PoiskKinoClient()
    print(client.search_movie("Матрица", 1999))
