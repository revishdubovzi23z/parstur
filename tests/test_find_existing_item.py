"""Integration-flavoured tests for db.find_existing_item.

NOTE: title_norm columns store the *normalize_title()* output, which
folds Latin 'x' to Cyrillic 'х' (see test_app_core). When we insert
fixtures, we run the title through normalize_title to mirror what
the production add_item path does — otherwise the lookup compares
"matrix" against "matriх" and returns nothing.

This is the function fixed in 1.7 — the bug was that `kp_id` /
`imdb_id` / `rezka_url` matches did NOT consider category_id, so
the same external id legitimately appearing in two categories would
silently merge unrelated items. These tests pin both the happy path
(find by id within the right category) and the negative path
(reject when category mismatches).
"""

from __future__ import annotations


def _insert_item(
    db,
    *,
    title: str,
    year: int,
    category_id: int,
    kp_id: str | None = None,
    imdb_id: str | None = None,
    rezka_url: str | None = None,
) -> int:
    """Insert a row and return its id. title_norm is always derived
    via normalize_title so it matches what production code stores."""
    from app_core import normalize_title

    with db._conn() as c:
        cur = c.execute(
            """
            INSERT INTO items (
                title, year, category_id,
                kp_id, imdb_id, rezka_url, title_norm
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                title,
                year,
                category_id,
                kp_id,
                imdb_id,
                rezka_url,
                normalize_title(title),
            ),
        )
        return cur.lastrowid  # type: ignore[return-value]


class TestKpIdMatching:
    def test_finds_when_category_matches(self, tmp_db) -> None:
        item_id = _insert_item(tmp_db, title="Matrix", year=1999, category_id=1, kp_id="301")
        found = tmp_db.find_existing_item(kp_id="301", category_id=1)
        assert found == item_id

    def test_rejects_when_category_mismatches(self, tmp_db) -> None:
        # 1.7 regression test: same kp_id present, but in a different
        # category. find_existing_item must NOT collapse the two.
        _insert_item(tmp_db, title="Matrix", year=1999, category_id=1, kp_id="301")
        found = tmp_db.find_existing_item(kp_id="301", category_id=4)
        assert found is None

    def test_finds_across_categories_when_unspecified(self, tmp_db) -> None:
        # When the caller doesn't know the category yet, the function
        # falls back to the global lookup. This codepath is still in
        # use by tooling that just wants "any row with this kp_id".
        item_id = _insert_item(tmp_db, title="Matrix", year=1999, category_id=1, kp_id="301")
        found = tmp_db.find_existing_item(kp_id="301")
        assert found == item_id


class TestImdbIdMatching:
    def test_finds_when_category_matches(self, tmp_db) -> None:
        item_id = _insert_item(
            tmp_db,
            title="Matrix",
            year=1999,
            category_id=1,
            imdb_id="tt0133093",
        )
        found = tmp_db.find_existing_item(imdb_id="tt0133093", category_id=1)
        assert found == item_id

    def test_rejects_when_category_mismatches(self, tmp_db) -> None:
        _insert_item(
            tmp_db,
            title="Matrix",
            year=1999,
            category_id=1,
            imdb_id="tt0133093",
        )
        found = tmp_db.find_existing_item(imdb_id="tt0133093", category_id=4)
        assert found is None


class TestRezkaUrlMatching:
    def test_finds_when_category_matches(self, tmp_db) -> None:
        item_id = _insert_item(
            tmp_db,
            title="Matrix",
            year=1999,
            category_id=1,
            rezka_url="https://rezka.ag/films/fantastic/1234-matrix.html",
        )
        found = tmp_db.find_existing_item(
            rezka_url="https://rezka.ag/films/fantastic/1234-matrix.html",
            category_id=1,
        )
        assert found == item_id

    def test_rejects_when_category_mismatches(self, tmp_db) -> None:
        _insert_item(
            tmp_db,
            title="Matrix",
            year=1999,
            category_id=1,
            rezka_url="https://rezka.ag/films/fantastic/1234-matrix.html",
        )
        found = tmp_db.find_existing_item(
            rezka_url="https://rezka.ag/films/fantastic/1234-matrix.html",
            category_id=4,
        )
        assert found is None


class TestTitleNormFallback:
    def test_finds_by_title_when_no_external_ids(self, tmp_db) -> None:
        item_id = _insert_item(
            tmp_db,
            title="Matrix",
            year=1999,
            category_id=1,
        )
        # find_existing_item runs its own normalize_title on the
        # incoming title_norm argument, so passing the raw title is
        # the supported call shape.
        found = tmp_db.find_existing_item(title_norm="Matrix", year=1999, category_id=1)
        assert found == item_id

    def test_year_within_one_is_acceptable(self, tmp_db) -> None:
        # The matcher tolerates ±1 year because Rutor and Kinopoisk
        # disagree about release vs theatrical year more often than
        # not. Anything outside ±1 must NOT match.
        item_id = _insert_item(
            tmp_db,
            title="Matrix",
            year=1999,
            category_id=1,
        )
        assert tmp_db.find_existing_item(title_norm="Matrix", year=2000, category_id=1) == item_id
        assert tmp_db.find_existing_item(title_norm="Matrix", year=1998, category_id=1) == item_id

    def test_no_match_returns_none(self, tmp_db) -> None:
        assert tmp_db.find_existing_item(kp_id="999", category_id=1) is None
        assert tmp_db.find_existing_item(title_norm="Nonexistent", year=1999) is None
