"""Tests for `kinopub_collections_sync.py` — the bi-directional
collection-folder sync against kino.pub's Bookmarks API.

Pure-function helpers are exercised in isolation; the end-to-end
``sync_kinopub_collections()`` loop runs against ``tmp_db`` and a
fake ``KinopubClient`` that records every call, mirroring the
pattern from `test_sync_kinopub.py`.
"""

from __future__ import annotations

from typing import Any

import pytest

import kinopub_collections_sync as kcs

# ---------------------------------------------------------------------------
# Pure helpers


def test_match_folder_exact() -> None:
    collections = [
        {"id": 1, "name": "Боевики"},
        {"id": 2, "name": "Документалки"},
    ]
    coll = kcs._match_folder_to_collection("Боевики", collections)
    assert coll is not None
    assert coll["id"] == 1


def test_match_folder_fuzzy_above_threshold() -> None:
    collections = [{"id": 9, "name": "Долгая прогулка"}]
    coll = kcs._match_folder_to_collection("Долгая прогулка ", collections)
    assert coll is not None
    assert coll["id"] == 9


def test_match_folder_no_match_returns_none() -> None:
    collections = [{"id": 1, "name": "Боевики"}]
    coll = kcs._match_folder_to_collection("Артхаус", collections)
    assert coll is None


def test_folder_title_falls_back_to_name() -> None:
    assert kcs._folder_title({"title": "X"}) == "X"
    assert kcs._folder_title({"name": "Y"}) == "Y"
    assert kcs._folder_title({}) == ""


def test_folder_id_coerces_and_returns_none_on_bad_input() -> None:
    assert kcs._folder_id({"id": "42"}) == 42
    assert kcs._folder_id({"id": 7}) == 7
    assert kcs._folder_id({}) is None
    assert kcs._folder_id({"id": "abc"}) is None


# ---------------------------------------------------------------------------
# Fake client for end-to-end coverage of sync_kinopub_collections()


class _FakeClient:
    """Stand-in for KinopubClient. Records every call and returns
    scripted responses for `list_bookmark_folders` / `get_bookmark_folder_items`
    / `search` / `add_to_bookmark_folder` / `create_bookmark_folder`.
    """

    def __init__(self) -> None:
        self.folders: list[dict] = []
        self.folder_items: dict[int, list[dict]] = {}
        self.search_responses: list[list[dict]] = []
        self.add_calls: list[dict] = []
        self.create_calls: list[str] = []
        self.search_calls: list[dict] = []

    # --- mirror KinopubClient surface used by runtime helpers ----------

    def list_bookmark_folders(self) -> list[dict]:
        return list(self.folders)

    def get_bookmark_folder_items(self, folder_id: int) -> list[dict]:
        return list(self.folder_items.get(int(folder_id), []))

    def add_to_bookmark_folder(self, *, item: int, folder: int) -> None:
        self.add_calls.append({"item": int(item), "folder": int(folder)})

    def create_bookmark_folder(self, title: str) -> dict:
        self.create_calls.append(title)
        new_id = 9000 + len(self.create_calls)
        folder = {"id": new_id, "title": title, "count": 0}
        self.folders.append(folder)
        return folder

    def search(
        self,
        query: str,
        *,
        type_: str | None = None,
        year: int | None = None,
        limit: int = 25,
    ) -> list[dict]:
        self.search_calls.append({"query": query, "type_": type_, "year": year})
        if self.search_responses:
            return self.search_responses.pop(0)
        return []


@pytest.fixture()
def fake_client() -> _FakeClient:
    return _FakeClient()


def _seed_item(
    db,
    *,
    title: str,
    year: int | None = None,
    category_id: int = 1,
    kinopub_id: int | None = None,
) -> int:
    with db._conn() as c:
        cur = c.execute(
            "INSERT INTO items (title, year, category_id, kinopub_id) VALUES (?, ?, ?, ?)",
            (title, year, category_id, kinopub_id),
        )
        return int(cur.lastrowid)


def _seed_collection(db, *, name: str) -> int:
    with db._conn() as c:
        cur = c.execute("INSERT INTO collections (name) VALUES (?)", (name,))
        return int(cur.lastrowid)


def _link(db, *, collection_id: int, item_id: int) -> None:
    with db._conn() as c:
        c.execute(
            "INSERT INTO collection_items (collection_id, item_id) VALUES (?, ?)",
            (collection_id, item_id),
        )


def test_aborts_when_no_folders(tmp_db, fake_client) -> None:
    summary = kcs.sync_kinopub_collections(db=tmp_db, client=fake_client)
    assert summary == {
        "kinopub_to_project": 0,
        "project_to_kinopub": 0,
        "new_kinopub_ids": 0,
    }


def test_creates_local_collection_when_folder_is_missing(tmp_db, fake_client) -> None:
    fake_client.folders = [{"id": 1, "title": "Боевики", "count": 0}]
    fake_client.folder_items[1] = []

    summary = kcs.sync_kinopub_collections(db=tmp_db, client=fake_client)

    names = [c["name"] for c in tmp_db.get_collections()]
    assert "Боевики" in names
    assert summary["kinopub_to_project"] == 0


def test_adds_kinopub_item_to_existing_local_collection(tmp_db, fake_client) -> None:
    coll_id = _seed_collection(tmp_db, name="Боевики")
    item_id = _seed_item(tmp_db, title="Foo", year=2020, kinopub_id=4242)

    fake_client.folders = [{"id": 7, "title": "Боевики", "count": 1}]
    fake_client.folder_items[7] = [{"id": 4242, "title": "Foo", "year": 2020}]

    summary = kcs.sync_kinopub_collections(db=tmp_db, client=fake_client)

    assert summary["kinopub_to_project"] == 1
    with tmp_db._conn() as c:
        row = c.execute(
            "SELECT 1 FROM collection_items WHERE collection_id = ? AND item_id = ?",
            (coll_id, item_id),
        ).fetchone()
    assert row is not None


def test_pushes_bound_local_item_to_kinopub(tmp_db, fake_client) -> None:
    coll_id = _seed_collection(tmp_db, name="Боевики")
    # Local item already bound to kinopub but not in kinopub folder.
    item_id = _seed_item(tmp_db, title="Foo", year=2020, kinopub_id=4242)
    _link(tmp_db, collection_id=coll_id, item_id=item_id)

    fake_client.folders = [{"id": 7, "title": "Боевики", "count": 0}]
    fake_client.folder_items[7] = []

    summary = kcs.sync_kinopub_collections(db=tmp_db, client=fake_client)

    assert summary["project_to_kinopub"] == 1
    assert summary["new_kinopub_ids"] == 0
    assert fake_client.add_calls == [{"item": 4242, "folder": 7}]


def test_searches_unbound_local_items_then_pushes(tmp_db, fake_client) -> None:
    coll_id = _seed_collection(tmp_db, name="Боевики")
    item_id = _seed_item(tmp_db, title="Inception", year=2010, kinopub_id=None)
    _link(tmp_db, collection_id=coll_id, item_id=item_id)

    fake_client.folders = [{"id": 7, "title": "Боевики", "count": 0}]
    fake_client.folder_items[7] = []
    fake_client.search_responses.append(
        [{"id": 999, "title": "Inception", "year": 2010, "type": "movie"}]
    )

    summary = kcs.sync_kinopub_collections(db=tmp_db, client=fake_client)

    assert summary["project_to_kinopub"] == 1
    assert summary["new_kinopub_ids"] == 1
    assert fake_client.add_calls == [{"item": 999, "folder": 7}]
    with tmp_db._conn() as c:
        row = c.execute(
            "SELECT kinopub_id, kinopub_type, kinopub_url FROM items WHERE id = ?",
            (item_id,),
        ).fetchone()
    assert row["kinopub_id"] == 999
    assert row["kinopub_type"] == "movie"
    assert row["kinopub_url"] == "https://kino.pub/item/view/999"


def test_unbound_local_item_with_no_search_match_is_skipped(tmp_db, fake_client) -> None:
    coll_id = _seed_collection(tmp_db, name="Боевики")
    item_id = _seed_item(tmp_db, title="Some Russian Drama", year=1980, kinopub_id=None)
    _link(tmp_db, collection_id=coll_id, item_id=item_id)

    fake_client.folders = [{"id": 7, "title": "Боевики", "count": 0}]
    fake_client.folder_items[7] = []
    fake_client.search_responses.append([])

    summary = kcs.sync_kinopub_collections(db=tmp_db, client=fake_client)

    assert summary["project_to_kinopub"] == 0
    assert summary["new_kinopub_ids"] == 0
    assert fake_client.add_calls == []
    with tmp_db._conn() as c:
        row = c.execute("SELECT kinopub_id FROM items WHERE id = ?", (item_id,)).fetchone()
    assert row["kinopub_id"] is None


def test_returns_zero_summary_on_auth_failure(tmp_db) -> None:
    from kinopub_client import KinopubAuthError

    class _FailingClient:
        def list_bookmark_folders(self) -> list[dict]:
            raise KinopubAuthError("not authenticated")

    summary = kcs.sync_kinopub_collections(db=tmp_db, client=_FailingClient())
    assert summary == {
        "kinopub_to_project": 0,
        "project_to_kinopub": 0,
        "new_kinopub_ids": 0,
    }


def test_continues_past_folder_fetch_error(tmp_db, fake_client) -> None:
    from kinopub_client import KinopubAPIError

    coll_id = _seed_collection(tmp_db, name="Working")
    item_id = _seed_item(tmp_db, title="A", year=2020, kinopub_id=11)

    fake_client.folders = [
        {"id": 1, "title": "Broken", "count": 0},
        {"id": 2, "title": "Working", "count": 1},
    ]

    def explode(_self_id: int) -> list[dict]:
        raise KinopubAPIError(500, "boom")

    # Override only for folder 1; folder 2 gets a real reply.
    real_fetch = fake_client.get_bookmark_folder_items

    def selective(folder_id: int) -> list[dict]:
        if int(folder_id) == 1:
            raise KinopubAPIError(500, "boom")
        return real_fetch(folder_id)

    fake_client.get_bookmark_folder_items = selective  # type: ignore[assignment]
    fake_client.folder_items[2] = [{"id": 11, "title": "A", "year": 2020}]

    summary = kcs.sync_kinopub_collections(db=tmp_db, client=fake_client)

    # Folder 1 should be skipped silently; folder 2 still wires the item up.
    assert summary["kinopub_to_project"] == 1
    with tmp_db._conn() as c:
        row = c.execute(
            "SELECT 1 FROM collection_items WHERE collection_id = ? AND item_id = ?",
            (coll_id, item_id),
        ).fetchone()
    assert row is not None


# ---------------------------------------------------------------------------
# _search_kinopub_id pure-ish helper (no DB; only client+item dict)


def test_search_kinopub_id_returns_best_match(fake_client) -> None:
    fake_client.search_responses.append(
        [
            {"id": 1, "title": "Wrong", "year": 1990, "type": "movie"},
            {"id": 2, "title": "Inception", "year": 2010, "type": "movie"},
        ]
    )
    item_info: dict[str, Any] = {
        "title": "Inception",
        "year": 2010,
        "category_id": 1,
    }
    found = kcs._search_kinopub_id(item_info, client=fake_client)
    assert found is not None
    kp_id, kp_type = found
    assert kp_id == 2
    assert kp_type == "movie"


def test_search_kinopub_id_returns_none_when_no_match(fake_client) -> None:
    fake_client.search_responses.append([])
    item_info: dict[str, Any] = {
        "title": "Obscure",
        "year": 1900,
        "category_id": 1,
    }
    assert kcs._search_kinopub_id(item_info, client=fake_client) is None
