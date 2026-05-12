import logging
import re
import time

logger = logging.getLogger("parsclode.client.tmdb")

from api_cache import get_cached_session
from settings import settings

TMDB_API_KEY = settings.tmdb_api_key


def _norm(s):
    if not s:
        return ""
    return re.sub(r"[^a-zа-яё0-9]", "", s.lower())


class TMDBClient:
    def __init__(self):
        self.api_key = settings.tmdb_api_key
        self.api_token = settings.tmdb_api_token
        self.base_url = "https://api.themoviedb.org/3"
        self.image_base_url = "https://image.tmdb.org/t/p/w500"
        self.headers = {"accept": "application/json"}
        if self.api_token:
            self.headers["Authorization"] = f"Bearer {self.api_token}"
        self.is_limited = False
        self.session = get_cached_session()

    def get_external_ids(self, media_type, tmdb_id):
        if self.is_limited:
            return None
        url = f"{self.base_url}/{media_type}/{tmdb_id}/external_ids"
        params = {}
        if not self.api_token:
            params["api_key"] = self.api_key
        try:
            time.sleep(0.1)
            resp = self.session.get(url, params=params, headers=self.headers, timeout=5)
            if resp.status_code == 200:
                return resp.json().get("imdb_id")
            logger.warning(f"TMDB external_ids({media_type}, {tmdb_id}) -> HTTP {resp.status_code}")
        except Exception as e:
            logger.error(
                f"TMDB external_ids({media_type}, {tmdb_id}) failed: {type(e).__name__}: {e}",
                exc_info=True,
            )
        return None

    def get_videos(self, media_type, tmdb_id):
        """Return TMDB /videos for a given title (8.7 trailer support).

        media_type: 'movie' or 'tv'.
        Returns: list of dicts with at least {key, name, site, type, official}.
        Tries Russian first, falls back to English/none if empty.
        """
        if not (self.api_key or self.api_token) or self.is_limited or not tmdb_id:
            return []
        if media_type not in ("movie", "tv"):
            return []
        url = f"{self.base_url}/{media_type}/{tmdb_id}/videos"
        for lang in ("ru-RU", "en-US", None):
            params = {}
            if not self.api_token:
                params["api_key"] = self.api_key
            if lang:
                params["language"] = lang
            try:
                time.sleep(0.1)
                resp = self.session.get(url, params=params, headers=self.headers, timeout=5)
                if resp.status_code in (401, 403):
                    self.is_limited = True
                    return []
                if resp.status_code == 200:
                    results = resp.json().get("results", []) or []
                    if results:
                        return results
            except Exception as e:
                logger.error(
                    f"TMDB get_videos({media_type},{tmdb_id},{lang}): {type(e).__name__}: {e}",
                    exc_info=True,
                )
        return []

    def find_by_imdb_id(self, imdb_id, return_meta=False):
        """Resolve imdb_id → TMDB title metadata (and optionally id+media_type).

        When return_meta=True the dict additionally includes 'tmdb_id' and
        'media_type' so callers (e.g. 8.7 trailers) can hit /videos
        without a second search round-trip.
        """
        if not imdb_id or self.is_limited:
            return None
        url = f"{self.base_url}/find/{imdb_id}"
        params = {
            "external_source": "imdb_id",
            "language": "ru-RU",
        }
        if not self.api_token:
            params["api_key"] = self.api_key
        try:
            time.sleep(0.1)
            response = self.session.get(url, params=params, headers=self.headers, timeout=10)
            if response.status_code in [401, 403]:
                self.is_limited = True
                return None
            if response.status_code == 200:
                data = response.json()
                movie_hits = data.get("movie_results") or []
                tv_hits = data.get("tv_results") or []
                results = movie_hits or tv_hits
                if results:
                    result = results[0]
                    poster_path = result.get("poster_path")
                    poster_url = f"{self.image_base_url}{poster_path}" if poster_path else ""
                    release_date = result.get("release_date") or result.get("first_air_date") or ""
                    out = {
                        "title": result.get("title") or result.get("name") or "",
                        "original_title": result.get("original_title")
                        or result.get("original_name")
                        or "",
                        "poster_url": poster_url,
                        "description": result.get("overview", ""),
                        "release_date": release_date,
                        "imdb_id": imdb_id,
                    }
                    if return_meta:
                        out["tmdb_id"] = result.get("id")
                        out["media_type"] = "movie" if movie_hits else "tv"
                    return out
        except Exception as e:
            logger.error(f"Ошибка TMDB find_by_imdb_id ({imdb_id}): {e}", exc_info=True)
        return None

    def search_movie(self, title, year=None, alt_title=None):
        if not (self.api_key or self.api_token) or self.is_limited:
            return None

        queries = [title]
        if alt_title:
            alt_clean = alt_title.strip()
            if alt_clean and _norm(alt_clean) != _norm(title):
                queries.append(alt_clean)

        candidates = {}

        for query in queries:
            if self.is_limited:
                break
            if any(c["score"] >= 180 for c in candidates.values()):
                break

            url = f"{self.base_url}/search/multi"
            params = {"query": query, "language": "ru-RU"}
            if not self.api_token:
                params["api_key"] = self.api_key

            for attempt in range(3):
                try:
                    time.sleep(0.3)
                    response = self.session.get(
                        url, params=params, headers=self.headers, timeout=10
                    )
                    if response.status_code == 429:
                        time.sleep(5)
                        continue
                    if response.status_code in [401, 403]:
                        self.is_limited = True
                        return None
                    response.raise_for_status()
                    data = response.json()

                    for result in data.get("results", []):
                        media_type = result.get("media_type", "movie")
                        if media_type not in ("movie", "tv"):
                            continue

                        tmdb_id = result.get("id")
                        res_title = (result.get("title") or result.get("name") or "").strip()
                        res_orig = (
                            result.get("original_title") or result.get("original_name") or ""
                        ).strip()
                        release_date = (
                            result.get("release_date") or result.get("first_air_date") or ""
                        )
                        res_year = (
                            int(release_date[:4])
                            if release_date and len(release_date) >= 4
                            else None
                        )

                        year_score = 0
                        if year and res_year:
                            if res_year == year:
                                year_score = 60
                            elif abs(res_year - year) <= 1:
                                year_score = 40
                            elif abs(res_year - year) <= 3:
                                year_score = -20
                            else:
                                year_score = -80

                        our_raw = [_norm(query)]
                        if alt_title:
                            our_raw.append(_norm(alt_title))
                        our_norms = set(n for n in our_raw if n)
                        their_norms = set(n for n in [_norm(res_title), _norm(res_orig)] if n)

                        title_score = 0
                        if our_norms & their_norms:
                            title_score = 120
                        else:
                            for our in our_norms:
                                for their in their_norms:
                                    if len(our) > 4 and (our in their or their in our):
                                        title_score = max(title_score, 40)

                        score = year_score + title_score

                        if tmdb_id not in candidates or score > candidates[tmdb_id]["score"]:
                            candidates[tmdb_id] = {
                                "score": score,
                                "tmdb_id": tmdb_id,
                                "media_type": media_type,
                                "title": res_title,
                                "original_title": res_orig,
                                "poster_url": f"{self.image_base_url}{result.get('poster_path')}"
                                if result.get("poster_path")
                                else "",
                                "description": result.get("overview", ""),
                                "release_date": release_date,
                            }
                    break
                except Exception as e:
                    logger.error(f"Ошибка при запросе к TMDB: {e}", exc_info=True)
                    time.sleep(1)

        if not candidates:
            return None

        best = max(candidates.values(), key=lambda c: c["score"])

        if best["score"] < 130:
            return None

        imdb_id = self.get_external_ids(best["media_type"], best["tmdb_id"])

        return {
            "title": best["title"],
            "original_title": best["original_title"],
            "poster_url": best["poster_url"],
            "description": best["description"],
            "release_date": best["release_date"],
            "imdb_id": imdb_id,
        }


if __name__ == "__main__":
    from logging_config import setup_logging

    setup_logging("tmdb_test")
    client = TMDBClient()
    result = client.search_movie("The Bride!", 2026, alt_title="Невеста!")
    if result:
        logger.info(f"Название: {result['title']}")
        logger.info(f"Оригинал: {result['original_title']}")
        logger.info(f"Постер: {'да' if result['poster_url'] else 'нет'}")
    else:
        logger.info("Не найдено")
