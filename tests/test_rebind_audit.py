"""8.17 + 8.18 — manual rebind endpoint with audit log + undo.

Auth is disabled in these tests by clearing AUTH_USER/AUTH_PASS so
the endpoints can be called directly via TestClient.
"""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

import main


@pytest.fixture()
def client(monkeypatch, tmp_path) -> TestClient:
    monkeypatch.delenv("AUTH_USER", raising=False)
    monkeypatch.delenv("AUTH_PASS", raising=False)
    monkeypatch.delenv("AUTH_PASS_HASH", raising=False)
    importlib.reload(main)
    from db import Database

    test_db_path = tmp_path / "test.db"
    main.db = Database(str(test_db_path))
    main.db.init_schema()

    with main.db._conn() as c:
        c.execute(
            "INSERT INTO items (id, category_id, title, year, kp_id, imdb_id, rezka_url) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (1, 1, "Test", 2020, "111", "tt001", "https://rezka/old"),
        )
    return TestClient(main.app)


def test_rebind_updates_and_logs(client: TestClient) -> None:
    r = client.post(
        "/api/rebind/1",
        json={"kp_id": "999", "rezka_url": "https://rezka/new"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["before"]["kp_id"] == "111"
    assert body["after"]["kp_id"] == "999"
    assert body["after"]["rezka_url"] == "https://rezka/new"

    # The audit log must contain an entry for this change.
    log = client.get("/api/audit_log").json()
    assert len(log) == 1
    entry = log[0]
    assert entry["action"] == "rebind"
    assert entry["item_id"] == 1
    assert "111" in (entry["old_value"] or "")
    assert "999" in (entry["new_value"] or "")


def test_undo_restores_prior_values(client: TestClient) -> None:
    client.post("/api/rebind/1", json={"kp_id": "999"})
    audit_id = client.get("/api/audit_log").json()[0]["id"]

    r = client.post(f"/api/audit_log/{audit_id}/undo")
    assert r.status_code == 200, r.text

    with main.db._conn() as c:
        row = c.execute("SELECT kp_id FROM items WHERE id = 1").fetchone()
        assert row["kp_id"] == "111"  # restored

    # Second undo of the same entry must report 'already undone'.
    r2 = client.post(f"/api/audit_log/{audit_id}/undo")
    assert r2.status_code == 400


def test_rebind_404_when_item_missing(client: TestClient) -> None:
    r = client.post("/api/rebind/999", json={"kp_id": "x"})
    assert r.status_code == 404


def test_rebind_no_changes_returns_noop(client: TestClient) -> None:
    r = client.post("/api/rebind/1", json={})
    assert r.status_code == 200
    assert r.json() == {"status": "noop"}


def test_filter_rules_endpoints_roundtrip(client: TestClient) -> None:
    r = client.post(
        "/api/filter_rules",
        json={"name": "block-foo", "field": "title", "pattern": "foo", "action": "hide"},
    )
    assert r.status_code == 200, r.text
    rid = r.json()["id"]

    r = client.get("/api/filter_rules")
    assert r.status_code == 200
    rules = r.json()
    assert any(rule["id"] == rid for rule in rules)

    r = client.put(f"/api/filter_rules/{rid}", json={"enabled": False})
    assert r.status_code == 200

    r = client.delete(f"/api/filter_rules/{rid}")
    assert r.status_code == 200

    r = client.delete(f"/api/filter_rules/{rid}")
    assert r.status_code == 404


def test_filter_rules_reject_invalid_regex(client: TestClient) -> None:
    r = client.post(
        "/api/filter_rules",
        json={"name": "bad", "field": "title", "pattern": "[unclosed", "action": "hide"},
    )
    assert r.status_code == 400


def test_filter_rules_reject_unknown_field(client: TestClient) -> None:
    r = client.post(
        "/api/filter_rules",
        json={"name": "bad", "field": "evil_col", "pattern": ".*", "action": "hide"},
    )
    assert r.status_code == 400
