import time
from api_cache import get_cached_session


class BaseMovieClient:
    base_url: str = ""
    headers: dict = {}
    is_limited: bool = False
    rate_limit_codes: list = [429]
    payment_limit_codes: list = [402]
    request_delay: float = 0.3

    def __init__(self):
        self.session = get_cached_session()
        self.network_error = False
        self._init_done = False

    def _check_limited(self):
        if self.is_limited:
            return True
        if not self._get_api_key():
            return True
        return False

    def _get_api_key(self):
        raise NotImplementedError

    def _build_headers(self):
        raise NotImplementedError

    def _handle_rate_limit(self, response):
        if response.status_code in self.payment_limit_codes:
            print(
                f"{self.__class__.__name__}: Лимит запросов исчерпан ({response.status_code})."
            )
            self.is_limited = True
            return True
        if response.status_code in self.rate_limit_codes:
            print(
                f"{self.__class__.__name__}: Слишком много запросов. Ждем 5 секунд..."
            )
            time.sleep(5)
            return True
        return False

    def _request(self, url, params=None, timeout=20):
        if self._check_limited():
            return None

        self.network_error = False
        time.sleep(self.request_delay)

        response = self.session.get(
            url, headers=self.headers, params=params, timeout=timeout
        )

        if self._handle_rate_limit(response):
            return None

        response.raise_for_status()
        return response.json()

    def get_by_id(self, kp_id):
        raise NotImplementedError

    def search_movie(self, title, year=None, max_retries=3):
        raise NotImplementedError
