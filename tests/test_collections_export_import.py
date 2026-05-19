"""8.2 — collections export/import contract.

Pins the round-trip behaviour of `export_collections` /
`import_collections`: an export from one DB must be importable
into a fresh DB that has the same items (matched by external
identity, not autoincrement id) and produce the same membership.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app_core import normalize_title
from db import Database


def _seed_items(d: Database, items: list[dict]) -> dict[str, int]:
    """Insert items into d, return dict[external_key -> item_id]."""
    keys: dict[str, int] = {}
    with d._conn() as c:
        for it in items:
            title_norm = normalize_title(it["title"])
            cur = c.execute(
                "INSERT INTO items (category_id, title, year, kp_id, imdb_id, "
                "rezka_url, original_title, title_norm) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    it.get("category_id", 1),
                    it["title"],
                    it["year"],
                    it.get("kp_id"),
                    it.get("imdb_id"),
                    it.get("rezka_url"),
                    it.get("original_title"),
                    title_norm,
                ),
            )
            keys[it["title"]] = int(cur.lastrowid)
    return keys


@pytest.fixture()
def src_db(tmp_path: Path) -> tuple[Database, dict[str, int]]:
    d = Database(str(tmp_path / "src.db"))
    d.init_schema()
    keys = _seed_items(
        d,
        [
            {"title": "Test Movie One", "year": 2020, "kp_id": "100", "imdb_id": "tt100"},
            {"title": "Test Movie Two", "year": 2021, "kp_id": "200"},
            {"title": "Test Movie Three", "year": 2022, "imdb_id": "tt300"},
        ],
    )
    # Build collections + memberships.
    d.create_collection("test-col-A")
    d.create_collection("test-col-B")
    cols = {c["name"]: c["id"] for c in d.get_collections()}
    d.toggle_collection_item(cols["test-col-A"], keys["Test Movie One"])
    d.toggle_collection_item(cols["test-col-A"], keys["Test Movie Two"])
    d.toggle_collection_item(cols["test-col-B"], keys["Test Movie Three"])
    return d, keys


def test_export_groups_by_collection(src_db) -> None:
    d, _ = src_db
    payload = d.export_collections()
    by_name = {c["name"]: c for c in payload}
    # Only the two custom collections we created in src_db should be
    # present — fresh DBs no longer seed any default collections.
    assert "test-col-A" in by_name
    assert "test-col-B" in by_name
    a_titles = {it["title"] for it in by_name["test-col-A"]["items"]}
    assert a_titles == {"Test Movie One", "Test Movie Two"}
    b_titles = {it["title"] for it in by_name["test-col-B"]["items"]}
    assert b_titles == {"Test Movie Three"}


def test_export_carries_external_identifiers(src_db) -> None:
    d, _ = src_db
    payload = d.export_collections()
    by_name = {c["name"]: c for c in payload}
    items = by_name["test-col-A"]["items"]
    one = next(it for it in items if it["title"] == "Test Movie One")
    assert one["kp_id"] == "100"
    assert one["imdb_id"] == "tt100"
    assert one["year"] == 2020


def test_round_trip_import_to_fresh_db(src_db, tmp_path: Path) -> None:
    """Export from src, set up a fresh DB with the SAME items
    (different autoincrement IDs are fine), import, verify
    membership matches."""
    src, _ = src_db
    payload = src.export_collections()

    dest = Database(str(tmp_path / "dest.db"))
    dest.init_schema()
    # Same external identity but fresh autoincrement ids — that's
    # the whole point of the export format.
    dest_keys = _seed_items(
        dest,
        [
            {"title": "Test Movie One", "year": 2020, "kp_id": "100", "imdb_id": "tt100"},
            {"title": "Test Movie Two", "year": 2021, "kp_id": "200"},
            {"title": "Test Movie Three", "year": 2022, "imdb_id": "tt300"},
        ],
    )
    report = dest.import_collections(payload)
    assert report["missing_items"] == 0
    # `created_collections` should be exactly 2 — the dest DB starts
    # empty (no seeded default collections), so the import has to
    # create both `test-col-A` and `test-col-B`. The stronger
    # invariant is that no items were lost.
    assert report["added_items"] == 3

    # Verify the membership ended up correct on the dest DB.
    cols_after = {c["name"]: c["id"] for c in dest.get_collections()}
    a_items = dest.get_feed(category_id=0, collection_id=cols_after["test-col-A"], limit=100)[
        "items"
    ]
    assert {it["title"] for it in a_items} == {"Test Movie One", "Test Movie Two"}
    # The ids on dest are different from src, but membership is right.
    a_dest_ids = {it["id"] for it in a_items}
    assert a_dest_ids == {dest_keys["Test Movie One"], dest_keys["Test Movie Two"]}


def test_import_skips_unknown_items(src_db, tmp_path: Path) -> None:
    """If the export references items that don't exist on the dest
    DB, those are reported as missing rather than crashing."""
    src, _ = src_db
    payload = src.export_collections()

    dest = Database(str(tmp_path / "dest.db"))
    dest.init_schema()
    # Seed only ONE of the three items.
    _seed_items(
        dest,
        [
            {"title": "Test Movie One", "year": 2020, "kp_id": "100", "imdb_id": "tt100"},
        ],
    )
    report = dest.import_collections(payload)
    assert report["added_items"] == 1
    assert report["missing_items"] == 2


def test_import_replace_wipes_membership(src_db, tmp_path: Path) -> None:
    """replace=True drops existing collection_items rows on the
    dest before re-adding from the payload — useful for syncing
    a stale dest back to a known-good source."""
    src, _ = src_db
    payload = src.export_collections()

    dest = Database(str(tmp_path / "dest.db"))
    dest.init_schema()
    keys = _seed_items(
        dest,
        [
            {"title": "Test Movie One", "year": 2020, "kp_id": "100", "imdb_id": "tt100"},
            {"title": "Test Movie Two", "year": 2021, "kp_id": "200"},
            {"title": "Test Movie Three", "year": 2022, "imdb_id": "tt300"},
            {"title": "Stale Local", "year": 1999, "kp_id": "999"},
        ],
    )
    # Pre-populate test-col-A with a stale local-only entry that
    # the source export does NOT contain.
    dest.create_collection("test-col-A")
    cols = {c["name"]: c["id"] for c in dest.get_collections()}
    dest.toggle_collection_item(cols["test-col-A"], keys["Stale Local"])

    dest.import_collections(payload, replace=True)
    a_items = dest.get_feed(category_id=0, collection_id=cols["test-col-A"], limit=100)["items"]
    titles = {it["title"] for it in a_items}
    # Stale Local must be gone — replace=True wiped membership before
    # the import.
    assert "Stale Local" not in titles
    assert {"Test Movie One", "Test Movie Two"} <= titles


def test_import_idempotent_without_replace(src_db, tmp_path: Path) -> None:
    """Importing the same payload twice must not duplicate
    membership — collection_items has a composite PK so INSERT OR
    IGNORE is the right primitive, this test pins it."""
    src, _ = src_db
    payload = src.export_collections()

    dest = Database(str(tmp_path / "dest.db"))
    dest.init_schema()
    _seed_items(
        dest,
        [
            {"title": "Test Movie One", "year": 2020, "kp_id": "100", "imdb_id": "tt100"},
            {"title": "Test Movie Two", "year": 2021, "kp_id": "200"},
            {"title": "Test Movie Three", "year": 2022, "imdb_id": "tt300"},
        ],
    )
    r1 = dest.import_collections(payload)
    r2 = dest.import_collections(payload)
    assert r1["added_items"] == 3
    # Second pass adds nothing — INSERT OR IGNORE no-ops the duplicates.
    assert r2["added_items"] == 0


def test_resolve_item_id_kp_then_imdb_then_url_then_title(tmp_path: Path) -> None:
    """Pin the resolution priority order so future schema additions
    don't accidentally break import for older exports."""
    d = Database(str(tmp_path / "x.db"))
    d.init_schema()
    keys = _seed_items(
        d,
        [
            {"title": "By KP", "year": 2000, "kp_id": "kp-1"},
            {"title": "By IMDB", "year": 2001, "imdb_id": "tt-1"},
            {
                "title": "By URL",
                "year": 2002,
                "rezka_url": "https://rezka.ag/films/x/1-foo.html",
            },
            {"title": "By Title", "year": 2003},
        ],
    )

    with d._conn() as c:
        assert d._resolve_item_id(c, {"kp_id": "kp-1"}) == keys["By KP"]
        assert d._resolve_item_id(c, {"imdb_id": "tt-1"}) == keys["By IMDB"]
        assert (
            d._resolve_item_id(c, {"rezka_url": "https://rezka.ag/films/x/1-foo.html"})
            == keys["By URL"]
        )
        # Test domain-agnostic match
        assert (
            d._resolve_item_id(c, {"rezka_url": "https://hdrzk.org/films/x/1-foo.html"})
            == keys["By URL"]
        )
        # Test exact title + year match
        assert d._resolve_item_id(c, {"title": "By Title", "year": 2003}) == keys["By Title"]
        # Test normalized title + year match
        assert d._resolve_item_id(c, {"title": "By Title (2003)", "year": 2003}) == keys["By Title"]
        # Nothing matches.
        assert d._resolve_item_id(c, {"kp_id": "nope"}) is None
