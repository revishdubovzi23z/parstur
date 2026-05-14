"""HTTP-level tests for PR 3 kino.pub endpoints (search / bind / unbind / stream_info).

These tests drive the real FastAPI app via TestClient. Auth is
disabled by unsetting AUTH_USER/AUTH_PASS so the middleware lets
every request through. Outbound HTTP to kino.pub is faked by
patching `runtime.kinopub.search` / `.get_stream_info`.
"""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from kinopub_client import KinopubAPIError, KinopubAuthError


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
    from routes import kinopub as routes_kinopub
    from runtime import kinopub as runtime_kinopub

    db_module.db = main.db
    runtime_kinopub.db = main.db
    routes_kinopub.db = main.db
    return TestClient(main.app)


@pytest.fixture(autouse=True)
def _enable_kinopub(monkeypatch):
    import settings as settings_mod
    from routes import kinopub as routes_kinopub
    from runtime import kinopub as runtime_kinopub

    current = settings_mod.settings
    monkeypatch.setattr(runtime_kinopub, "settings", current, raising=True)
    monkeypatch.setattr(routes_kinopub, "settings", current, raising=True)
    monkeypatch.setattr(current, "kinopub_enabled", True, raising=True)
    monkeypatch.setattr(current, "kinopub_client_id", "xbmc", raising=True)
    monkeypatch.setattr(current, "kinopub_client_secret", "s3cr3t", raising=True)


def _insert_item(db, *, title: str = "Inception", year: int = 2010) -> int:
    with db._conn() as c:
        cur = c.execute(
            "INSERT INTO items (title, year, category_id) VALUES (?, ?, ?)",
            (title, year, 1),
        )
        return int(cur.lastrowid)


# ── /search ─────────────────────────────────────────────────────────────


def test_search_503_when_disabled(client: TestClient, monkeypatch) -> None:
    from runtime import kinopub as rk

    monkeypatch.setattr(rk.settings, "kinopub_enabled", False, raising=True)
    r = client.get("/api/kinopub/search", params={"title": "Inception"})
    assert r.status_code == 503


def test_search_requires_title(client: TestClient) -> None:
    r = client.get("/api/kinopub/search")
    assert r.status_code == 422


def test_search_passes_filters_through(client: TestClient, monkeypatch) -> None:
    from runtime import kinopub as rk

    captured: dict = {}

    def _fake_search(query, **kwargs):
        captured["query"] = query
        captured.update(kwargs)
        return [
            {
                "id": 1,
                "title": "Inception",
                "year": 2010,
                "type": "movie",
                "url": "https://kino.pub/item/1",
                "poster": None,
            }
        ]

    monkeypatch.setattr(rk, "search", _fake_search, raising=True)
    r = client.get(
        "/api/kinopub/search",
        params={"title": "Inception", "year": 2010, "type": "movie", "limit": 5},
    )
    assert r.status_code == 200, r.text
    assert captured["query"] == "Inception"
    assert captured["year"] == 2010
    assert captured["type_"] == "movie"
    assert captured["limit"] == 5
    body = r.json()
    assert len(body["results"]) == 1
    assert body["results"][0]["id"] == 1


def test_search_maps_auth_error_to_401(client: TestClient, monkeypatch) -> None:
    from runtime import kinopub as rk

    def _fake_search(*a, **k):
        raise KinopubAuthError("nope")

    monkeypatch.setattr(rk, "search", _fake_search, raising=True)
    r = client.get("/api/kinopub/search", params={"title": "x"})
    assert r.status_code == 401


def test_search_maps_upstream_to_502(client: TestClient, monkeypatch) -> None:
    from runtime import kinopub as rk

    def _fake_search(*a, **k):
        raise KinopubAPIError(500, "boom")

    monkeypatch.setattr(rk, "search", _fake_search, raising=True)
    r = client.get("/api/kinopub/search", params={"title": "x"})
    assert r.status_code == 502


# ── /stream_info/{item_id} ──────────────────────────────────────────────


def test_stream_info_404_when_item_missing(client: TestClient) -> None:
    r = client.get("/api/kinopub/stream_info/999999")
    assert r.status_code == 404


def test_stream_info_409_when_not_bound(client: TestClient) -> None:
    import main

    item_id = _insert_item(main.db)
    r = client.get(f"/api/kinopub/stream_info/{item_id}")
    assert r.status_code == 409
    assert "not bound" in r.json()["detail"].lower()


def test_stream_info_returns_payload(client: TestClient, monkeypatch) -> None:
    import main
    from runtime import kinopub as rk

    item_id = _insert_item(main.db)
    main.db.kinopub_bind(
        item_id,
        kinopub_id=555,
        kinopub_type="movie",
        kinopub_url="https://kino.pub/item/555",
    )

    captured: list[int] = []

    def _fake_stream(kid: int):
        captured.append(kid)
        return {
            "id": 555,
            "title": "Inception",
            "year": 2010,
            "type": "movie",
            "url": "https://kino.pub/item/555",
            "videos": [],
            "seasons": [],
        }

    monkeypatch.setattr(rk, "get_stream_info", _fake_stream, raising=True)
    r = client.get(f"/api/kinopub/stream_info/{item_id}")
    assert r.status_code == 200, r.text
    assert captured == [555]
    body = r.json()
    assert body["id"] == 555
    assert body["title"] == "Inception"


def test_stream_info_maps_404_from_upstream(client: TestClient, monkeypatch) -> None:
    import main
    from runtime import kinopub as rk

    item_id = _insert_item(main.db)
    main.db.kinopub_bind(item_id, kinopub_id=42)

    def _fake_stream(kid: int):
        raise KinopubAPIError(404, "not found")

    monkeypatch.setattr(rk, "get_stream_info", _fake_stream, raising=True)
    r = client.get(f"/api/kinopub/stream_info/{item_id}")
    assert r.status_code == 404
    assert "not found" in r.json()["detail"].lower()


# ── /bind/{item_id} ─────────────────────────────────────────────────────


def test_bind_404_when_item_missing(client: TestClient) -> None:
    r = client.post("/api/kinopub/bind/999999", json={"kinopub_id": 1})
    assert r.status_code == 404


def test_bind_rejects_zero_or_negative(client: TestClient) -> None:
    import main

    item_id = _insert_item(main.db)
    r = client.post(f"/api/kinopub/bind/{item_id}", json={"kinopub_id": 0})
    assert r.status_code == 422


def test_bind_writes_columns_and_audit(client: TestClient) -> None:
    import main

    item_id = _insert_item(main.db)
    r = client.post(
        f"/api/kinopub/bind/{item_id}",
        json={"kinopub_id": 12345, "kinopub_type": "movie"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "success"
    assert body["before"]["kinopub_id"] is None
    assert body["after"]["kinopub_id"] == 12345
    # Backend auto-fills URL when caller omits it.
    assert body["after"]["kinopub_url"] == "https://kino.pub/item/12345"
    row = main.db.get_item(item_id)
    assert row["kinopub_id"] == 12345
    assert row["kinopub_type"] == "movie"
    assert row["checked_kinopub"] == 1
    audit = main.db.list_audit(item_id=item_id)
    assert any(a["action"] == "kinopub_bind" for a in audit)


def test_bind_503_when_disabled(client: TestClient, monkeypatch) -> None:
    from runtime import kinopub as rk

    monkeypatch.setattr(rk.settings, "kinopub_enabled", False, raising=True)
    r = client.post("/api/kinopub/bind/1", json={"kinopub_id": 1})
    assert r.status_code == 503


# ── /unbind/{item_id} ───────────────────────────────────────────────────


def test_unbind_404_when_item_missing(client: TestClient) -> None:
    r = client.post("/api/kinopub/unbind/999999")
    assert r.status_code == 404


def test_unbind_clears_and_audits(client: TestClient) -> None:
    import main

    item_id = _insert_item(main.db)
    main.db.kinopub_bind(item_id, kinopub_id=7, kinopub_type="movie", kinopub_url="u")
    r = client.post(f"/api/kinopub/unbind/{item_id}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["before"]["kinopub_id"] == 7
    assert body["after"]["kinopub_id"] is None
    row = main.db.get_item(item_id)
    assert row["kinopub_id"] is None
    assert row["checked_kinopub"] == 0
    audit = main.db.list_audit(item_id=item_id)
    assert any(a["action"] == "kinopub_unbind" for a in audit)


def test_unbind_idempotent_on_already_unbound(client: TestClient) -> None:
    import main

    item_id = _insert_item(main.db)
    # Already unbound — should still 200 and not crash on audit.
    r = client.post(f"/api/kinopub/unbind/{item_id}")
    assert r.status_code == 200
    audit = main.db.list_audit(item_id=item_id)
    # No-op unbind should NOT emit an audit row (before == after).
    assert not any(a["action"] == "kinopub_unbind" for a in audit)
