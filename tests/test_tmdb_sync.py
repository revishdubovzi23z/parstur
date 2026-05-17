from unittest.mock import MagicMock

import pytest

import tmdb_sync
from tmdb_sync import sync_tmdb_collections


class FakeTMDBClient:
    def __init__(self):
        self.api_token = "fake_token"
        self.created_lists = []
        self.added_items = {}
        self.removed_items = {}
        self.list_items = {}

    def create_list(self, name, description=""):
        list_id = f"list_{len(self.created_lists) + 1}"
        self.created_lists.append({"id": list_id, "name": name, "description": description})
        self.list_items[list_id] = []
        return list_id

    def get_list_items(self, list_id):
        return self.list_items.get(list_id, [])

    def add_items_to_list(self, list_id, items):
        if list_id not in self.added_items:
            self.added_items[list_id] = []
        self.added_items[list_id].extend(items)
        # Also update list_items for get_list_items to work
        for item in items:
            self.list_items[list_id].append(
                {"media_type": item["media_type"], "id": item["media_id"]}
            )
        return True

    def remove_items_from_list(self, list_id, items):
        if list_id not in self.removed_items:
            self.removed_items[list_id] = []
        self.removed_items[list_id].extend(items)
        # Update list_items
        for item in items:
            self.list_items[list_id] = [
                i
                for i in self.list_items[list_id]
                if not (i["media_type"] == item["media_type"] and i["id"] == item["media_id"])
            ]
        return True

    def find_by_imdb_id(self, imdb_id, return_meta=False):
        if imdb_id == "tt12345":
            return (
                {"tmdb_id": 123, "media_type": "movie"} if return_meta else {"title": "Test Movie"}
            )
        return None

    def search_movie(self, title, year=None):
        if title == "Test Movie 2":
            return {"tmdb_id": 456, "media_type": "movie"}
        return None


@pytest.fixture
def fake_tmdb_client(monkeypatch):
    fake = FakeTMDBClient()
    monkeypatch.setattr(tmdb_sync, "TMDBClient", lambda: fake)
    return fake


def test_sync_tmdb_collections_creates_list(tmp_db, fake_tmdb_client, monkeypatch):
    # Monkeypatch db in tmdb_sync to use tmp_db
    monkeypatch.setattr(tmdb_sync, "db", tmp_db)

    # Create a collection in tmp_db
    tmp_db.create_collection("My Coll")

    # Run sync
    sync_tmdb_collections()

    # Assert list was created
    assert len(fake_tmdb_client.created_lists) == 1
    assert fake_tmdb_client.created_lists[0]["name"] == "My Coll"

    # Assert mapping was saved in app_state
    with tmp_db._conn() as c:
        row = c.execute("SELECT value FROM app_state WHERE key = 'tmdb_list_id_1'").fetchone()
    assert row is not None
    assert row[0] == "list_1"


def test_sync_tmdb_collections_adds_items(tmp_db, fake_tmdb_client, monkeypatch):
    monkeypatch.setattr(tmdb_sync, "db", tmp_db)

    tmp_db.create_collection("My Coll")

    # Add an item to DB
    with tmp_db._conn() as c:
        c.execute(
            "INSERT INTO items (id, title, imdb_id, category_id) VALUES (1, 'Test Movie', 'tt12345', 1)"
        )
        c.execute("INSERT INTO collection_items (collection_id, item_id) VALUES (1, 1)")

    sync_tmdb_collections()

    # Assert item was added to TMDB list
    assert "list_1" in fake_tmdb_client.added_items
    assert len(fake_tmdb_client.added_items["list_1"]) == 1
    assert fake_tmdb_client.added_items["list_1"][0]["media_id"] == 123
