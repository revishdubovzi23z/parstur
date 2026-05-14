"""10.7f — `GET /api/item/{id}` returns item + releases + collections.

These tests pin the contract the new SPA item-card modal depends on.
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
    from routes import collections, feed, items

    items.db = collections.db = feed.db = main.db
    main.db.init_schema()

    with main.db._conn() as c:
        c.execute(
            "INSERT INTO items (id, category_id, title, year, kp_id, imdb_id, rezka_url) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (1, 1, "Test", 2020, "111", "tt001", "https://rezka/x"),
        )
        c.execute(
            "INSERT INTO releases (id, item_id, rutor_id, torrent_title, date_added) "
            "VALUES (?, ?, ?, ?, ?)",
            (10, 1, "rt-1", "Test.2020.1080p", "2024-01-15 10:00:00"),
        )
        c.execute(
            "INSERT INTO releases (id, item_id, rutor_id, torrent_title, date_added) "
            "VALUES (?, ?, ?, ?, ?)",
            (11, 1, "rt-2", "Test.2020.4K", "2024-02-20 10:00:00"),
        )
    return TestClient(main.app)


def test_get_item_returns_full_payload(client: TestClient) -> None:
    r = client.get("/api/item/1")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["item"]["id"] == 1
    assert body["item"]["title"] == "Test"
    assert body["item"]["kp_id"] == "111"
    assert isinstance(body["releases"], list)
    assert len(body["releases"]) == 2
    # Sorted by date_added DESC — newest first.
    assert body["releases"][0]["rutor_id"] == "rt-2"
    assert body["releases"][1]["rutor_id"] == "rt-1"
    assert isinstance(body["collections"], list)


def test_get_item_404_when_missing(client: TestClient) -> None:
    r = client.get("/api/item/9999")
    assert r.status_code == 404
    assert r.json()["error"] == "item not found"
