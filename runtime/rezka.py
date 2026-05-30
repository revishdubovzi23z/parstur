"""HdRezka session lifecycle and helpers.

This module owns the long-lived `HdRezkaSession` used to authenticate
against rezka.ag for favorites / folders operations. It exposes:

* `rezka_session` — current `HdRezkaSession` (or `None`).
* `rezka_session_folders_cache` — mapping of `normalize_title(folder)`
  to favorite-folder cat_id, refreshed after login or any folder
  mutation.
* `rezka_session_state` — UI-facing state string ("down" / "connecting"
  / "up").
* `_init_rezka_session()` — log in synchronously and refresh the
  folders cache.
* `_rezka_session_dead(resp)` — content-/status-level sniffer that
  decides whether a previous response indicates the cookie expired.
* `_rezka_request(method, url, **kwargs)` — thin requests.request
  wrapper that detects logout, re-logs in once, and retries with
  fresh cookies.
* `_refresh_rezka_folders_cache()` — re-read /favorites/ and rebuild
  the cache.
* `_recover_rezka_url(item_id, old_url)` — search rezka for a moved
  item URL and patch the row in-place.
* `_get_rezka_obj(item_id, rezka_url)` — build an `HdRezkaApi` for the
  given URL, falling back to recovery if the original 404s.

All symbol names that were exposed on `main` before the decomposition
are kept under the same (`_`-prefixed) names here. Tests previously
relied on `monkeypatch.setattr(main, "rezka_session", ...)`; they now
monkeypatch `runtime.rezka` directly.
"""

from __future__ import annotations

import logging
import re

from runtime.ws import broadcast_threadsafe
from settings import settings

logger = logging.getLogger("parsclode.runtime.rezka")

rezka_session = None
rezka_session_folders_cache = None
rezka_session_state = "down"  # down, connecting, up


# Markers that identify the unauthenticated login popup HTML rezka
# serves when a cookie has expired. We content-sniff because rezka
# does NOT raise — a logged-out user gets a 200 OK with the public
# login HTML in the body.
_REZKA_LOGIN_MARKERS = (
    b"b-loginpopup",
    b"forgot_password",
    b'name="login_name"',
    b'id="login_email"',
)


def _init_rezka_session() -> bool:
    """Authenticate against rezka and refresh the folders cache.

    Returns True on success. Broadcasts the state transition via
    `runtime.ws.broadcast_threadsafe` so the SPA sidebar can show
    the live session indicator.
    """
    global rezka_session, rezka_session_folders_cache, rezka_session_state
    rezka_email = settings.rezka_email
    rezka_password = settings.rezka_password
    if not rezka_email or not rezka_password:
        logger.info("[REZKA] No credentials, session skipped")
        rezka_session_state = "down"
        return False

    rezka_session_state = "connecting"
    broadcast_threadsafe({"type": "rezka_session", "state": "connecting"})

    try:
        from proxy_manager import proxy_manager

        proxies = proxy_manager.get_requests_proxies("rezka") or {}

        from HdRezkaApi import HdRezkaSession as _Session

        rezka_session = _Session("https://rezka.ag/", proxy=proxies)
        rezka_session.login(rezka_email, rezka_password)
        _refresh_rezka_folders_cache()
        logger.info(f"[REZKA] Session initialized, cookies: {list(rezka_session.cookies.keys())}")
        rezka_session_state = "up"
        broadcast_threadsafe({"type": "rezka_session", "state": "up"})
        return True
    except Exception as e:
        logger.error(f"[REZKA] Session init failed: {e}")
        rezka_session = None
        rezka_session_state = "down"
        broadcast_threadsafe({"type": "rezka_session", "state": "down"})
        return False


def _rezka_session_dead(resp) -> bool:
    """Return True if `resp` looks like rezka has logged us out.

    Sniffs the response status, redirect target, final URL after
    follow_redirects, and finally the body for the public login-popup
    markers. The body check requires a non-trivial length so an empty
    AJAX 204 isn't misread as dead.
    """
    if resp is None:
        return False
    if resp.status_code in (401, 403):
        return True
    location = (resp.headers.get("Location") or "").lower()
    final_url = (getattr(resp, "url", "") or "").lower()
    if any(needle in location for needle in ("/login", "/auth")):
        return True
    if any(needle in final_url for needle in ("/login.html", "/auth")):
        return True
    body = resp.content or b""
    if len(body) >= 256 and any(m in body for m in _REZKA_LOGIN_MARKERS):
        return True
    return False


def _rezka_request(method: str, url: str, **kwargs):
    """`requests.request(...)` with one transparent re-login retry.

    Caller should pass `cookies=rezka_session.cookies` themselves —
    the helper updates them in-place after a successful re-login.
    Returns the final response (whether or not retry happened) or
    `None` when no rezka_session is configured.
    """
    if rezka_session is None:
        return None
    import requests as _req

    from proxy_manager import proxy_manager

    proxies = proxy_manager.get_requests_proxies("rezka")
    if proxies:
        kwargs["proxies"] = proxies

    resp = _req.request(method, url, **kwargs)
    if not _rezka_session_dead(resp):
        return resp
    logger.warning(f"[REZKA] session looks dead (status={resp.status_code}, url={url}); re-login")
    try:
        _init_rezka_session()
    except Exception as e:
        logger.error(f"[REZKA] re-login failed: {type(e).__name__}: {e}", exc_info=True)
        return resp
    if rezka_session is None:
        # Re-login itself failed (e.g. credentials wrong now).
        return resp
    # Refresh the caller's cookie jar reference so the retry has
    # the new auth cookie.
    kwargs["cookies"] = rezka_session.cookies
    return _req.request(method, url, **kwargs)


def _refresh_rezka_folders_cache() -> None:
    """Re-read /favorites/ and rebuild `rezka_session_folders_cache`."""
    global rezka_session_folders_cache
    if not rezka_session:
        return
    try:
        from bs4 import BeautifulSoup as _BS

        from app_core import normalize_title

        resp = _rezka_request(
            "GET",
            "https://rezka.ag/favorites/",
            headers={"User-Agent": "Mozilla/5.0"},
            cookies=rezka_session.cookies,
            timeout=15,
        )
        if resp is None:
            return
        soup = _BS(resp.content, "html.parser")
        sidebar = soup.find("div", class_="b-favorites_content__sidebarbar") or soup.find(
            "div", class_="b-favorites_content__sidebar"
        )
        folders: dict[str, str] = {}
        if sidebar:
            for a in sidebar.find_all("a", href=True):
                href = a.get("href", "")
                if "javascript" in href:
                    continue
                text = a.text.strip()
                name = re.sub(r"\s*\(\d+\)", "", text).strip()
                m = re.search(r"/favorites/(\d+)/", href)
                if m:
                    folders[normalize_title(name)] = m.group(1)
        rezka_session_folders_cache = folders
        logger.info(f"[REZKA] Folders cache refreshed: {len(folders)} folders")
    except Exception as e:
        logger.error(f"[REZKA] Folders cache refresh failed: {e}", exc_info=True)
        rezka_session_folders_cache = None


def _recover_rezka_url(item_id: int, old_url: str) -> str | None:
    """Search rezka for a moved-item URL and patch the row in-place.

    Rezka periodically rewrites the slug portion of a URL (e.g. when
    a title is re-released or the editor renames it). The numeric
    post id is stable, so we re-search for the title and pick the
    first hit whose post id matches. Returns the new URL when one
    is found, else `None`.
    """
    from HdRezkaApi.search import HdRezkaSearch

    from app_core import clean_title_for_search
    from db import db

    row = (
        db.get_connection()
        .cursor()
        .execute("SELECT title, year FROM items WHERE id = ?", (item_id,))
        .fetchone()
    )
    if not row:
        return None
    title = row["title"]
    year = row["year"]

    clean = clean_title_for_search(title)
    if not clean:
        return None
    try:
        from proxy_manager import proxy_manager

        proxies = proxy_manager.get_requests_proxies("rezka") or {}
        results = HdRezkaSearch("https://rezka.ag", proxy=proxies)(f"{clean} {year}")
    except Exception:
        return None
    old_num = re.search(r"/(\d+)-", old_url)
    if not old_num:
        return None
    old_id = old_num.group(1)
    for r in results:
        url = r.get("url", "")
        if not url or url == old_url:
            continue
        new_num = re.search(r"/(\d+)-", url)
        if new_num and new_num.group(1) == old_id:
            conn = db.get_connection()
            conn.execute("UPDATE items SET rezka_url = ? WHERE id = ?", (url, item_id))
            conn.commit()
            conn.close()
            logger.info(f"[REZKA] URL recovered for item {item_id}: {old_url} -> {url}")
            return url
    return None


def _get_rezka_obj(item_id: int, rezka_url: str):
    """Build an `HdRezkaApi` for `rezka_url`, recovering moved URLs.

    Tries the original URL first; on failure falls back to
    `_recover_rezka_url` and retries with the patched URL. Returns
    a `(HdRezkaApi | None, str)` tuple where the second element is
    the URL that ultimately produced (or failed for) the object.
    """
    from HdRezkaApi import HdRezkaApi

    from proxy_manager import proxy_manager

    cookies = rezka_session.cookies if rezka_session else {"hdmbbs": "1"}
    proxies = proxy_manager.get_requests_proxies("rezka") or {}
    try:
        rezka = HdRezkaApi(rezka_url, cookies=cookies, proxy=proxies)
        if rezka.ok:
            return rezka, rezka_url
    except Exception:
        pass
    new_url = _recover_rezka_url(item_id, rezka_url)
    if new_url:
        try:
            rezka = HdRezkaApi(new_url, cookies=cookies, proxy=proxies)
            if rezka.ok:
                return rezka, new_url
        except Exception:
            pass
    return None, rezka_url
