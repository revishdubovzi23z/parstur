"""HTTP endpoints for the kino.pub integration.

PR 2 + PR 3 of the kino.pub integration; see
`docs/plans/PLAN-kinopub-integration.md`.

Endpoints (all auth-gated via the global auth_middleware in main.py):

  GET  /api/kinopub/status              → current auth state (UI badge)
  POST /api/kinopub/device/start        → start Device Flow (rate-limited)
  POST /api/kinopub/device/poll         → poll for user confirmation
  POST /api/kinopub/logout              → wipe the stored token row

  GET  /api/kinopub/search              → search kino.pub catalog by title
  GET  /api/kinopub/stream_info/{id}    → resolve qualities/audios/subtitles
  POST /api/kinopub/bind/{item_id}      → attach kinopub_id to a par2 item
  POST /api/kinopub/unbind/{item_id}    → detach kinopub_id from a par2 item

State and refresh logic live in `runtime/kinopub.py`. This file is
intentionally thin — input validation, error→HTTP mapping, that's it.
"""

from __future__ import annotations

import json as _json
import logging

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field
from slowapi import Limiter
from slowapi.util import get_remote_address

from db import db
from kinopub_client import KinopubAPIError, KinopubAuthError, KinopubError
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


# ── PR 3: catalog/binding endpoints ─────────────────────────────────────


class SearchResultItem(BaseModel):
    id: int
    title: str | None = None
    year: int | None = None
    type: str | None = None
    url: str
    poster: str | None = None


class SearchResponse(BaseModel):
    results: list[SearchResultItem]


class BindRequest(BaseModel):
    kinopub_id: int = Field(gt=0)
    kinopub_type: str | None = Field(default=None, max_length=32)
    kinopub_url: str | None = Field(default=None, max_length=512)


class BindResponse(BaseModel):
    status: str
    before: dict
    after: dict


def _ensure_enabled() -> None:
    """Common 503-on-disabled guard for PR 3 endpoints."""
    if not _kinopub.is_enabled():
        raise HTTPException(
            status_code=503,
            detail="kino.pub integration is disabled (set KINOPUB_ENABLED=true)",
        )


def _map_kinopub_error(e: KinopubError) -> HTTPException:
    """Translate a `kinopub_client` exception into the appropriate HTTPException.

    * `KinopubAuthError` → 401 (the UI prompts for re-auth)
    * `KinopubAPIError(404)` → 404 (item not on kino.pub)
    * other `KinopubAPIError` / network → 502 (upstream)
    """
    if isinstance(e, KinopubAuthError):
        return HTTPException(status_code=401, detail="kino.pub not authenticated")
    if isinstance(e, KinopubAPIError) and e.status == 404:
        return HTTPException(status_code=404, detail="not found on kino.pub")
    logger.error(f"[KINOPUB] upstream error: {type(e).__name__}: {e}")
    return HTTPException(status_code=502, detail=str(e))


@router.get("/search", response_model=SearchResponse)
def search(
    title: str = Query(min_length=1, max_length=200),
    year: int | None = Query(default=None, ge=1900, le=2100),
    type_: str | None = Query(default=None, alias="type", max_length=32),
    limit: int = Query(default=25, ge=1, le=50),
) -> SearchResponse:
    """Search the kino.pub catalog by title (optionally filtered by year/type).

    Pure read-through to `runtime.kinopub.search`; the SPA uses this
    to populate the "find on kino.pub" picker in the item modal.
    """
    _ensure_enabled()
    try:
        results = _kinopub.search(title, year=year, type_=type_, limit=limit)
    except KinopubError as e:
        raise _map_kinopub_error(e) from e
    return SearchResponse(results=[SearchResultItem(**r) for r in results])


@router.get("/stream_info/{item_id}")
def stream_info(item_id: int) -> dict:
    """Resolve playback metadata for a par2 item.

    The item must already be bound to a kinopub_id (via `/bind/{item_id}`
    or the future sync_kinopub matcher in PR 4). Returns 409 if not
    bound, 404 if kino.pub doesn't know the bound id, 401 if not
    authenticated.
    """
    _ensure_enabled()
    item = db.get_item(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="item not found")
    kinopub_id = item.get("kinopub_id")
    if not kinopub_id:
        raise HTTPException(
            status_code=409,
            detail="item is not bound to a kino.pub id",
        )
    try:
        return _kinopub.get_stream_info(int(kinopub_id))
    except KinopubError as e:
        raise _map_kinopub_error(e) from e


@router.post("/bind/{item_id}", response_model=BindResponse)
def bind(item_id: int, body: BindRequest) -> BindResponse:
    """Attach a kino.pub identifier to a par2 item.

    Writes `items.kinopub_id`, optional `kinopub_type` and
    `kinopub_url`, and an audit_log row. Idempotent — re-binding an
    item to the same id is allowed and just overwrites the previous
    type/url.
    """
    _ensure_enabled()
    # If the operator omitted the canonical URL, fill it in so the UI
    # always has a hyperlink available without round-tripping the API.
    url = body.kinopub_url or f"https://kino.pub/item/{body.kinopub_id}"
    result = db.kinopub_bind(
        item_id,
        kinopub_id=body.kinopub_id,
        kinopub_type=body.kinopub_type,
        kinopub_url=url,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="item not found")
    db.append_audit(
        action="kinopub_bind",
        item_id=item_id,
        field="kinopub_id,kinopub_type,kinopub_url",
        old_value=_json.dumps(result["before"], ensure_ascii=False),
        new_value=_json.dumps(result["after"], ensure_ascii=False),
    )
    return BindResponse(status="success", before=result["before"], after=result["after"])


@router.post("/unbind/{item_id}", response_model=BindResponse)
def unbind(item_id: int) -> BindResponse:
    """Detach the kino.pub identifier from a par2 item.

    Clears all `kinopub_*` columns and resets `checked_kinopub` so the
    next sync_kinopub sweep retries the match. Audit_log records the
    change. Idempotent on already-unbound items.
    """
    _ensure_enabled()
    result = db.kinopub_unbind(item_id)
    if result is None:
        raise HTTPException(status_code=404, detail="item not found")
    if result["before"] != result["after"]:
        db.append_audit(
            action="kinopub_unbind",
            item_id=item_id,
            field="kinopub_id,kinopub_type,kinopub_url",
            old_value=_json.dumps(result["before"], ensure_ascii=False),
            new_value=_json.dumps(result["after"], ensure_ascii=False),
        )
    return BindResponse(status="success", before=result["before"], after=result["after"])
