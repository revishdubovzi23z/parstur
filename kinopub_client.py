"""Low-level HTTP client for the kino.pub JSON API (PR 1 of the
kino.pub integration; see `docs/plans/PLAN-kinopub-integration.md`).

The client is intentionally STATELESS — it does not read or write the
`kinopub_auth` table and does not refresh tokens on its own. The state
machine (token storage, automatic refresh, retry on 401, WebSocket
broadcast of "session expired") lives one level up in
`runtime/kinopub.py` (PR 2). This file only knows how to speak HTTP
with `https://api.service-kp.com/`.

Auth model:
  * OAuth endpoints (`/oauth2/device`, `/oauth2/token`) authenticate
    with `client_id` + `client_secret` only — no Bearer header.
  * Everything else requires an `access_token`, passed to the
    constructor and sent as `Authorization: Bearer <token>`.

Why not extend `BaseMovieClient`?
  * BaseMovieClient is wired into the shared `requests-cache` backing
    store. kino.pub stream URLs are short-lived and IP-locked — caching
    them would actively break playback. Auth endpoints also must never
    be cached. Different concern, separate client.

References:
  * Official API docs: https://kinoapi.com/
  * Open-source reference implementation (BSD-3-Clause):
    https://github.com/quarckster/kodi.kino.pub
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import requests

from settings import settings

logger = logging.getLogger("kinopub.client")


# ---------------------------------------------------------------------------
# Exceptions


class KinopubError(Exception):
    """Base class for all kino.pub client errors."""


class KinopubAuthError(KinopubError):
    """Auth-related failures: invalid bearer token, missing token."""


class KinopubAuthPendingError(KinopubAuthError):
    """Device Flow: the user has not yet confirmed the code. Caller
    should keep polling at `DeviceCode.interval` seconds."""


class KinopubAuthExpiredError(KinopubAuthError):
    """Device Flow: the device_code expired before the user
    confirmed, OR the refresh_token is no longer valid. Caller must
    restart the Device Flow from scratch."""


class KinopubRateLimitError(KinopubError):
    """HTTP 429 from the API. Caller should back off (RFC 6585)."""


class KinopubAPIError(KinopubError):
    """Generic non-2xx response. `status` carries the HTTP code (0
    indicates a network-level error before any response was received)."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"{status}: {message}")
        self.status = status


# ---------------------------------------------------------------------------
# Typed payloads


@dataclass(frozen=True)
class DeviceCode:
    """First leg of the Device Flow (RFC 8628)."""

    device_code: str
    user_code: str
    verification_uri: str
    interval: int  # poll cadence in seconds
    expires_in: int  # device_code lifetime in seconds


@dataclass(frozen=True)
class TokenPair:
    """Successful response from `/oauth2/device` (after user confirms)
    or from a `refresh_token` grant."""

    access_token: str
    refresh_token: str
    expires_in: int  # access_token lifetime in seconds


# ---------------------------------------------------------------------------
# Client


class KinopubClient:
    """HTTP client for kino.pub.

    Construct one per request OR keep long-lived; a fresh
    `requests.Session` is built either way (or you can inject one to
    share connection pooling).
    """

    DEFAULT_TIMEOUT = 30
    USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"
    )

    def __init__(
        self,
        access_token: str | None = None,
        *,
        api_base_url: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        timeout: int = DEFAULT_TIMEOUT,
        session: requests.Session | None = None,
    ) -> None:
        self._access_token = access_token
        self._api_base = (api_base_url or settings.kinopub_api_base_url).rstrip("/")
        self._client_id = client_id or settings.kinopub_client_id
        self._client_secret = client_secret or settings.kinopub_client_secret
        self._timeout = timeout
        self._session = session or requests.Session()

    # ── Low-level request ────────────────────────────────────────────────

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        data: Mapping[str, Any] | None = None,
        auth_required: bool = True,
    ) -> dict:
        url = f"{self._api_base}{path}"
        headers = {"User-Agent": self.USER_AGENT}
        if auth_required:
            if not self._access_token:
                raise KinopubAuthError("No access_token provided")
            headers["Authorization"] = f"Bearer {self._access_token}"

        try:
            resp = self._session.request(
                method,
                url,
                params=params,
                data=data,
                headers=headers,
                timeout=self._timeout,
            )
        except requests.RequestException as e:
            raise KinopubAPIError(0, f"network error: {e}") from e

        if resp.status_code == 429:
            raise KinopubRateLimitError("rate limited")
        if resp.status_code == 401:
            raise KinopubAuthError("access token rejected")

        # Try to parse body as JSON regardless of status — kino.pub
        # surfaces OAuth-specific errors as 400 with {"error": "..."}.
        try:
            body = resp.json()
        except ValueError as e:
            if not resp.ok:
                raise KinopubAPIError(resp.status_code, (resp.text or "")[:200]) from e
            return {}

        if not resp.ok:
            error = body.get("error") if isinstance(body, dict) else None
            if resp.status_code == 400 and error == "authorization_pending":
                raise KinopubAuthPendingError(str(error))
            if resp.status_code == 400 and error in (
                "code_expired",
                "authorization_expired",
                "invalid_refresh_token",
            ):
                raise KinopubAuthExpiredError(str(error))
            raise KinopubAPIError(
                resp.status_code, str(error) if error else (resp.text or "")[:200]
            )

        return body if isinstance(body, dict) else {"data": body}

    # ── OAuth Device Flow (no Bearer required) ───────────────────────────

    def get_device_code(self) -> DeviceCode:
        """Start the Device Flow. Returns the user_code to display and
        the device_code to poll with."""
        body = self._request(
            "POST",
            "/oauth2/device",
            params={
                "grant_type": "device_code",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            },
            auth_required=False,
        )
        return DeviceCode(
            device_code=str(body["code"]),
            user_code=str(body["user_code"]),
            verification_uri=str(body["verification_uri"]),
            interval=int(body.get("interval", 5)),
            expires_in=int(body.get("expires_in", 600)),
        )

    def get_device_token(self, device_code: str) -> TokenPair:
        """Poll for confirmation. Raises `KinopubAuthPendingError` until
        the user enters the user_code on the verification page."""
        body = self._request(
            "POST",
            "/oauth2/device",
            params={
                "grant_type": "device_token",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "code": device_code,
            },
            auth_required=False,
        )
        return TokenPair(
            access_token=str(body["access_token"]),
            refresh_token=str(body["refresh_token"]),
            expires_in=int(body.get("expires_in", 0)),
        )

    def refresh_access_token(self, refresh_token: str) -> TokenPair:
        """Mint a fresh access_token + (rotated) refresh_token."""
        body = self._request(
            "POST",
            "/oauth2/token",
            data={
                "grant_type": "refresh_token",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "refresh_token": refresh_token,
            },
            auth_required=False,
        )
        return TokenPair(
            access_token=str(body["access_token"]),
            refresh_token=str(body["refresh_token"]),
            expires_in=int(body.get("expires_in", 0)),
        )

    # ── Content endpoints (require Bearer token) ─────────────────────────

    def search(
        self,
        query: str,
        *,
        type_: str | None = None,
        year: int | None = None,
        limit: int = 25,
    ) -> list[dict]:
        """`GET /v1/items?q=<query>` with optional type/year filters.
        Returns the raw `items` array; mapping into par2 shape is the
        caller's job."""
        params: dict[str, Any] = {"q": query, "perpage": limit}
        if type_:
            params["type"] = type_
        if year:
            params["year"] = year
        body = self._request("GET", "/v1/items", params=params)
        return list(body.get("items", []) or [])

    def get_item(self, item_id: int) -> dict:
        """`GET /v1/items/{id}` — full details including videos[],
        seasons[], subtitles[]. Returned dict is the API's `item`
        object, NOT the outer envelope."""
        body = self._request("GET", f"/v1/items/{item_id}")
        return dict(body.get("item", body))
