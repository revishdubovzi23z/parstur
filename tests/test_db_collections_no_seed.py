"""Regression test: fresh `init_schema()` no longer seeds 11 default
collections.

Before the "lazy collections" change, `db/core.py` shipped a
hard-coded list of 11 personal collection names ("говноозвучки",
"топ фильмы", …) and inserted them via INSERT OR IGNORE on every
schema init. That meant a brand-new install presented the previous
owner's taxonomy in the sidebar before any HDRezka sync had run —
confusing for new operators and easy to mistake for production
data.

After the change, the `collections` table on a fresh DB is empty
until `sync_rezka_collections` (or a manual create/import) fills
it. The companion change is a banner in the SPA inviting the user
to kick off the first sync. Existing installs are unaffected: we
just stopped emitting new INSERTs, the old rows stay where they
are (this test does NOT exercise that — see migrations/0006
companion tests for upgrade-path coverage).
"""

from __future__ import annotations

from pathlib import Path

from db import Database


def test_fresh_db_has_no_seeded_collections(tmp_path: Path) -> None:
    db = Database(str(tmp_path / "fresh.db"))
    db.init_schema()
    assert db.get_collections() == [], (
        "Fresh DBs must start with zero collections — the SPA's "
        "empty-state banner depends on this. If you re-introduced "
        "a seed list, the sidebar will show stale rows on every "
        "new install."
    )


def test_existing_collections_survive_re_init(tmp_path: Path) -> None:
    """`init_schema` is idempotent and must not touch existing rows.

    This guards against a future change that 'cleans up' seed rows
    by DELETEing on init — that would wipe a user's real
    collections on every restart.
    """
    db = Database(str(tmp_path / "warm.db"))
    db.init_schema()
    db.create_collection("My Real Collection")
    db.create_collection("Another One")
    assert {c["name"] for c in db.get_collections()} == {
        "My Real Collection",
        "Another One",
    }

    # Simulate a restart / migration re-run.
    db.init_schema()
    assert {c["name"] for c in db.get_collections()} == {
        "My Real Collection",
        "Another One",
    }
