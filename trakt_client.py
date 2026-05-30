import logging

from api_cache import get_cached_session
from db import db
from settings import settings

logger = logging.getLogger("parsclode.client.trakt")


class TraktClient:
    def __init__(self):
        self.client_id = settings.trakt_client_id
        self.client_secret = settings.trakt_client_secret
        self.redirect_uri = "urn:ietf:wg:oauth:2.0:oob"
        self.base_url = "https://api.trakt.tv"
        self.session = get_cached_session()
        self.access_token = self._load_token()

    def _load_token(self):
        try:
            with db._conn() as c:
                row = c.execute(
                    "SELECT value FROM app_state WHERE key = 'trakt_access_token'"
                ).fetchone()
                return row[0] if row else None
        except Exception:
            return None

    def _save_token(self, token):
        self.access_token = token
        with db._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO app_state (key, value) VALUES (?, ?)",
                ("trakt_access_token", token),
            )

    @property
    def headers(self):
        h = {
            "Content-Type": "application/json",
            "trakt-api-version": "2",
            "trakt-api-key": self.client_id,
        }
        if self.access_token:
            h["Authorization"] = f"Bearer {self.access_token}"
        return h

    def _request(self, method, url, **kwargs):
        import time

        max_retries = 3
        for attempt in range(max_retries):
            try:
                if method == "GET":
                    resp = self.session.get(url, **kwargs)
                else:
                    resp = self.session.post(url, **kwargs)

                if resp.status_code == 429:
                    retry_after = int(resp.headers.get("Retry-After", 5))
                    logger.warning(
                        f"Trakt API rate limited (HTTP 429). Waiting {retry_after}s before retry {attempt + 1}/{max_retries}..."
                    )
                    time.sleep(retry_after)
                    continue
                elif resp.status_code == 420:
                    logger.error(
                        f"Trakt Account Limit Exceeded (HTTP 420). You may need Trakt VIP to increase limits (e.g., max custom lists). Details: {resp.text}"
                    )
                    return resp

                return resp
            except Exception as e:
                logger.error(f"Trakt API request error: {e}")
                if attempt == max_retries - 1:
                    raise
                time.sleep(2)
        return resp

    def get_auth_url(self):
        return f"https://trakt.tv/oauth/authorize?response_type=code&client_id={self.client_id}&redirect_uri={self.redirect_uri}"

    def exchange_code(self, pin_code):
        url = f"{self.base_url}/oauth/token"
        payload = {
            "code": pin_code,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "redirect_uri": self.redirect_uri,
            "grant_type": "authorization_code",
        }
        resp = self._request("POST", url, json=payload)
        if resp and resp.status_code == 200:
            data = resp.json()
            self._save_token(data["access_token"])
            logger.info("Successfully authenticated with Trakt.tv!")
            return True
        else:
            logger.error(f"Trakt auth failed: {resp.text if resp else 'Unknown error'}")
            return False

    def sync_history(self, movies=None, shows=None):
        """Add items to history (watched). payload: {"movies": [...], "shows": [...]}"""
        if not self.access_token:
            return None
        url = f"{self.base_url}/sync/history"
        payload = {}
        if movies:
            payload["movies"] = movies
        if shows:
            payload["shows"] = shows

        resp = self._request("POST", url, json=payload, headers=self.headers, timeout=60)
        if resp and resp.status_code == 201:
            return resp.json()
        if resp:
            logger.error(f"Trakt sync_history failed: HTTP {resp.status_code} - {resp.text}")
        return None

    def sync_ratings(self, movies=None, shows=None):
        """Add 1-10 ratings. payload: {"movies": [...], "shows": [...]}"""
        if not self.access_token:
            return None
        url = f"{self.base_url}/sync/ratings"
        payload = {}
        if movies:
            payload["movies"] = movies
        if shows:
            payload["shows"] = shows

        resp = self._request("POST", url, json=payload, headers=self.headers, timeout=60)
        if resp and resp.status_code == 201:
            return resp.json()
        if resp:
            logger.error(f"Trakt sync_ratings failed: HTTP {resp.status_code} - {resp.text}")
        return None

    def get_custom_lists(self):
        """Get all custom lists created by the user."""
        if not self.access_token:
            return []
        url = f"{self.base_url}/users/me/lists"
        resp = self._request("GET", url, headers=self.headers, timeout=15)
        if resp and resp.status_code == 200:
            return resp.json()
        if resp:
            logger.error(f"Trakt get_custom_lists failed: HTTP {resp.status_code} - {resp.text}")
        return []

    def create_custom_list(self, name, description="", privacy="private"):
        """Create a new custom list on Trakt."""
        if not self.access_token:
            return None
        url = f"{self.base_url}/users/me/lists"
        payload = {"name": name, "description": description, "privacy": privacy}
        resp = self._request("POST", url, json=payload, headers=self.headers, timeout=15)
        if resp and resp.status_code == 201:
            return resp.json()
        if resp:
            if resp.status_code == 420:
                logger.error(
                    f"Cannot create Trakt list '{name}': Account limit exceeded (HTTP 420). You may need Trakt VIP or to delete existing lists."
                )
            else:
                logger.error(
                    f"Trakt create_custom_list failed: HTTP {resp.status_code} - {resp.text}"
                )
        return None

    def get_custom_list_items(self, list_id):
        """Get all items inside a custom list."""
        if not self.access_token:
            return []
        url = f"{self.base_url}/users/me/lists/{list_id}/items"
        resp = self._request("GET", url, headers=self.headers, timeout=15)
        if resp and resp.status_code == 200:
            return resp.json()
        if resp:
            logger.error(
                f"Trakt get_custom_list_items failed: HTTP {resp.status_code} - {resp.text}"
            )
        return []

    def add_items_to_custom_list(self, list_id, movies=None, shows=None):
        """Add items (movies, shows) to a custom list."""
        if not self.access_token or not (movies or shows):
            return None
        url = f"{self.base_url}/users/me/lists/{list_id}/items"
        payload = {}
        if movies:
            payload["movies"] = movies
        if shows:
            payload["shows"] = shows
        resp = self._request("POST", url, json=payload, headers=self.headers, timeout=30)
        if resp and resp.status_code in (200, 201):
            return resp.json()
        if resp:
            logger.error(
                f"Trakt add_items_to_custom_list failed: HTTP {resp.status_code} - {resp.text}"
            )
        return None

    def remove_items_from_custom_list(self, list_id, movies=None, shows=None):
        """Remove items (movies, shows) from a custom list."""
        if not self.access_token or not (movies or shows):
            return None
        url = f"{self.base_url}/users/me/lists/{list_id}/items/remove"
        payload = {}
        if movies:
            payload["movies"] = movies
        if shows:
            payload["shows"] = shows
        resp = self._request("POST", url, json=payload, headers=self.headers, timeout=30)
        if resp and resp.status_code in (200, 201):
            return resp.json()
        if resp:
            logger.error(
                f"Trakt remove_items_from_custom_list failed: HTTP {resp.status_code} - {resp.text}"
            )
        return None
