"""8.2 — collections export/import endpoints.

Smoke-tests the JSON and CSV variants of /api/collections/export
plus the JSON and CSV import endpoints. Uses TestClient against a
freshly-loaded `main.app`.
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
    # Use a fresh DB file per test so we don't pollute the repo's
    # app_data.db. main.py's `db` global is constructed at module
    # import time; rebind it to a temp-path Database after reload.
    importlib.reload(main)
    from db import Database

    test_db_path = tmp_path / "test.db"
    main.db = Database(str(test_db_path))
    from routes import collections, feed, items

    items.db = collections.db = feed.db = main.db
    from routes import collections, items

    items.db = collections.db = main.db
    main.db.init_schema()
    return TestClient(main.app)


def test_export_json_envelope(client: TestClient) -> None:
    # Seed a single collection so the export has something to round-trip.
    # `init_schema` used to seed 11 default collections by name, but as
    # of the "lazy collections" change the table starts empty on a
    # fresh DB. We create one explicitly here so this test still
    # exercises the export shape.
    client.post(
        "/api/collections/import",
        json={
            "collections": [{"name": "test-export-source", "sort_order": 0, "items": []}],
            "replace": False,
        },
    )
    r = client.get("/api/collections/export")
    assert r.status_code == 200
    assert r.headers["content-disposition"].endswith("collections.json")
    body = r.json()
    assert body["version"] == 1
    assert isinstance(body["collections"], list)
    assert body["collections"], "expected at least the seeded collection in export"
    for col in body["collections"]:
        assert "name" in col
        assert "items" in col


def test_export_csv_has_header(client: TestClient) -> None:
    r = client.get("/api/collections/export?fmt=csv")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    text = r.text
    first_line = text.splitlines()[0]
    assert first_line.startswith(
        "collection_name,sort_order,kp_id,imdb_id,rezka_url,title,original_title,year,added_at"
    )


def test_import_json_envelope_accepted(client: TestClient) -> None:
    payload = {
        "collections": [
            {
                "name": "test-import-target",
                "sort_order": 99,
                "items": [],
            }
        ],
        "replace": False,
    }
    r = client.post("/api/collections/import", json=payload)
    assert r.status_code == 200
    body = r.json()
    # Field names match Database.import_collections's contract.
    assert "added_items" in body
    assert "missing_items" in body
    assert "created_collections" in body
    assert "updated_collections" in body
    # Fresh collection name -> created_collections >= 1.
    assert body["created_collections"] >= 1


def test_import_csv_smoke(client: TestClient) -> None:
    csv_body = (
        "collection_name,sort_order,kp_id,imdb_id,rezka_url,title,"
        "original_title,year,added_at\n"
        "test-csv-import,0,,,,,,,\n"
    )
    r = client.post(
        "/api/collections/import_csv",
        content=csv_body,
        headers={"Content-Type": "text/csv"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["created_collections"] >= 1
