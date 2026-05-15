"""Tests for `kinopub_client.py` (PR 1 of the kino.pub integration).

These tests use a fake `requests.Session` injected via the constructor
to avoid hitting the network. The fake records every call so we can
assert request shape (URL, params, headers, body) and lets us script
any HTTP status / JSON body in response.
"""

from __future__ import annotations

import json
from typing import Any, Optional

import pytest

from kinopub_client import (
    DeviceCode,
    KinopubAPIError,
    KinopubAuthError,
    KinopubAuthExpiredError,
    KinopubAuthPendingError,
    KinopubClient,
    KinopubRateLimitError,
    TokenPair,
)

# ── Test doubles ─────────────────────────────────────────────────────────


class _FakeResponse:
    """Stand-in for requests.Response with the small surface the client uses."""

    def __init__(
        self,
        status: int,
        body: dict | None = None,
        *,
        text: str = "",
    ) -> None:
        self.status_code = status
        self._body = body
        self._text = text or (json.dumps(body) if body is not None else "")

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 400

    @property
    def text(self) -> str:
        return self._text

    def json(self) -> Any:
        if self._body is None:
            raise ValueError("no body")
        return self._body


class _FakeSession:
    """Stand-in for requests.Session.

    Pass a single _FakeResponse to reply with the same response to every
    call, or a callable `(method, url, params, data, headers, timeout) -> _FakeResponse`
    for per-call dispatch."""

    def __init__(self, response: Any) -> None:
        self._response = response
        self.calls: list[dict] = []

    def request(
        self,
        method: str,
        url: str,
        *,
        params: Any = None,
        data: Any = None,
        headers: Any = None,
        timeout: Any = None,
    ) -> _FakeResponse:
        self.calls.append(
            {
                "method": method,
                "url": url,
                "params": params,
                "data": data,
                "headers": headers,
                "timeout": timeout,
            }
        )
        if callable(self._response):
            return self._response(method, url, params, data, headers, timeout)
        return self._response


# ── Device Flow ──────────────────────────────────────────────────────────


def test_get_device_code_parses_response() -> None:
    fake = _FakeSession(
        _FakeResponse(
            200,
            {
                "code": "abc123",
                "user_code": "WXYZ-1234",
                "verification_uri": "https://kino.pub/device",
                "interval": 5,
                "expires_in": 600,
            },
        )
    )
    c = KinopubClient(session=fake, client_id="xbmc", client_secret="s3cr3t")
    dc = c.get_device_code()
    assert isinstance(dc, DeviceCode)
    assert dc.device_code == "abc123"
    assert dc.user_code == "WXYZ-1234"
    assert dc.verification_uri == "https://kino.pub/device"
    assert dc.interval == 5
    assert dc.expires_in == 600

    # Request shape: POST /oauth2/device with grant_type=device_code, no Bearer.
    call = fake.calls[0]
    assert call["method"] == "POST"
    assert call["url"].endswith("/oauth2/device")
    assert call["params"]["grant_type"] == "device_code"
    assert call["params"]["client_id"] == "xbmc"
    assert call["params"]["client_secret"] == "s3cr3t"
    assert "Authorization" not in (call["headers"] or {})


def test_get_device_token_pending_raises_AuthPending() -> None:
    fake = _FakeSession(_FakeResponse(400, {"error": "authorization_pending"}))
    c = KinopubClient(session=fake, client_id="x", client_secret="s")
    with pytest.raises(KinopubAuthPendingError):
        c.get_device_token("dc")


def test_get_device_token_expired_raises_AuthExpired() -> None:
    fake = _FakeSession(_FakeResponse(400, {"error": "code_expired"}))
    c = KinopubClient(session=fake, client_id="x", client_secret="s")
    with pytest.raises(KinopubAuthExpiredError):
        c.get_device_token("dc")


def test_get_device_token_success() -> None:
    fake = _FakeSession(
        _FakeResponse(
            200,
            {"access_token": "AT", "refresh_token": "RT", "expires_in": 3600},
        )
    )
    c = KinopubClient(session=fake, client_id="x", client_secret="s")
    tok = c.get_device_token("dc")
    assert isinstance(tok, TokenPair)
    assert tok.access_token == "AT"
    assert tok.refresh_token == "RT"
    assert tok.expires_in == 3600


def test_refresh_token_invalid_raises_AuthExpired() -> None:
    fake = _FakeSession(_FakeResponse(400, {"error": "invalid_refresh_token"}))
    c = KinopubClient(session=fake, client_id="x", client_secret="s")
    with pytest.raises(KinopubAuthExpiredError):
        c.refresh_access_token("rt")


def test_refresh_token_success_returns_new_pair() -> None:
    fake = _FakeSession(
        _FakeResponse(
            200,
            {"access_token": "AT2", "refresh_token": "RT2", "expires_in": 7200},
        )
    )
    c = KinopubClient(session=fake, client_id="x", client_secret="s")
    tok = c.refresh_access_token("rt-old")
    assert tok.access_token == "AT2"
    assert tok.refresh_token == "RT2"
    assert tok.expires_in == 7200

    # Refresh uses POST form-data, NOT query params (per kino.pub docs).
    call = fake.calls[0]
    assert call["method"] == "POST"
    assert call["url"].endswith("/oauth2/token")
    assert call["data"]["grant_type"] == "refresh_token"
    assert call["data"]["refresh_token"] == "rt-old"


# ── Content endpoints ────────────────────────────────────────────────────


def test_search_includes_bearer_token_and_filters() -> None:
    fake = _FakeSession(_FakeResponse(200, {"items": [{"id": 1, "title": "Foo"}]}))
    c = KinopubClient(access_token="abcdef", session=fake)
    items = c.search("Foo", type_="movie", year=2020, limit=10)
    assert items == [{"id": 1, "title": "Foo"}]
    call = fake.calls[0]
    assert call["url"].endswith("/v1/items/search")
    assert call["headers"]["Authorization"] == "Bearer abcdef"
    assert call["params"]["q"] == "Foo"
    assert call["params"]["field"] == "title"
    assert call["params"]["type"] == "movie"
    assert call["params"]["year"] == 2020
    assert call["params"]["perpage"] == 10


def test_search_without_token_raises() -> None:
    fake = _FakeSession(_FakeResponse(200, {"items": []}))
    c = KinopubClient(session=fake)
    with pytest.raises(KinopubAuthError):
        c.search("x")


def test_search_returns_empty_list_when_items_missing() -> None:
    fake = _FakeSession(_FakeResponse(200, {}))
    c = KinopubClient(access_token="t", session=fake)
    assert c.search("nothing") == []


def test_get_item_returns_inner_item() -> None:
    fake = _FakeSession(_FakeResponse(200, {"item": {"id": 42, "title": "Inception"}}))
    c = KinopubClient(access_token="t", session=fake)
    item = c.get_item(42)
    assert item["id"] == 42
    assert item["title"] == "Inception"
    assert fake.calls[0]["url"].endswith("/v1/items/42")


# ── Error mapping ────────────────────────────────────────────────────────


def test_429_raises_RateLimit() -> None:
    fake = _FakeSession(_FakeResponse(429, {}))
    c = KinopubClient(access_token="t", session=fake)
    with pytest.raises(KinopubRateLimitError):
        c.search("x")


def test_401_raises_AuthError() -> None:
    fake = _FakeSession(_FakeResponse(401, {}))
    c = KinopubClient(access_token="t", session=fake)
    with pytest.raises(KinopubAuthError):
        c.search("x")


def test_500_raises_APIError_with_status() -> None:
    fake = _FakeSession(_FakeResponse(500, None, text="oops"))
    c = KinopubClient(access_token="t", session=fake)
    with pytest.raises(KinopubAPIError) as exc:
        c.search("x")
    assert exc.value.status == 500


def test_network_error_raises_APIError_status_0() -> None:
    import requests

    def raise_network(*args, **kwargs):
        raise requests.ConnectionError("dns failure")

    fake = _FakeSession(raise_network)
    c = KinopubClient(access_token="t", session=fake)
    with pytest.raises(KinopubAPIError) as exc:
        c.search("x")
    assert exc.value.status == 0
