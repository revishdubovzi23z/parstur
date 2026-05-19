"""Tests for `sync_kinopub.py` — the background matcher introduced in
PR 4 of the kino.pub integration.

We exercise the scoring logic in isolation (pure-function tests, no
DB needed) and the end-to-end ``run()`` loop against an in-memory
``Database`` via the ``tmp_db`` fixture and a fake ``KinopubClient``
that records every search request — no network involved.
"""

from __future__ import annotations

from typing import Any

import pytest

import sync_kinopub
from sync_kinopub import (
    _candidate_titles,
    _normalise_title,
    best_candidate,
    score_candidate,
)

# ---------------------------------------------------------------------------
# Pure-function helpers.


def test_normalise_title_strips_punctuation_and_case() -> None:
    assert _normalise_title("Inception (2010)!!") == "inception 2010"
    assert _normalise_title(None) == ""
    assert _normalise_title("") == ""
    assert _normalise_title("  ALL    UPPER  ") == "all upper"


def test_candidate_titles_splits_on_slash() -> None:
    item = {"title": "Начало / Inception"}
    assert _candidate_titles(item) == ["Начало", "Inception"]


def test_score_candidate_exact_title_year_type() -> None:
    item = {"title": "Inception", "year": 2010}
    cand = {"id": 1, "title": "Inception", "year": 2010, "type": "movie"}
    score = score_candidate(item=item, candidate=cand, type_hint="movie")
    # 50 (title) + 60 (year) + 40 (type) = 150
    assert score == 150


def test_score_candidate_punishes_wrong_year() -> None:
    item = {"title": "Inception", "year": 2010}
    cand = {"id": 1, "title": "Inception", "year": 1999, "type": "movie"}
    score = score_candidate(item=item, candidate=cand, type_hint="movie")
    # 50 (title) − 80 (year) + 40 (type) = 10 — below threshold (60)
    assert score < sync_kinopub.SCORE_MIN_ACCEPT


def test_score_candidate_off_by_one_year() -> None:
    item = {"title": "Inception", "year": 2010}
    cand = {"id": 1, "title": "Inception", "year": 2011, "type": "movie"}
    score = score_candidate(item=item, candidate=cand, type_hint="movie")
    # 50 + 30 + 40 = 120 — should still pass
    assert score >= sync_kinopub.SCORE_MIN_ACCEPT


def test_best_candidate_picks_highest_score() -> None:
    item = {"title": "Inception", "year": 2010}
    raw = [
        {"id": 100, "title": "Inception", "year": 1999, "type": "movie"},  # wrong year
        {"id": 200, "title": "Inception", "year": 2010, "type": "movie"},  # perfect
        {"id": 300, "title": "Inception: The Cobol Job", "year": 2010, "type": "movie"},
    ]
    pick = best_candidate(item=item, raw_results=raw, type_hint="movie")
    assert pick is not None
    cand, score = pick
    assert cand["id"] == 200
    assert score == 150


def test_best_candidate_returns_none_under_threshold() -> None:
    item = {"title": "Some Russian Drama", "year": 2010}
    raw = [
        # Title only partially matches and year is way off — should not pass.
        {"id": 1, "title": "Drama", "year": 1995, "type": "movie"},
    ]
    pick = best_candidate(item=item, raw_results=raw, type_hint="movie")
    assert pick is None


def test_best_candidate_handles_garbage_entries() -> None:
    item = {"title": "Inception", "year": 2010}
    raw: list[Any] = [
        "not a dict",
        {"title": "missing id"},
        None,
        {"id": 7, "title": "Inception", "year": 2010, "type": "movie"},
    ]
    pick = best_candidate(item=item, raw_results=raw, type_hint="movie")
    assert pick is not None
    cand, _ = pick
    assert cand["id"] == 7


# ---------------------------------------------------------------------------
# End-to-end run() against tmp_db + fake KinopubClient.


class _FakeClient:
    """Minimal stand-in for KinopubClient. Records each `search()`
    call's args so tests can assert what the matcher actually asked for.
    Implements the same `isinstance` check used by the production code
    by inheriting from the real client class — see fixture below.
    """

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.responses: list[list[dict]] = []

    def queue(self, response: list[dict]) -> None:
        self.responses.append(response)

    def search(
        self,
        query: str,
        *,
        type_: str | None = None,
        year: int | None = None,
        limit: int = 25,
    ) -> list[dict]:
        self.calls.append({"query": query, "type_": type_, "year": year, "limit": limit})
        if self.responses:
            return self.responses.pop(0)
        return []


@pytest.fixture()
def fake_client(monkeypatch) -> _FakeClient:
    """Patch the `KinopubClient` isinstance gate so `_make_client_factory`
    accepts our recorder. Returns the recorder instance."""
    fake = _FakeClient()
    monkeypatch.setattr(sync_kinopub, "KinopubClient", _FakeClient)
    return fake


def _seed_item(
    db,
    *,
    title: str,
    year: int | None = None,
    category_id: int = 1,
    checked: int = 0,
) -> int:
    with db._conn() as c:
        cur = c.execute(
            "INSERT INTO items (title, year, category_id, checked_kinopub) VALUES (?, ?, ?, ?)",
            (title, year, category_id, checked),
        )
        return int(cur.lastrowid)


def test_run_skips_when_no_eligible_items(tmp_db, fake_client) -> None:
    summary = sync_kinopub.run(db=tmp_db, client_factory=lambda: fake_client, delay_ms=0)
    assert summary == {"processed": 0, "bound": 0, "skipped": 0}
    assert fake_client.calls == []


def test_run_binds_matching_item(tmp_db, fake_client) -> None:
    item_id = _seed_item(tmp_db, title="Inception", year=2010, category_id=1)
    fake_client.queue(
        [
            {"id": 4242, "title": "Inception", "year": 2010, "type": "movie"},
        ]
    )

    summary = sync_kinopub.run(db=tmp_db, client_factory=lambda: fake_client, delay_ms=0)

    assert summary["bound"] == 1
    assert summary["skipped"] == 0
    assert fake_client.calls == [{"query": "Inception", "type_": None, "year": 2010, "limit": 25}]
    with tmp_db._conn() as c:
        row = c.execute(
            "SELECT kinopub_id, kinopub_type, kinopub_url, checked_kinopub FROM items WHERE id = ?",
            (item_id,),
        ).fetchone()
    assert row["kinopub_id"] == 4242
    assert row["kinopub_type"] == "movie"
    assert row["kinopub_url"] == "https://kino.pub/item/view/4242"
    assert row["checked_kinopub"] == 1


def test_run_falls_back_to_second_title_piece(tmp_db, fake_client) -> None:
    item_id = _seed_item(
        tmp_db, title="Несуществующее название / Inception", year=2010, category_id=1
    )
    fake_client.queue([])  # Primary search for first title piece with year=2010 -> returns empty
    fake_client.queue([])  # Fallback search for first title piece with year=None -> returns empty
    fake_client.queue(
        [{"id": 4242, "title": "Inception", "year": 2010, "type": "movie"}]
    )  # Primary search for second title piece with year=2010 -> returns match

    summary = sync_kinopub.run(db=tmp_db, client_factory=lambda: fake_client, delay_ms=0)

    assert summary["bound"] == 1
    assert fake_client.calls == [
        {"query": "Несуществующее название", "type_": None, "year": 2010, "limit": 25},
        {"query": "Несуществующее название", "type_": None, "year": None, "limit": 25},
        {"query": "Inception", "type_": None, "year": 2010, "limit": 25},
    ]
    with tmp_db._conn() as c:
        row = c.execute("SELECT kinopub_id FROM items WHERE id = ?", (item_id,)).fetchone()
    assert row["kinopub_id"] == 4242


def test_run_marks_checked_when_no_candidate(tmp_db, fake_client) -> None:
    item_id = _seed_item(tmp_db, title="Obscure Movie", year=1980, category_id=1)
    fake_client.queue([])  # Empty result set.

    summary = sync_kinopub.run(db=tmp_db, client_factory=lambda: fake_client, delay_ms=0)

    assert summary == {"processed": 1, "bound": 0, "skipped": 1}
    with tmp_db._conn() as c:
        row = c.execute(
            "SELECT kinopub_id, checked_kinopub FROM items WHERE id = ?",
            (item_id,),
        ).fetchone()
    assert row["kinopub_id"] is None
    assert row["checked_kinopub"] == 1


def test_run_skips_ineligible_categories(tmp_db, fake_client) -> None:
    # category 9 isn't in ELIGIBLE_CATEGORY_IDS — games/software.
    _seed_item(tmp_db, title="Some Game", year=2020, category_id=9)
    summary = sync_kinopub.run(db=tmp_db, client_factory=lambda: fake_client, delay_ms=0)
    assert summary == {"processed": 0, "bound": 0, "skipped": 0}
    assert fake_client.calls == []


def test_run_skips_already_checked(tmp_db, fake_client) -> None:
    _seed_item(tmp_db, title="Already Checked", category_id=1, checked=1)
    summary = sync_kinopub.run(db=tmp_db, client_factory=lambda: fake_client, delay_ms=0)
    assert summary == {"processed": 0, "bound": 0, "skipped": 0}


def test_run_returns_skipped_when_auth_fails(tmp_db) -> None:
    def _raises() -> Any:
        from kinopub_client import KinopubAuthError

        raise KinopubAuthError("not authenticated")

    _seed_item(tmp_db, title="Foo", category_id=1)
    summary = sync_kinopub.run(db=tmp_db, client_factory=_raises, delay_ms=0)
    assert summary["bound"] == 0
    assert summary["skipped"] == 1


def test_run_recheck_resets_flag(tmp_db, fake_client) -> None:
    item_id = _seed_item(tmp_db, title="Re-check Me", year=2010, category_id=1, checked=1)
    fake_client.queue([{"id": 7777, "title": "Re-check Me", "year": 2010, "type": "movie"}])

    summary = sync_kinopub.run(
        db=tmp_db,
        client_factory=lambda: fake_client,
        delay_ms=0,
        recheck=True,
    )

    assert summary["bound"] == 1
    with tmp_db._conn() as c:
        row = c.execute("SELECT kinopub_id FROM items WHERE id = ?", (item_id,)).fetchone()
    assert row["kinopub_id"] == 7777


def test_run_uses_all_title_pieces_for_query(tmp_db, fake_client) -> None:
    # The DB stores Russian / English titles separated by " / ".
    # Query both pieces because kino.pub may index only one language variant.
    _seed_item(tmp_db, title="Начало / Inception", year=2010, category_id=1)
    fake_client.queue([])
    fake_client.queue([])
    sync_kinopub.run(db=tmp_db, client_factory=lambda: fake_client, delay_ms=0)
    assert fake_client.calls == [
        {"query": "Начало", "type_": None, "year": 2010, "limit": 25},
        {"query": "Начало", "type_": None, "year": None, "limit": 25},
        {"query": "Inception", "type_": None, "year": 2010, "limit": 25},
        {"query": "Inception", "type_": None, "year": None, "limit": 25},
    ]


def test_run_falls_back_to_year_none_when_year_mismatch(tmp_db, fake_client) -> None:
    # DB has year 2024, but Kino.pub has year 2023.
    item_id = _seed_item(tmp_db, title="Last Straw", year=2024, category_id=1)
    # 1. Search with year=2024 returns empty (due to mismatch).
    fake_client.queue([])
    # 2. Fallback search with year=None returns the 2023 candidate.
    fake_client.queue([{"id": 5426, "title": "Last Straw", "year": 2023, "type": "movie"}])

    summary = sync_kinopub.run(db=tmp_db, client_factory=lambda: fake_client, delay_ms=0)

    # Should match! (score for 2023 vs 2024 off by one is 50 title + 30 year off by one + 40 type = 120 >= 60).
    assert summary["bound"] == 1
    assert fake_client.calls == [
        {"query": "Last Straw", "type_": None, "year": 2024, "limit": 25},
        {"query": "Last Straw", "type_": None, "year": None, "limit": 25},
    ]
    with tmp_db._conn() as c:
        row = c.execute("SELECT kinopub_id FROM items WHERE id = ?", (item_id,)).fetchone()
    assert row["kinopub_id"] == 5426


def test_run_continues_past_api_errors(tmp_db, fake_client) -> None:
    """When kino.pub returns 500 for one row, sync should mark it
    checked and move on instead of aborting the whole sweep."""
    from kinopub_client import KinopubAPIError

    a = _seed_item(tmp_db, title="First", year=2010, category_id=1)
    b = _seed_item(tmp_db, title="Second", year=2010, category_id=1)

    call_count = {"n": 0}

    def search_with_error(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise KinopubAPIError(500, "upstream sneezed")
        return [{"id": 555, "title": "First", "year": 2010, "type": "movie"}]

    fake_client.search = search_with_error  # type: ignore[assignment]

    summary = sync_kinopub.run(db=tmp_db, client_factory=lambda: fake_client, delay_ms=0)

    # We iterate items in descending id order (`ORDER BY id DESC`), so
    # `b` is processed first and raises; `a` should still be attempted.
    assert summary["processed"] == 2
    with tmp_db._conn() as c:
        row_b = c.execute(
            "SELECT checked_kinopub, kinopub_id FROM items WHERE id = ?", (b,)
        ).fetchone()
        row_a = c.execute(
            "SELECT checked_kinopub, kinopub_id FROM items WHERE id = ?", (a,)
        ).fetchone()
    # b raised — it should be marked checked, not bound.
    assert row_b["checked_kinopub"] == 1
    assert row_b["kinopub_id"] is None
    # a came after and the second response matched its title — bound.
    assert row_a["kinopub_id"] == 555
