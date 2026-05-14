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
from typing import Optional

from db import db
from kinopub_client import (
    DeviceCode,
    KinopubAPIError,
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


def start_device_flow(client: Optional[KinopubClient] = None) -> DeviceCode:
    """Kick off the Device Flow. The returned `DeviceCode` carries the
    `user_code` to show the operator, the verification URL to send them
    to, and the `device_code` to feed to `poll_device_flow`."""
    c = client or KinopubClient()
    dc = c.get_device_code()
    _gc_pending(time.time())
    with _pending_lock:
        _pending[dc.device_code] = _PendingDeviceCode(code=dc, started_at=time.time())
    logger.info(
        "[KINOPUB] device flow started "
        f"(user_code={dc.user_code!r}, expires_in={dc.expires_in}s)"
    )
    return dc


def poll_device_flow(device_code: str, client: Optional[KinopubClient] = None) -> str:
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
        "[KINOPUB] device flow confirmed; " f"access_token valid for {int(tokens.expires_in)}s"
    )
    return "confirmed"


def logout() -> None:
    """Clear the persisted token row. Used by /api/kinopub/logout."""
    db.kinopub_auth_clear()
    logger.info("[KINOPUB] logged out (kinopub_auth row cleared)")


def current_token(client: Optional[KinopubClient] = None) -> Optional[str]:
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
