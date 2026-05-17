"""GET /api/jacred/search — parstur-as-JacRed-source contract tests.

These pin the shape consumed by Lampac's ParsturController (see
the Lampac PR `Make parstur a JacRed torrent source`). Anything that
breaks here will silently break Lampac integration, so the assertions
are explicit about field names rather than blanket "is a list".
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
    from routes import jacred as jacred_routes

    jacred_routes.db = main.db
    main.db.init_schema()

    with main.db._conn() as c:
        # Two items so we can confirm the search filter matches the
        # right one. Inception (matches "Inception") + an unrelated
        # film that must NOT appear in the response.
        c.execute(
            "INSERT INTO items (id, category_id, title, original_title, year, "
            "kp_id, imdb_id, title_norm) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (1, 1, "Inception", "Inception", 2010, "447301", "tt1375666", "inception"),
        )
        c.execute(
            "INSERT INTO items (id, category_id, title, original_title, year, "
            "title_norm) VALUES (?, ?, ?, ?, ?, ?)",
            (2, 1, "Some Other Movie", "Some Other Movie", 2015, "someothermovie"),
        )
        # Release with a magnet — must appear.
        c.execute(
            "INSERT INTO releases (id, item_id, rutor_id, torrent_title, "
            "link, magnet, size, date_added) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                10,
                1,
                "rt-1",
                "Inception.2010.1080p.BluRay.x264",
                "https://rutor.info/torrent/10/inception",
                "magnet:?xt=urn:btih:abc123",
                "2.5 GB",
                "2024-01-15 10:00:00",
            ),
        )
        # Release WITHOUT a magnet — must be filtered out (Lampac can't
        # stream those).
        c.execute(
            "INSERT INTO releases (id, item_id, rutor_id, torrent_title, "
            "link, magnet, size, date_added) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                11,
                1,
                "rt-2",
                "Inception.2010.4K.HDR",
                "https://rutor.info/torrent/11/inception-4k",
                "",
                "12.4 GB",
                "2024-02-20 10:00:00",
            ),
        )
        # Release linked to the unrelated item — must be filtered out
        # by the title match, not by the magnet check.
        c.execute(
            "INSERT INTO releases (id, item_id, rutor_id, torrent_title, "
            "magnet, size, date_added) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                12,
                2,
                "rt-3",
                "Some.Other.Movie.2015.1080p",
                "magnet:?xt=urn:btih:def456",
                "1.8 GB",
                "2024-03-10 10:00:00",
            ),
        )
    return TestClient(main.app)


def test_search_returns_matching_releases(client: TestClient) -> None:
    r = client.get("/api/jacred/search?query=Inception")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "results" in body
    assert isinstance(body["results"], list)
    # Only the magnet-bearing Inception release; the unrelated movie
    # and the no-magnet 4K release must be filtered out.
    assert len(body["results"]) == 1

    rel = body["results"][0]
    assert rel["tracker"] == "parstur"
    assert rel["title"] == "Inception.2010.1080p.BluRay.x264"
    assert rel["magnet"] == "magnet:?xt=urn:btih:abc123"
    assert rel["url"] == "https://rutor.info/torrent/10/inception"
    assert rel["size_name"] == "2.5 GB"

    item = rel["item"]
    assert item["id"] == 1
    assert item["title"] == "Inception"
    assert item["year"] == 2010
    assert item["kp_id"] == "447301"
    assert item["imdb_id"] == "tt1375666"


def test_search_empty_query_returns_empty_results(client: TestClient) -> None:
    r = client.get("/api/jacred/search?query=")
    assert r.status_code == 200
    assert r.json() == {"results": []}


def test_search_no_match_returns_empty_results(client: TestClient) -> None:
    r = client.get("/api/jacred/search?query=NonexistentTitleString")
    assert r.status_code == 200
    body = r.json()
    assert body["results"] == []


def test_search_respects_limit(client: TestClient) -> None:
    # Add multiple matching releases to confirm `limit` truncates.
    with main.db._conn() as c:
        for i in range(20, 30):
            c.execute(
                "INSERT INTO releases (id, item_id, rutor_id, torrent_title, "
                "magnet, size, date_added) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    i,
                    1,
                    f"rt-{i}",
                    f"Inception.2010.Rip.{i}",
                    f"magnet:?xt=urn:btih:zzz{i}",
                    "2.0 GB",
                    f"2024-04-{i:02d} 10:00:00",
                ),
            )

    r = client.get("/api/jacred/search?query=Inception&limit=3")
    assert r.status_code == 200
    assert len(r.json()["results"]) == 3


def test_search_limit_clamped_to_max(client: TestClient) -> None:
    # The Query(le=200) validator should reject limit > 200 with 422.
    r = client.get("/api/jacred/search?query=Inception&limit=5000")
    assert r.status_code == 422
