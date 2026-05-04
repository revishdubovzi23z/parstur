"""Unit tests for db._placeholders / _materialize_id_list.

Both helpers underpin the SQL-injection-safe IN-clause handling in
get_feed and get_categories_with_counts. A regression in either
re-introduces the original CVE class (string-formatted ids) or
breaks the >256 id-list path.
"""

from __future__ import annotations

import pytest

from db import _LARGE_ID_LIST_THRESHOLD, _materialize_id_list, _placeholders


class TestPlaceholders:
    def test_empty_returns_null_sentinel(self) -> None:
        # SQLite rejects literal `IN ()`; the helper substitutes
        # `NULL` so the matching IN clause never matches anything,
        # which is the right default when callers ask for "filter
        # by an empty id list".
        assert _placeholders([]) == "NULL"

    def test_single_element(self) -> None:
        assert _placeholders([1]) == "?"

    def test_three_elements(self) -> None:
        assert _placeholders([1, 2, 3]) == "?,?,?"

    def test_works_with_tuples(self) -> None:
        assert _placeholders((1, 2)) == "?,?"

    def test_works_with_iterables_lacking_len(self) -> None:
        # The helper's docstring says it handles iterables that
        # don't expose __len__; confirm with a generator.
        assert _placeholders(x for x in (10, 20, 30)) == "?,?,?"

    def test_does_not_inject_user_data(self) -> None:
        # Even if someone passes a hostile string like "1' OR '1'='1",
        # _placeholders only emits `?,?,?...` — the data itself is
        # never interpolated. This regression-tests the CVE class.
        out = _placeholders(["1' OR '1'='1", "DROP TABLE items"])
        assert out == "?,?"
        assert "OR" not in out and "DROP" not in out


class TestMaterializeIdList:
    def test_round_trip(self, tmp_db) -> None:
        ids = list(range(_LARGE_ID_LIST_THRESHOLD + 50))
        with tmp_db._conn() as c:
            table = _materialize_id_list(c, ids, "test")
            count = c.execute(f"SELECT COUNT(*) FROM temp.{table}").fetchone()[0]
            assert count == len(ids)
            # First/last sanity check. fetchone() returns a sqlite3.Row
            # by default in this codebase (see Database._conn), so
            # index by position rather than comparing to a tuple.
            row = c.execute(f"SELECT MIN(id), MAX(id) FROM temp.{table}").fetchone()
            assert row[0] == 0
            assert row[1] == len(ids) - 1

    def test_drops_existing_table(self, tmp_db) -> None:
        # The helper must reset the table on every call so a second
        # invocation with smaller content doesn't see the union of
        # both runs.
        with tmp_db._conn() as c:
            _materialize_id_list(c, [1, 2, 3, 4, 5], "test")
            _materialize_id_list(c, [99], "test")
            count = c.execute("SELECT COUNT(*) FROM temp._par2_test").fetchone()[0]
            assert count == 1

    def test_handles_duplicates(self, tmp_db) -> None:
        # INSERT OR IGNORE drops dupes — the materialised set is
        # the unique set of ids the caller passed in.
        with tmp_db._conn() as c:
            _materialize_id_list(c, [1, 1, 2, 2, 3, 3], "test")
            count = c.execute("SELECT COUNT(*) FROM temp._par2_test").fetchone()[0]
            assert count == 3

    @pytest.mark.parametrize(
        "name_hint",
        ["feed", "stats", "ratings"],
    )
    def test_table_name_uses_hint(self, tmp_db, name_hint: str) -> None:
        with tmp_db._conn() as c:
            table = _materialize_id_list(c, [1, 2], name_hint)
            assert table == f"_par2_{name_hint}"
