"""kino.pub OAuth Device Flow state machine.

Owns the auth lifecycle that sits between the stateless
`kinopub_client.KinopubClient` (HTTP only) and the `routes/kinopub.py`
endpoints / UI. Responsibilities:

* Remember pending device codes in process memory (no point persisting
  them — they expire after ~10 minutes).
* On confirmation, persist the (access_token, refresh_token,
  expires_at) tuple to `kinopub_auth` via the DB mixin.
* On request for a fresh access_token, refresh in-place if it's near
  expiry. Refresh failures wipe the stored credentials and surface as
  "not authenticated" — the operator must restart the Device Flow.
* On startup, detect operator-rotated `client_secret` (the stored
  `client_secret_sha256` no longer matches `settings.kinopub_client_secret`)
  and drop the stored tokens — they'd be useless anyway.

What this module does NOT do:
  * No HTTP. All wire-level concerns live in `kinopub_client.py`.
  * No FastAPI types. Endpoints translate between this and HTTP.
  * No background polling. The UI polls /device/poll on its own
    cadence; we just answer "is this code confirmed yet?" idempotently.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any

from db import db
from kinopub_client import (
    DeviceCode,
    KinopubAPIError,
    KinopubAuthError,
    KinopubAuthExpiredError,
    KinopubAuthPendingError,
    KinopubClient,
    KinopubError,
    KinopubRateLimitError,
    TokenPair,
)
from settings import settings

logger = logging.getLogger("parsclode.runtime.kinopub")


# ---------------------------------------------------------------------------
# Pending device-flow attempts (process-local).
#
# A user starts the flow → we cache the DeviceCode + a `started_at`
# timestamp keyed by `device_code`. The UI polls /device/poll(device_code).
# Entries auto-expire — see _gc_pending().


@dataclass
class _PendingDeviceCode:
    code: DeviceCode
    started_at: float


_pending: dict[str, _PendingDeviceCode] = {}
_pending_lock = threading.Lock()


def _gc_pending(now: float) -> None:
    """Drop pending entries whose device_code has expired."""
    with _pending_lock:
        expired = [dc for dc, p in _pending.items() if now > p.started_at + p.code.expires_in]
        for dc in expired:
            _pending.pop(dc, None)


# ---------------------------------------------------------------------------
# Public state machine


def is_enabled() -> bool:
    """Master switch — KINOPUB_ENABLED env."""
    return bool(settings.kinopub_enabled)


def get_status() -> dict:
    """Snapshot of auth state for the UI.

    Returns:
        {
            "enabled": bool,
            "authenticated": bool,
            "expires_at": float | None,
            "expires_in": int | None,        # seconds, never negative
            "client_id": str | None,
        }
    """
    if not is_enabled():
        return {
            "enabled": False,
            "authenticated": False,
            "expires_at": None,
            "expires_in": None,
            "client_id": None,
        }
    row = db.kinopub_auth_get()
    if row is None:
        return {
            "enabled": True,
            "authenticated": False,
            "expires_at": None,
            "expires_in": None,
            "client_id": settings.kinopub_client_id,
        }
    # If the operator rotated the secret since the token was minted,
    # the row is dead weight — surface as not-authenticated so the UI
    # nudges the user to re-auth.
    if not db.kinopub_auth_secret_matches(settings.kinopub_client_secret):
        logger.warning("[KINOPUB] stored client_secret_sha256 mismatch, dropping tokens")
        db.kinopub_auth_clear()
        return {
            "enabled": True,
            "authenticated": False,
            "expires_at": None,
            "expires_in": None,
            "client_id": settings.kinopub_client_id,
        }
    now = time.time()
    return {
        "enabled": True,
        "authenticated": True,
        "expires_at": row["expires_at"],
        "expires_in": max(0, int(row["expires_at"] - now)),
        "client_id": row["client_id"],
    }


def start_device_flow(client: KinopubClient | None = None) -> DeviceCode:
    """Kick off the Device Flow. The returned `DeviceCode` carries the
    `user_code` to show the operator, the verification URL to send them
    to, and the `device_code` to feed to `poll_device_flow`."""
    c = client or KinopubClient()
    dc = c.get_device_code()
    _gc_pending(time.time())
    with _pending_lock:
        _pending[dc.device_code] = _PendingDeviceCode(code=dc, started_at=time.time())
    logger.info(
        f"[KINOPUB] device flow started (user_code={dc.user_code!r}, expires_in={dc.expires_in}s)"
    )
    return dc


def poll_device_flow(device_code: str, client: KinopubClient | None = None) -> str:
    """Poll for confirmation. Returns one of:

    * "pending" — user hasn't entered the user_code yet
    * "confirmed" — tokens were minted and persisted
    * "expired" — the device_code is too old or unknown
    """
    now = time.time()
    _gc_pending(now)
    with _pending_lock:
        pending = _pending.get(device_code)
    if pending is None:
        return "expired"

    c = client or KinopubClient()
    try:
        tokens = c.get_device_token(device_code)
    except KinopubAuthPendingError:
        return "pending"
    except KinopubAuthExpiredError:
        with _pending_lock:
            _pending.pop(device_code, None)
        return "expired"
    except KinopubError as e:
        logger.error(f"[KINOPUB] poll_device_flow error: {type(e).__name__}: {e}")
        raise

    # Success — persist tokens, drop the pending entry.
    expires_at = now + max(60, int(tokens.expires_in))
    db.kinopub_auth_set(
        access_token=tokens.access_token,
        refresh_token=tokens.refresh_token,
        expires_at=expires_at,
        client_id=settings.kinopub_client_id,
        client_secret=settings.kinopub_client_secret,
        now=now,
    )
    with _pending_lock:
        _pending.pop(device_code, None)
    logger.info(
        f"[KINOPUB] device flow confirmed; access_token valid for {int(tokens.expires_in)}s"
    )
    return "confirmed"


def logout() -> None:
    """Clear the persisted token row. Used by /api/kinopub/logout."""
    db.kinopub_auth_clear()
    logger.info("[KINOPUB] logged out (kinopub_auth row cleared)")


def current_token(client: KinopubClient | None = None) -> str | None:
    """Return a valid access_token, refreshing if needed.

    Returns None if not authenticated or refresh fails. The caller
    (PR 3 stream_info endpoint, PR 4 sync_kinopub) is expected to
    interpret None as "tell the user to re-authenticate" rather than
    triggering refresh themselves.
    """
    if not is_enabled():
        return None
    row = db.kinopub_auth_get()
    if row is None:
        return None
    if not db.kinopub_auth_secret_matches(settings.kinopub_client_secret):
        db.kinopub_auth_clear()
        return None

    now = time.time()
    skew = settings.kinopub_refresh_skew_seconds
    if now < row["expires_at"] - skew:
        # Still fresh.
        return row["access_token"]

    # Refresh.
    c = client or KinopubClient(
        client_id=row["client_id"],
        client_secret=settings.kinopub_client_secret,
    )
    try:
        tokens: TokenPair = c.refresh_access_token(row["refresh_token"])
    except KinopubAuthExpiredError:
        logger.warning("[KINOPUB] refresh_token rejected; logging out")
        db.kinopub_auth_clear()
        return None
    except (KinopubAPIError, KinopubRateLimitError) as e:
        # Don't drop tokens on a transient network/rate-limit failure
        # — return the (possibly soon-to-expire) current one and let
        # the next request retry.
        logger.warning(f"[KINOPUB] refresh failed transiently: {type(e).__name__}: {e}")
        if now < row["expires_at"]:
            return row["access_token"]
        return None

    expires_at = time.time() + max(60, int(tokens.expires_in))
    db.kinopub_auth_set(
        access_token=tokens.access_token,
        refresh_token=tokens.refresh_token,
        expires_at=expires_at,
        client_id=row["client_id"],
        client_secret=settings.kinopub_client_secret,
        now=time.time(),
    )
    logger.info(f"[KINOPUB] access_token refreshed; valid for {int(tokens.expires_in)}s")
    return tokens.access_token


# ---------------------------------------------------------------------------
# Content lookups (PR 3 of the kino.pub integration).
#
# Both helpers take the access_token via `current_token()` rather than
# making the caller pass it in — that way every entry point goes through
# the same lazy-refresh logic. If the operator is not authenticated they
# raise `KinopubAuthError`, which `routes/kinopub.py` translates into a
# 401 so the UI can prompt for re-auth.


def _authenticated_client() -> KinopubClient:
    """Return a `KinopubClient` already wired with a fresh Bearer token.

    Raises `KinopubAuthError` when the operator is not authenticated
    or the stored refresh-token has been rejected. Callers should let
    that propagate; `routes/kinopub.py` maps it to HTTP 401.
    """
    if not is_enabled():
        raise KinopubAuthError("kino.pub integration disabled")
    token = current_token()
    if not token:
        raise KinopubAuthError("kino.pub not authenticated")
    return KinopubClient(token)


def search(
    query: str,
    *,
    year: int | None = None,
    type_: str | None = None,
    limit: int = 25,
    client: KinopubClient | None = None,
    kp_id: str | None = None,
    imdb_id: str | None = None,
) -> list[dict]:
    """`GET /v1/items/search?q=<query>` filtered by year/type.

    Returns a list of `{id, title, year, type, url, poster}` dicts in
    the shape the SPA expects. We intentionally strip the heavier
    fields from the API response (cast, plot, ratings…) so the UI
    only sees what it actually renders in the result picker.
    """
    c = client or _authenticated_client()
    raw = c.search(query, type_=type_, year=year, limit=limit)

    if imdb_id and imdb_id.startswith("tt"):
        imdb_id = imdb_id[2:]

    exact_matches: list[dict] = []
    other_matches: list[dict] = []

    for entry in raw:
        if not isinstance(entry, dict):
            continue
        ent_id = entry.get("id")
        if ent_id is None:
            continue
        posters = entry.get("posters") if isinstance(entry.get("posters"), dict) else {}
        poster = (
            posters.get("medium")
            or posters.get("small")
            or posters.get("big")
            or entry.get("poster")
            or None
        )

        # Check for exact ID match
        cand_kp = str(entry.get("kinopoisk") or "")
        cand_imdb = str(entry.get("imdb") or "")

        is_exact = False
        if kp_id and str(kp_id) == cand_kp:
            is_exact = True
        if imdb_id and str(imdb_id) == cand_imdb:
            is_exact = True

        res_item = {
            "id": int(ent_id),
            "title": str(entry.get("title") or "").strip() or None,
            "year": int(entry["year"]) if entry.get("year") else None,
            "type": str(entry.get("type") or "") or None,
            "url": _build_item_url(int(ent_id)),
            "poster": str(poster) if poster else None,
        }

        if is_exact:
            exact_matches.append(res_item)
        else:
            other_matches.append(res_item)

    return exact_matches + other_matches


def _build_item_url(kinopub_id: int) -> str:
    """Stable public URL for a kino.pub item player page. Used by the UI to render
    'Open on kino.pub' chips without round-tripping the API again."""
    return f"https://kino.pub/item/view/{int(kinopub_id)}"


def _extract_string(val: Any) -> str | None:
    """Pick the most useful string from a value that might be a dict.
    Preference: hls -> http -> title -> name -> first value."""
    if not val:
        return None
    if isinstance(val, str | int | float):
        return str(val)
    if isinstance(val, dict):
        return (
            val.get("hls")
            or val.get("http")
            or val.get("title")
            or val.get("name")
            or val.get("code")
            or next((str(v) for v in val.values() if v), None)
        )
    return str(val)


def _map_video(video: dict) -> dict:
    """Normalise one `videos[]` entry from `/v1/items/{id}` into the
    shape the SPA player will consume."""
    files: list[dict] = []

    # Check if we have an hls4 (adaptive) stream in the first file.
    # Kino.pub hls4 master playlists contain all qualities and audio tracks.
    raw_files = video.get("files", []) or []
    if isinstance(raw_files, list) and len(raw_files) > 0 and isinstance(raw_files[0], dict):
        url_dict = raw_files[0].get("url") or raw_files[0].get("file")
        if isinstance(url_dict, dict) and url_dict.get("hls4"):
            files.append(
                {"url": url_dict["hls4"], "quality": "Adaptive (HLS Pro)", "codec": "h264"}
            )

    # If no hls4 was found, fall back to extracting the individual files
    if not files:
        for f in raw_files:
            if not isinstance(f, dict):
                continue
            url = _extract_string(f.get("url") or f.get("file"))
            if not url:
                continue
            files.append(
                {
                    "url": url,
                    "quality": _extract_string(f.get("quality")),
                    "codec": _extract_string(f.get("codec")),
                }
            )

    audios: list[dict] = []
    for a in video.get("audios", []) or []:
        if not isinstance(a, dict):
            continue
        audios.append(
            {
                "lang": _extract_string(a.get("lang")),
                "type": _extract_string(a.get("type")),
                "author": _extract_string(a.get("author")),
            }
        )

    subtitles: list[dict] = []
    for s in video.get("subtitles", []) or []:
        if not isinstance(s, dict):
            continue
        url = _extract_string(s.get("url") or s.get("file"))
        if not url:
            continue
        subtitles.append(
            {
                "url": url,
                "lang": _extract_string(s.get("lang")),
                "shift": int(s.get("shift") or 0),
                "embed": bool(s.get("embed", False)),
            }
        )

    return {
        "number": int(video["number"]) if isinstance(video.get("number"), int) else None,
        "title": str(video.get("title") or "").strip() or None,
        "duration": int(video["duration"]) if isinstance(video.get("duration"), int) else None,
        "files": files,
        "audios": audios,
        "subtitles": subtitles,
    }


def get_stream_info(
    kinopub_id: int,
    *,
    client: KinopubClient | None = None,
) -> dict:
    """`GET /v1/items/{id}` mapped into a SPA-friendly shape.

    Output:
        {
            "id": int,
            "title": str | None,
            "year": int | None,
            "type": str | None,
            "url": str,                 # https://kino.pub/item/<id>
            "videos": [_map_video(...), ...],   # set for type == movie
            "seasons": [{"number": int, "episodes": [_map_video(...)]}, ...]
                                          # set for type == serial/multi
        }

    The caller (`routes/kinopub.py`) translates `KinopubAuthError` to
    401, `KinopubAPIError(404)` to 404, and everything else to 502.
    """
    c = client or _authenticated_client()
    body = c.get_item(int(kinopub_id))

    out: dict[str, Any] = {
        "id": int(kinopub_id),
        "title": str(body.get("title") or "").strip() or None,
        "year": int(body["year"]) if isinstance(body.get("year"), int) else None,
        "type": str(body.get("type") or "").strip() or None,
        "url": _build_item_url(int(kinopub_id)),
        "videos": [],
        "seasons": [],
    }

    for v in body.get("videos", []) or []:
        if isinstance(v, dict):
            out["videos"].append(_map_video(v))

    for season in body.get("seasons", []) or []:
        if not isinstance(season, dict):
            continue
        episodes: list[dict] = []
        for ep in season.get("episodes", []) or []:
            if isinstance(ep, dict):
                episodes.append(_map_video(ep))
        out["seasons"].append(
            {
                "number": int(season["number"]) if isinstance(season.get("number"), int) else None,
                "episodes": episodes,
            }
        )

    return out
