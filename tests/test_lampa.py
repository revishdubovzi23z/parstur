from __future__ import annotations

import importlib
from unittest.mock import MagicMock

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

    # Bind Database to routers
    from routes import collections, feed, items, lampa

    items.db = collections.db = feed.db = lampa.db = main.db
    main.db.init_schema()

    with main.db._conn() as c:
        # Clear collections to avoid seeded conflict
        c.execute("DELETE FROM collections")
        c.execute("DELETE FROM collection_items")
        c.execute("DELETE FROM items")

        # Add test item
        c.execute(
            "INSERT INTO items (id, category_id, title, original_title, year, kp_id, imdb_id, description, poster_url, user_rating, is_watched) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                1,
                1,
                "Фильм Тест / Movie Test",
                "Movie Test",
                2025,
                "111",
                "tt001",
                "Описание фильма",
                "http://path/to/poster.jpg",
                9,
                1,
            ),
        )
        c.execute(
            "INSERT INTO items (id, category_id, title, original_title, year, kp_id, imdb_id, description, poster_url, user_rating, is_watched) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                2,
                4,
                "Сериал Тест",
                "Show Test",
                2024,
                "222",
                "tt002",
                "Описание сериала",
                "",
                None,
                0,
            ),
        )
        # Add test collections
        c.execute(
            "INSERT INTO collections (id, name, sort_order, is_system) VALUES (?, ?, ?, ?)",
            (1, "Хочу посмотреть", 1, 0),
        )
        c.execute(
            "INSERT INTO collections (id, name, sort_order, is_system) VALUES (?, ?, ?, ?)",
            (2, "Просмотренное", 9999, 1),
        )
        # Add items to collections
        c.execute("INSERT INTO collection_items (collection_id, item_id) VALUES (?, ?)", (1, 1))
        c.execute("INSERT INTO collection_items (collection_id, item_id) VALUES (?, ?)", (1, 2))
        c.execute("INSERT INTO collection_items (collection_id, item_id) VALUES (?, ?)", (2, 1))

    return TestClient(main.app)


def _make_settings(lampa_enabled: bool = True, lampa_api_key: str | None = None) -> MagicMock:
    """Build a fake settings object for Lampa route patching."""
    mock = MagicMock()
    mock.lampa_enabled = lampa_enabled
    mock.lampa_api_key = lampa_api_key
    return mock


def test_lampa_plugin_js_endpoint(client: TestClient, monkeypatch) -> None:
    import routes.lampa

    monkeypatch.setattr(routes.lampa, "settings", _make_settings(lampa_api_key="secret-key"))

    r = client.get("/api/lampa/plugin.js?key=secret-key")
    assert r.status_code == 200
    assert r.headers["Content-Type"] == "application/javascript"
    assert "📚 Мои коллекции" in r.text
    assert "secret-key" in r.text


def test_lampa_collections_endpoint(client: TestClient, monkeypatch) -> None:
    import routes.lampa

    monkeypatch.setattr(routes.lampa, "settings", _make_settings())

    r = client.get("/api/lampa/collections")
    assert r.status_code == 200
    data = r.json()
    assert "collections" in data
    cols = data["collections"]
    assert len(cols) == 2

    # Sort order: Хочу посмотреть (1), Просмотренное (9999)
    assert cols[0]["name"] == "Хочу посмотреть"
    assert cols[0]["count"] == 2
    assert cols[1]["name"] == "Просмотренное"
    assert cols[1]["count"] == 1


def test_lampa_collection_items_endpoint(client: TestClient, monkeypatch) -> None:
    import routes.lampa

    monkeypatch.setattr(routes.lampa, "settings", _make_settings())

    # Fetch collection 1 ("Хочу посмотреть")
    r = client.get("/api/lampa/collection/1")
    assert r.status_code == 200
    data = r.json()
    assert data["page"] == 1
    assert len(data["results"]) == 2

    # Check item details conversion
    item1 = next(it for it in data["results"] if it["antigravity_id"] == 1)
    assert item1["title"] == "Фильм Тест"
    assert item1["media_type"] == "movie"
    assert item1["vote_average"] == 9.0
    assert item1["is_watched"] is True

    item2 = next(it for it in data["results"] if it["antigravity_id"] == 2)
    assert item2["title"] == "Сериал Тест"
    assert item2["media_type"] == "tv"


def test_lampa_search_endpoint(client: TestClient, monkeypatch) -> None:
    import routes.lampa

    monkeypatch.setattr(routes.lampa, "settings", _make_settings())

    r = client.get("/api/lampa/search?q=Сериал")
    assert r.status_code == 200
    data = r.json()
    assert len(data["results"]) == 1
    assert data["results"][0]["title"] == "Сериал Тест"


def test_lampa_item_endpoint(client: TestClient, monkeypatch) -> None:
    import routes.lampa

    monkeypatch.setattr(routes.lampa, "settings", _make_settings())

    r = client.get("/api/lampa/item/1")
    assert r.status_code == 200
    item = r.json()
    assert item["title"] == "Фильм Тест"
    assert item["vote_average"] == 9.0


def test_lampa_unauthorized(client: TestClient, monkeypatch) -> None:
    import routes.lampa

    monkeypatch.setattr(routes.lampa, "settings", _make_settings(lampa_api_key="highly-secret"))

    r = client.get("/api/lampa/collections")
    assert r.status_code == 401

    r = client.get("/api/lampa/collections?key=highly-secret")
    assert r.status_code == 200


def test_lampa_disabled(client: TestClient, monkeypatch) -> None:
    import routes.lampa

    monkeypatch.setattr(routes.lampa, "settings", _make_settings(lampa_enabled=False))

    r = client.get("/api/lampa/plugin.js")
    assert r.status_code == 403

    r = client.get("/api/lampa/collections")
    assert r.status_code == 403
