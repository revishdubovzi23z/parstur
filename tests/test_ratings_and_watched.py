import pytest
from fastapi.testclient import TestClient

from main import app


@pytest.fixture
def client():
    return TestClient(app)


def test_db_rating_and_watched(tmp_db):
    # Insert a dummy item
    with tmp_db._conn() as c:
        c.execute(
            "INSERT INTO items (id, title, year, category_id, imdb_id, kp_id, title_norm) "
            "VALUES (42, 'Inception', 2010, 1, 'tt1375666', '447301', 'inception')"
        )

    # Test rating
    tmp_db.rate_item(42, 9)
    item = tmp_db.get_item(42)
    assert item["user_rating"] == 9

    # Verify user_ratings entry was created
    with tmp_db._conn() as c:
        row = c.execute("SELECT rating FROM user_ratings WHERE imdb_id = 'tt1375666'").fetchone()
    assert row is not None
    assert row[0] == 9

    # Test clearing rating
    tmp_db.rate_item(42, None)
    item = tmp_db.get_item(42)
    assert item["user_rating"] is None

    # Verify user_ratings entry was deleted
    with tmp_db._conn() as c:
        row = c.execute("SELECT rating FROM user_ratings WHERE imdb_id = 'tt1375666'").fetchone()
    assert row is None

    # Test watched status
    tmp_db.mark_watched(42, True)
    item = tmp_db.get_item(42)
    assert item["is_watched"] == 1
    assert item["watched_at"] is not None

    # Check that it got added to the "Просмотренное" collection
    colls = tmp_db.get_collections(include_system=True)
    watched_coll = next((c for c in colls if c["name"] == "Просмотренное"), None)
    assert watched_coll is not None
    assert watched_coll["is_system"] == 1

    # Check item is in collection
    item_colls = tmp_db.get_item_collections(42)
    assert watched_coll["id"] in item_colls

    # Test unwatching
    tmp_db.mark_watched(42, False)
    item = tmp_db.get_item(42)
    assert item["is_watched"] == 0
    assert item["watched_at"] is None

    item_colls = tmp_db.get_item_collections(42)
    assert watched_coll["id"] not in item_colls


def test_api_rating_and_watched(tmp_db, client, monkeypatch):
    import routes.items

    monkeypatch.setattr(routes.items, "db", tmp_db)

    # Insert item
    with tmp_db._conn() as c:
        c.execute(
            "INSERT INTO items (id, title, year, category_id) VALUES (100, 'Interstellar', 2014, 1)"
        )

    # API Rate
    resp = client.post("/api/item/100/rate", json={"rating": 8})
    assert resp.status_code == 200
    assert tmp_db.get_item(100)["user_rating"] == 8

    # API Watched
    resp = client.post("/api/item/100/watched", json={"watched": True})
    assert resp.status_code == 200
    assert tmp_db.get_item(100)["is_watched"] == 1


def test_export_endpoints(tmp_db, client, monkeypatch):
    import routes.export

    monkeypatch.setattr(routes.export, "db", tmp_db)

    with tmp_db._conn() as c:
        c.execute(
            "INSERT INTO items (id, title, year, category_id, imdb_id, kp_id, tmdb_id, user_rating, is_watched, watched_at) "
            "VALUES (200, 'The Dark Knight', 2008, 1, 'tt0468569', '111', '155', 10, 1, '2026-05-29 12:00:00')"
        )

    # Test IMDb watched CSV export
    resp = client.get("/api/export/watched/imdb")
    assert resp.status_code == 200
    assert "text/csv" in resp.headers["content-type"]
    assert "tt0468569" in resp.text
    assert "The Dark Knight" in resp.text

    # Test IMDb ratings CSV export
    resp = client.get("/api/export/ratings/imdb")
    assert resp.status_code == 200
    assert "tt0468569" in resp.text
    assert "10" in resp.text

    # Test KP export
    resp = client.get("/api/export/watched/kp")
    assert resp.status_code == 200
    assert "111" in resp.text
    assert "The Dark Knight" in resp.text

    # Test Trakt JSON export
    resp = client.get("/api/export/watched/trakt")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["movies"]) == 1
    assert data["movies"][0]["title"] == "The Dark Knight"
    assert data["movies"][0]["ids"]["imdb"] == "tt0468569"
    assert data["movies"][0]["ids"]["tmdb"] == 155
