import logging
import time

import requests

from api_cache import get_cached_session


class BaseMovieClient:
    base_url: str = ""
    headers: dict = {}
    is_limited: bool = False
    rate_limit_codes: list = [429]
    payment_limit_codes: list = [401, 402, 403]
    request_delay: float = 0.3

    def __init__(self):
        self.logger = logging.getLogger(f"parsclode.client.{self.__class__.__name__.lower()}")
        self.session = get_cached_session()
        # network_error: True when the upstream is *temporarily* down
        # (5xx, timeout, connection reset). Callers gate checked_* DB
        # writes on this so the row is re-queued next sync run.
        self.network_error = False
        # not_found: True when the upstream definitively says the
        # resource doesn't exist (404). Gives callers a way to mark
        # checked_* without losing the can-retry-later signal.
        self.not_found = False
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
            self.logger.error(f"Лимит запросов исчерпан ({response.status_code}).")
            self.is_limited = True
            return True
        if response.status_code in self.rate_limit_codes:
            self.logger.warning("Слишком много запросов. Ждем 5 секунд...")
            time.sleep(5)
            return True
        return False

    def _request(self, url, params=None, timeout=20):
        if self._check_limited():
            return None

        self.network_error = False
        self.not_found = False

        try:
            response = self.session.get(url, headers=self.headers, params=params, timeout=timeout)
        except (requests.Timeout, requests.ConnectionError) as e:
            # Treat transport-level failures as transient. Callers
            # check self.network_error before flipping checked_* = 1.
            self.logger.error(f"transient network error ({type(e).__name__}); will retry next run")
            self.network_error = True
            return None

        # 3.16: only respect request_delay on a real (non-cached) hit.
        # CachedSession's responses expose .from_cache=True; the throttle
        # is only useful to avoid hammering the upstream, so don't
        # pay it on a local cache lookup.
        if not getattr(response, "from_cache", False):
            time.sleep(self.request_delay)

        if self._handle_rate_limit(response):
            return None

        # 3.17: differentiate 404 from 5xx/timeout. Previously every
        # non-2xx flowed into raise_for_status(); the caller could only
        # tell 'something went wrong' and the calling sync script
        # blanket-set checked_* = 1, so a transient 502 made the row
        # un-retryable until the user manually rebuilt the catalog.
        if response.status_code == 404:
            self.not_found = True
            return None
        if 500 <= response.status_code < 600:
            self.logger.warning(f"upstream {response.status_code}; treating as transient")
            self.network_error = True
            return None

        response.raise_for_status()
        return response.json()

    def get_by_id(self, kp_id):
        raise NotImplementedError

    def search_movie(self, title, year=None, max_retries=3):
        raise NotImplementedError
