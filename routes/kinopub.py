"""HTTP endpoints for the kino.pub Device-Flow authentication.

PR 2 of the kino.pub integration. See `docs/plans/PLAN-kinopub-integration.md`.

Endpoints (all auth-gated via the global auth_middleware in main.py):

  GET  /api/kinopub/status              → current auth state (UI badge)
  POST /api/kinopub/device/start        → start Device Flow (rate-limited)
  POST /api/kinopub/device/poll         → poll for user confirmation
  POST /api/kinopub/logout              → wipe the stored token row

State and refresh logic live in `runtime/kinopub.py`. This file is
intentionally thin — input validation, error→HTTP mapping, that's it.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from slowapi import Limiter
from slowapi.util import get_remote_address

from kinopub_client import KinopubError
from runtime import kinopub as _kinopub
from settings import settings

logger = logging.getLogger("parsclode.routes.kinopub")

router = APIRouter(prefix="/api/kinopub", tags=["kinopub"])

# Independent limiter so the existing 5/min cap on /api/login isn't
# affected. Device-flow start is much more expensive than a login
# attempt (a real HTTP call to kino.pub), so 5/hour is conservative
# without being annoying for legitimate use.
limiter = Limiter(key_func=get_remote_address)


# ── Schemas ─────────────────────────────────────────────────────────────


class DeviceStartResponse(BaseModel):
    """First leg of the Device Flow.

    The UI displays `user_code` + `verification_uri`. The browser
    then polls /device/poll using `device_code` (which we keep
    server-side too, but echoing it lets the SPA recover after a
    soft reload).
    """

    device_code: str
    user_code: str
    verification_uri: str
    interval: int = Field(description="Recommended seconds between poll calls.")
    expires_in: int = Field(description="device_code lifetime in seconds.")


class DevicePollRequest(BaseModel):
    device_code: str = Field(min_length=1, max_length=128)


class DevicePollResponse(BaseModel):
    state: str = Field(description="'pending', 'confirmed', or 'expired'.")


class StatusResponse(BaseModel):
    enabled: bool
    authenticated: bool
    expires_at: float | None = None
    expires_in: int | None = None
    client_id: str | None = None


# ── Endpoints ───────────────────────────────────────────────────────────


@router.get("/status", response_model=StatusResponse)
def status() -> StatusResponse:
    return StatusResponse(**_kinopub.get_status())


@router.post("/device/start", response_model=DeviceStartResponse)
@limiter.limit("5/hour")
def device_start(request: Request) -> DeviceStartResponse:
    """Start the Device Flow.

    `request` is required by slowapi's `@limiter.limit` to pull the
    client IP. We intentionally don't return any other data than what
    the operator will need to type in / display.
    """
    _ = request  # silence "unused" — slowapi reads it via reflection
    if not _kinopub.is_enabled():
        raise HTTPException(
            status_code=503,
            detail="kino.pub integration is disabled (set KINOPUB_ENABLED=true)",
        )
    if not settings.kinopub_client_id or not settings.kinopub_client_secret:
        raise HTTPException(
            status_code=503,
            detail="kino.pub client credentials are not configured",
        )
    try:
        dc = _kinopub.start_device_flow()
    except KinopubError as e:
        logger.error(f"[KINOPUB] /device/start failed: {type(e).__name__}: {e}")
        raise HTTPException(status_code=502, detail=str(e)) from e
    return DeviceStartResponse(
        device_code=dc.device_code,
        user_code=dc.user_code,
        verification_uri=dc.verification_uri,
        interval=dc.interval,
        expires_in=dc.expires_in,
    )


@router.post("/device/poll", response_model=DevicePollResponse)
def device_poll(body: DevicePollRequest) -> DevicePollResponse:
    """Poll for user confirmation. Caller should retry every
    `interval` seconds returned by /device/start."""
    try:
        state = _kinopub.poll_device_flow(body.device_code)
    except KinopubError as e:
        logger.error(f"[KINOPUB] /device/poll failed: {type(e).__name__}: {e}")
        raise HTTPException(status_code=502, detail=str(e)) from e
    return DevicePollResponse(state=state)


@router.post("/logout")
def logout() -> dict:
    """Wipe the stored token row. Idempotent — calling on an already
    logged-out instance just returns the same `{"status": "ok"}`."""
    _kinopub.logout()
    return {"status": "ok"}
