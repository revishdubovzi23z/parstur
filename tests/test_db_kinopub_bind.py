"""Tests for `db.items` kinopub_bind / kinopub_unbind (PR 3).

These helpers attach and detach the `kinopub_id` / `kinopub_url` /
`kinopub_type` columns on `items`. They also flip the
`checked_kinopub` flag so the future sync_kinopub matcher (PR 4)
knows whether a row needs re-evaluating.
"""

from __future__ import annotations


def _insert_item(db, *, title: str = "Inception", year: int = 2010) -> int:
    with db._conn() as c:
        cur = c.execute(
            "INSERT INTO items (title, year, category_id) VALUES (?, ?, ?)",
            (title, year, 1),
        )
        return int(cur.lastrowid)


def test_bind_returns_none_for_missing_item(tmp_db) -> None:
    assert tmp_db.kinopub_bind(999_999, kinopub_id=42) is None


def test_unbind_returns_none_for_missing_item(tmp_db) -> None:
    assert tmp_db.kinopub_unbind(999_999) is None


def test_bind_writes_all_fields(tmp_db) -> None:
    item_id = _insert_item(tmp_db)
    result = tmp_db.kinopub_bind(
        item_id,
        kinopub_id=12345,
        kinopub_type="movie",
        kinopub_url="https://kino.pub/item/12345",
    )
    assert result is not None
    assert result["before"] == {
        "kinopub_id": None,
        "kinopub_type": None,
        "kinopub_url": None,
    }
    assert result["after"] == {
        "kinopub_id": 12345,
        "kinopub_type": "movie",
        "kinopub_url": "https://kino.pub/item/12345",
    }
    row = tmp_db.get_item(item_id)
    assert row is not None
    assert row["kinopub_id"] == 12345
    assert row["kinopub_type"] == "movie"
    assert row["kinopub_url"] == "https://kino.pub/item/12345"
    # bind() is "I just matched this manually, skip it in sync".
    assert row["checked_kinopub"] == 1


def test_bind_overwrites_previous_binding(tmp_db) -> None:
    item_id = _insert_item(tmp_db)
    tmp_db.kinopub_bind(item_id, kinopub_id=1, kinopub_type="movie", kinopub_url="u1")
    result = tmp_db.kinopub_bind(item_id, kinopub_id=2, kinopub_type="serial", kinopub_url="u2")
    assert result is not None
    assert result["before"]["kinopub_id"] == 1
    assert result["after"]["kinopub_id"] == 2
    row = tmp_db.get_item(item_id)
    assert row["kinopub_id"] == 2
    assert row["kinopub_type"] == "serial"
    assert row["kinopub_url"] == "u2"


def test_bind_accepts_missing_optional_fields(tmp_db) -> None:
    """`kinopub_type` and `kinopub_url` are optional; bind by ID only."""
    item_id = _insert_item(tmp_db)
    result = tmp_db.kinopub_bind(item_id, kinopub_id=77)
    assert result is not None
    row = tmp_db.get_item(item_id)
    assert row["kinopub_id"] == 77
    assert row["kinopub_type"] is None
    assert row["kinopub_url"] is None


def test_unbind_clears_all_fields_and_resets_checked(tmp_db) -> None:
    item_id = _insert_item(tmp_db)
    tmp_db.kinopub_bind(item_id, kinopub_id=9, kinopub_type="movie", kinopub_url="u")
    result = tmp_db.kinopub_unbind(item_id)
    assert result is not None
    assert result["before"]["kinopub_id"] == 9
    assert result["after"] == {
        "kinopub_id": None,
        "kinopub_type": None,
        "kinopub_url": None,
    }
    row = tmp_db.get_item(item_id)
    assert row["kinopub_id"] is None
    assert row["kinopub_type"] is None
    assert row["kinopub_url"] is None
    # unbind() reopens the row for the next sync sweep.
    assert row["checked_kinopub"] == 0


def test_unbind_on_unbound_item_is_idempotent(tmp_db) -> None:
    item_id = _insert_item(tmp_db)
    result = tmp_db.kinopub_unbind(item_id)
    assert result is not None
    assert result["before"]["kinopub_id"] is None
    assert result["after"]["kinopub_id"] is None
