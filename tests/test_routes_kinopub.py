"""HTTP-level tests for /api/kinopub/* (PR 2 of the kino.pub integration).

Drives the real FastAPI app via TestClient. Auth is disabled by
unsetting AUTH_USER/AUTH_PASS so the middleware lets every request
through. Outbound HTTP to kino.pub is faked by patching
`runtime.kinopub` entry points.
"""

from __future__ import annotations

import importlib
import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(monkeypatch, tmp_path) -> TestClient:
    monkeypatch.delenv("AUTH_USER", raising=False)
    monkeypatch.delenv("AUTH_PASS", raising=False)
    monkeypatch.delenv("AUTH_PASS_HASH", raising=False)

    import db as db_module
    import main

    importlib.reload(main)

    from db import Database

    test_db_path = tmp_path / "test.db"
    main.db = Database(str(test_db_path))
    main.db.init_schema()
    # Re-point every route module that holds a `db` reference at
    # boot time. runtime.kinopub also caches `db`.
    from routes import kinopub as routes_kinopub
    from runtime import kinopub as runtime_kinopub

    db_module.db = main.db
    runtime_kinopub.db = main.db
    _ = routes_kinopub  # ensure routes are registered
    return TestClient(main.app)


@pytest.fixture(autouse=True)
def _enable_kinopub(monkeypatch):
    """Flip the master switch on. Re-binds `settings` in every module
    that cached it at import-time, so any prior test that called
    `reload_settings()` doesn't poison this one with a stale instance.
    """
    import settings as settings_mod
    from routes import kinopub as routes_kinopub
    from runtime import kinopub as runtime_kinopub

    current = settings_mod.settings
    monkeypatch.setattr(runtime_kinopub, "settings", current, raising=True)
    monkeypatch.setattr(routes_kinopub, "settings", current, raising=True)
    monkeypatch.setattr(current, "kinopub_enabled", True, raising=True)
    monkeypatch.setattr(current, "kinopub_client_id", "xbmc", raising=True)
    monkeypatch.setattr(current, "kinopub_client_secret", "s3cr3t", raising=True)


# ── /status ─────────────────────────────────────────────────────────────


def test_status_unauthenticated(client: TestClient) -> None:
    r = client.get("/api/kinopub/status")
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is True
    assert body["authenticated"] is False
    assert body["expires_at"] is None
    assert body["client_id"] == "xbmc"


def test_status_when_disabled(client: TestClient, monkeypatch) -> None:
    from runtime import kinopub as rk

    monkeypatch.setattr(rk.settings, "kinopub_enabled", False, raising=True)
    r = client.get("/api/kinopub/status")
    assert r.status_code == 200
    assert r.json()["enabled"] is False


# ── /device/start ───────────────────────────────────────────────────────


def test_device_start_503_when_disabled(client: TestClient, monkeypatch) -> None:
    from runtime import kinopub as rk

    monkeypatch.setattr(rk.settings, "kinopub_enabled", False, raising=True)
    r = client.post("/api/kinopub/device/start")
    assert r.status_code == 503


def test_device_start_success(client: TestClient, monkeypatch) -> None:
    from kinopub_client import DeviceCode
    from runtime import kinopub as rk

    def _fake_start():
        return DeviceCode(
            device_code="DC-fake",
            user_code="WXYZ-1234",
            verification_uri="https://kino.pub/device",
            interval=5,
            expires_in=600,
        )

    monkeypatch.setattr(rk, "start_device_flow", lambda: _fake_start(), raising=True)
    r = client.post("/api/kinopub/device/start")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["device_code"] == "DC-fake"
    assert body["user_code"] == "WXYZ-1234"
    assert body["verification_uri"] == "https://kino.pub/device"
    assert body["interval"] == 5
    assert body["expires_in"] == 600


# ── /device/poll ────────────────────────────────────────────────────────


def test_device_poll_pending(client: TestClient, monkeypatch) -> None:
    from runtime import kinopub as rk

    monkeypatch.setattr(rk, "poll_device_flow", lambda code: "pending", raising=True)
    r = client.post("/api/kinopub/device/poll", json={"device_code": "DC"})
    assert r.status_code == 200
    assert r.json()["state"] == "pending"


def test_device_poll_confirmed(client: TestClient, monkeypatch) -> None:
    from runtime import kinopub as rk

    monkeypatch.setattr(rk, "poll_device_flow", lambda code: "confirmed", raising=True)
    r = client.post("/api/kinopub/device/poll", json={"device_code": "DC"})
    assert r.status_code == 200
    assert r.json()["state"] == "confirmed"


def test_device_poll_expired(client: TestClient, monkeypatch) -> None:
    from runtime import kinopub as rk

    monkeypatch.setattr(rk, "poll_device_flow", lambda code: "expired", raising=True)
    r = client.post("/api/kinopub/device/poll", json={"device_code": "DC"})
    assert r.status_code == 200
    assert r.json()["state"] == "expired"


def test_device_poll_rejects_empty_device_code(client: TestClient) -> None:
    r = client.post("/api/kinopub/device/poll", json={"device_code": ""})
    # Pydantic min_length=1 → 422.
    assert r.status_code == 422


# ── /logout ─────────────────────────────────────────────────────────────


def test_logout_is_idempotent(client: TestClient) -> None:
    r = client.post("/api/kinopub/logout")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    # Twice in a row — also fine.
    r2 = client.post("/api/kinopub/logout")
    assert r2.status_code == 200


def test_logout_clears_persisted_tokens(client: TestClient) -> None:
    # Seed an auth row directly.
    import main
    from runtime import kinopub as rk

    main.db.kinopub_auth_set(
        access_token="AT",
        refresh_token="RT",
        expires_at=time.time() + 3600,
        client_id="xbmc",
        client_secret=rk.settings.kinopub_client_secret,
        now=time.time(),
    )
    assert main.db.kinopub_auth_get() is not None
    r = client.post("/api/kinopub/logout")
    assert r.status_code == 200
    assert main.db.kinopub_auth_get() is None
