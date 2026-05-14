"""Tests for item_search_names + transliteration / homoglyph search (7.7).

The `get_feed` search clause hits three columns:
    items.title LIKE ?  OR  items.title_norm LIKE ?
       OR  EXISTS (SELECT 1 FROM item_search_names WHERE name_norm LIKE ?)

`title_norm` is `app_core.normalize_title(title)` — case-folded,
strip non-alphanum, with `x → х` (latin → cyrillic homoglyph).
`item_search_names` is a many-to-one table populated during
sync_job: each row stores an additional normalised name (translit,
original_title, etc.) so a search can match either side without
having to mutate the canonical row.

This test verifies that the LIKE-against-name_norm half of the
search clause actually fires for the rows we'd expect, including
the latin/cyrillic homoglyph case.
"""

from __future__ import annotations

import sqlite3

from app_core import normalize_title


def _add_item(d, *, title: str, year: int = 2020, kp_id: str = "") -> int:
    """Insert an items row and run the title_norm/search-names plumbing."""
    norm = normalize_title(title)
    with d._conn() as c:
        cur = c.execute(
            "INSERT INTO items (category_id, title, year, kp_id, title_norm) "
            "VALUES (?, ?, ?, ?, ?)",
            (1, title, year, kp_id, norm),
        )
        return int(cur.lastrowid)


def _search_via_get_feed(d, q: str) -> list[int]:
    """Run the same WHERE clause get_feed uses, return matched item ids."""
    pattern = f"%{normalize_title(q)}%"
    pattern_raw = f"%{q}%"
    with d._conn() as c:
        c.row_factory = sqlite3.Row
        rows = c.execute(
            "SELECT items.id FROM items "
            "WHERE items.title LIKE ? OR items.title_norm LIKE ? "
            "OR EXISTS (SELECT 1 FROM item_search_names sn "
            "           WHERE sn.item_id = items.id AND sn.name_norm LIKE ?)",
            (pattern_raw, pattern, pattern),
        ).fetchall()
    return [r["id"] for r in rows]


def test_normalize_title_folds_latin_x_to_cyrillic_kha():
    # The whole point of normalize_title's `x -> х` step.
    assert normalize_title("Xenon") == normalize_title("Хenon")
    # And both fold case + strip diacritics.
    assert normalize_title("X-Files") == normalize_title("х-files")


def test_search_matches_via_search_names_alias(tmp_db):
    item_id = _add_item(tmp_db, title="Назад в будущее")
    # Simulate sync_job's behaviour: register an English alias.
    tmd_db = tmp_db
    tmd_db.insert_search_name(item_id, normalize_title("Back to the Future"))

    # A search by the English alias must hit via item_search_names,
    # because items.title and items.title_norm both contain Cyrillic.
    hits = _search_via_get_feed(tmp_db, "back")
    assert item_id in hits, f"alias lookup missed item: {hits}"

    hits = _search_via_get_feed(tmp_db, "future")
    assert item_id in hits, f"alias lookup missed 'future': {hits}"


def test_search_matches_homoglyph_x_kha(tmp_db):
    """User typed `xenon` (latin x) but the title is `Хenon` (cyrillic х) — and vice versa.

    Both sides flow through normalize_title before storage and before
    matching, so the resulting `title_norm` and the query's
    normalised form must agree.
    """
    item_id = _add_item(tmp_db, title="Хenon")
    hits = _search_via_get_feed(tmp_db, "xenon")
    assert item_id in hits

    item2 = _add_item(tmp_db, title="Xenon Ultra", year=2021)
    hits = _search_via_get_feed(tmp_db, "хenon")
    assert item2 in hits


def test_search_misses_unrelated(tmp_db):
    _add_item(tmp_db, title="Назад в будущее")
    assert _search_via_get_feed(tmp_db, "матрица") == []


def test_remove_search_names_drops_alias_match(tmp_db):
    item_id = _add_item(tmp_db, title="Назад в будущее")
    tmp_db.insert_search_name(item_id, normalize_title("Back to the Future"))

    assert _search_via_get_feed(tmp_db, "back") == [item_id]

    tmp_db.delete_search_names_by_item(item_id)
    assert _search_via_get_feed(tmp_db, "back") == []
