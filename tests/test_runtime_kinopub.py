"""Tests for runtime.kinopub state machine (PR 2 of the kino.pub integration).

These tests use a real DB (via the `tmp_db` fixture) plus a fake
`KinopubClient` to avoid hitting the network. The fake exposes the same
public methods runtime.kinopub uses and is injected via the `client`
parameter on every entry point.
"""

from __future__ import annotations

import time
from typing import Optional

import pytest

import db as db_module
import runtime.kinopub as rk
from kinopub_client import (
    DeviceCode,
    KinopubAuthExpiredError,
    KinopubAuthPendingError,
    TokenPair,
)


@pytest.fixture(autouse=True)
def _wire_tmp_db(tmp_db, monkeypatch: pytest.MonkeyPatch):
    """Re-point `db.db` and `runtime.kinopub.db` at the per-test
    in-memory Database. Also clears the process-local pending dict
    between tests so state doesn't leak."""
    monkeypatch.setattr(db_module, "db", tmp_db, raising=True)
    monkeypatch.setattr(rk, "db", tmp_db, raising=True)
    rk._pending.clear()
    yield


@pytest.fixture(autouse=True)
def _enable_kinopub(monkeypatch: pytest.MonkeyPatch):
    """The runtime checks `settings.kinopub_enabled` — flip it on for
    every test in this module.

    Also re-points `runtime.kinopub.settings` at the *current*
    `settings.settings` instance. Other tests in the suite call
    `reload_settings()`, which swaps the global to a new instance —
    but `runtime.kinopub` cached the original at import time, so
    without this re-binding the module silently reads a stale
    settings object.
    """
    import settings as settings_mod

    current_settings = settings_mod.settings
    monkeypatch.setattr(rk, "settings", current_settings, raising=True)
    monkeypatch.setattr(current_settings, "kinopub_enabled", True, raising=True)


# ── Fake client ──────────────────────────────────────────────────────────


class _FakeClient:
    """Stand-in for KinopubClient with configurable responses.

    Each test wires up just the methods it needs. Unused methods raise
    NotImplementedError so silent fall-throughs fail loudly.
    """

    def __init__(
        self,
        *,
        device_code_response: DeviceCode | None = None,
        device_token_response: TokenPair | None = None,
        device_token_error: Exception | None = None,
        refresh_response: TokenPair | None = None,
        refresh_error: Exception | None = None,
    ) -> None:
        self.device_code_response = device_code_response
        self.device_token_response = device_token_response
        self.device_token_error = device_token_error
        self.refresh_response = refresh_response
        self.refresh_error = refresh_error
        self.calls: list[str] = []

    def get_device_code(self) -> DeviceCode:
        self.calls.append("get_device_code")
        if self.device_code_response is None:
            raise NotImplementedError("test forgot to wire device_code_response")
        return self.device_code_response

    def get_device_token(self, device_code: str) -> TokenPair:
        self.calls.append(f"get_device_token({device_code})")
        if self.device_token_error is not None:
            raise self.device_token_error
        if self.device_token_response is None:
            raise NotImplementedError("test forgot to wire device_token_response")
        return self.device_token_response

    def refresh_access_token(self, refresh_token: str) -> TokenPair:
        self.calls.append(f"refresh_access_token({refresh_token})")
        if self.refresh_error is not None:
            raise self.refresh_error
        if self.refresh_response is None:
            raise NotImplementedError("test forgot to wire refresh_response")
        return self.refresh_response


# ── get_status ───────────────────────────────────────────────────────────


def test_status_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rk.settings, "kinopub_enabled", False, raising=True)
    s = rk.get_status()
    assert s["enabled"] is False
    assert s["authenticated"] is False


def test_status_when_enabled_but_no_tokens() -> None:
    s = rk.get_status()
    assert s["enabled"] is True
    assert s["authenticated"] is False
    assert s["expires_at"] is None


def test_status_when_authenticated(tmp_db) -> None:
    tmp_db.kinopub_auth_set(
        access_token="AT",
        refresh_token="RT",
        expires_at=time.time() + 3600,
        client_id="xbmc",
        client_secret=rk.settings.kinopub_client_secret,
        now=time.time(),
    )
    s = rk.get_status()
    assert s["authenticated"] is True
    assert s["expires_at"] is not None
    assert s["expires_in"] is not None
    assert s["expires_in"] > 3000  # close to 3600


def test_status_drops_tokens_when_secret_rotated(tmp_db, monkeypatch: pytest.MonkeyPatch) -> None:
    tmp_db.kinopub_auth_set(
        access_token="AT",
        refresh_token="RT",
        expires_at=time.time() + 3600,
        client_id="xbmc",
        client_secret="old-secret",
        now=time.time(),
    )
    monkeypatch.setattr(rk.settings, "kinopub_client_secret", "new-secret", raising=True)
    s = rk.get_status()
    assert s["authenticated"] is False
    # Row was wiped — a second call sees it gone.
    assert tmp_db.kinopub_auth_get() is None


# ── Device Flow ──────────────────────────────────────────────────────────


def _dc(device_code: str = "DEVCODE", **kw) -> DeviceCode:
    return DeviceCode(
        device_code=device_code,
        user_code=kw.get("user_code", "WXYZ"),
        verification_uri=kw.get("verification_uri", "https://kino.pub/device"),
        interval=kw.get("interval", 5),
        expires_in=kw.get("expires_in", 600),
    )


def test_start_device_flow_caches_pending() -> None:
    fc = _FakeClient(device_code_response=_dc("DC123"))
    dc = rk.start_device_flow(client=fc)
    assert dc.user_code == "WXYZ"
    assert "DC123" in rk._pending


def test_poll_device_flow_pending() -> None:
    fc = _FakeClient(
        device_code_response=_dc("DC1"),
        device_token_error=KinopubAuthPendingError("authorization_pending"),
    )
    rk.start_device_flow(client=fc)
    state = rk.poll_device_flow("DC1", client=fc)
    assert state == "pending"


def test_poll_device_flow_expired_drops_pending() -> None:
    fc = _FakeClient(
        device_code_response=_dc("DC1"),
        device_token_error=KinopubAuthExpiredError("code_expired"),
    )
    rk.start_device_flow(client=fc)
    state = rk.poll_device_flow("DC1", client=fc)
    assert state == "expired"
    assert "DC1" not in rk._pending


def test_poll_unknown_device_code_returns_expired() -> None:
    fc = _FakeClient()  # nothing wired, must not call client
    state = rk.poll_device_flow("never-existed", client=fc)
    assert state == "expired"
    assert fc.calls == []


def test_poll_confirmed_persists_tokens(tmp_db) -> None:
    fc = _FakeClient(
        device_code_response=_dc("DC1"),
        device_token_response=TokenPair("AT-new", "RT-new", 3600),
    )
    rk.start_device_flow(client=fc)
    state = rk.poll_device_flow("DC1", client=fc)
    assert state == "confirmed"
    row = tmp_db.kinopub_auth_get()
    assert row is not None
    assert row["access_token"] == "AT-new"
    assert row["refresh_token"] == "RT-new"
    assert row["expires_at"] > time.time() + 3000
    # Pending entry was cleaned up.
    assert "DC1" not in rk._pending


# ── current_token / refresh ──────────────────────────────────────────────


def test_current_token_returns_none_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(rk.settings, "kinopub_enabled", False, raising=True)
    assert rk.current_token() is None


def test_current_token_returns_none_when_unauthenticated() -> None:
    assert rk.current_token() is None


def test_current_token_returns_existing_token_when_fresh(tmp_db) -> None:
    tmp_db.kinopub_auth_set(
        access_token="AT-fresh",
        refresh_token="RT",
        expires_at=time.time() + 3600,
        client_id="xbmc",
        client_secret=rk.settings.kinopub_client_secret,
        now=time.time(),
    )
    fc = _FakeClient()  # refresh shouldn't be called
    assert rk.current_token(client=fc) == "AT-fresh"
    assert fc.calls == []


def test_current_token_refreshes_when_near_expiry(tmp_db) -> None:
    # expires_at is past — definitely should refresh.
    tmp_db.kinopub_auth_set(
        access_token="AT-old",
        refresh_token="RT-old",
        expires_at=time.time() - 1,
        client_id="xbmc",
        client_secret=rk.settings.kinopub_client_secret,
        now=time.time() - 3600,
    )
    fc = _FakeClient(refresh_response=TokenPair("AT-new", "RT-new", 7200))
    tok = rk.current_token(client=fc)
    assert tok == "AT-new"
    row = tmp_db.kinopub_auth_get()
    assert row is not None
    assert row["access_token"] == "AT-new"
    assert row["refresh_token"] == "RT-new"


def test_current_token_drops_credentials_on_invalid_refresh_token(tmp_db) -> None:
    tmp_db.kinopub_auth_set(
        access_token="AT-old",
        refresh_token="RT-dead",
        expires_at=time.time() - 1,
        client_id="xbmc",
        client_secret=rk.settings.kinopub_client_secret,
        now=time.time() - 3600,
    )
    fc = _FakeClient(refresh_error=KinopubAuthExpiredError("invalid_refresh_token"))
    assert rk.current_token(client=fc) is None
    # DB row was wiped.
    assert tmp_db.kinopub_auth_get() is None


def test_logout_clears_credentials(tmp_db) -> None:
    tmp_db.kinopub_auth_set(
        access_token="AT",
        refresh_token="RT",
        expires_at=time.time() + 3600,
        client_id="xbmc",
        client_secret=rk.settings.kinopub_client_secret,
        now=time.time(),
    )
    rk.logout()
    assert tmp_db.kinopub_auth_get() is None
