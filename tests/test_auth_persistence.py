"""Auth persistence — end-to-end via FastAPI TestClient.

Pins the contract the user actually cares about: a bearer token
minted by `/api/login` survives both a live process (the obvious
case) and a swap of the `Database` instance (the not-so-obvious
case) — because the token now lives in the SQLite `sessions`
table rather than a process-local dict.
"""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient


def _make_app(monkeypatch, db_path):
    """Build a fresh `main.app` with auth enabled, wired up against
    a Database at `db_path`."""
    monkeypatch.setenv("AUTH_USER", "alice")
    monkeypatch.setenv("AUTH_PASS", "wonderland")
    monkeypatch.delenv("AUTH_PASS_HASH", raising=False)

    # The settings singleton is built once at import time; without a
    # forced re-read the monkey-patched env vars above wouldn't reach
    # `routes.auth`'s AUTH_USER / AUTH_PASS / _auth_enabled.
    import settings as settings_mod

    settings_mod.reload_settings()

    import main
    import routes.auth as auth_mod

    importlib.reload(auth_mod)
    importlib.reload(main)

    # Each test wants an isolated DB file. main.db is a module-level
    # global constructed at import time — rebind it (and the other
    # mixins that hold the same reference) to a fresh Database.
    from db import Database

    fresh = Database(str(db_path))
    fresh.init_schema()
    main.db = fresh

    # Routers cached their own `db` references at module load time;
    # rebind them too so endpoints actually hit the new DB.
    from routes import admin, auth, collections, feed, items

    admin.db = auth.db = collections.db = feed.db = items.db = fresh
    return main


@pytest.fixture(autouse=True)
def _restore_settings_and_modules(monkeypatch):
    """Tests in this file mutate the `settings` singleton and reload
    `routes.auth` / `main`. Without this fixture the next test file
    would inherit AUTH_USER=alice and start failing on every
    unauthenticated request."""
    yield
    # Drop the AUTH_* env vars (monkeypatch handles this) and force
    # `settings` + the affected modules back to their original state
    # so the rest of the test session is unaffected.
    monkeypatch.delenv("AUTH_USER", raising=False)
    monkeypatch.delenv("AUTH_PASS", raising=False)
    monkeypatch.delenv("AUTH_PASS_HASH", raising=False)
    import settings as settings_mod

    settings_mod.reload_settings()
    import routes.auth as auth_mod

    importlib.reload(auth_mod)
    import main as main_mod

    importlib.reload(main_mod)


@pytest.fixture()
def app(monkeypatch, tmp_path):
    return _make_app(monkeypatch, tmp_path / "test.db")


def test_login_returns_token_and_token_authenticates(app) -> None:
    client = TestClient(app.app)
    r = client.post("/api/login", json={"username": "alice", "password": "wonderland"})
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    assert token and token != "none"

    # Without the bearer, a protected endpoint must 401.
    r = client.get("/api/process_status")
    assert r.status_code == 401

    # With the bearer, the same endpoint passes.
    r = client.get("/api/process_status", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200


def test_token_survives_database_rebind(monkeypatch, tmp_path) -> None:
    """The whole reason the table exists: a token issued before a
    `Database` swap (the test analogue of a process restart against
    the same file) is still valid after the swap."""
    db_file = tmp_path / "restart.db"
    main_a = _make_app(monkeypatch, db_file)
    client_a = TestClient(main_a.app)
    r = client_a.post("/api/login", json={"username": "alice", "password": "wonderland"})
    assert r.status_code == 200
    token = r.json()["token"]

    # Swap in a brand-new Database instance against the same file.
    # This is the equivalent of a process restart for our purposes:
    # the in-memory dict is gone, but the SQLite file is intact.
    from db import Database

    fresh = Database(str(db_file))
    fresh.init_schema()
    main_a.db = fresh
    from routes import admin, auth, collections, feed, items

    admin.db = auth.db = collections.db = feed.db = items.db = fresh

    client_b = TestClient(main_a.app)
    r = client_b.get(
        "/api/process_status",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, f"Token did not survive Database rebind: {r.status_code} {r.text}"


def test_logout_invalidates_token(app) -> None:
    client = TestClient(app.app)
    r = client.post("/api/login", json={"username": "alice", "password": "wonderland"})
    token = r.json()["token"]

    r = client.post("/api/logout", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200

    r = client.get("/api/process_status", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


def test_wrong_credentials_rejected(app) -> None:
    client = TestClient(app.app)
    r = client.post("/api/login", json={"username": "alice", "password": "wrong"})
    assert r.status_code == 401
    r = client.post("/api/login", json={"username": "bob", "password": "wonderland"})
    assert r.status_code == 401
