"""6.6 — DB backup contract.

Pins the behaviours that downstream tooling (cron, the
`/api/backup/download` endpoint, the CLI `backup_db.py`) all rely
on:
  * Database.backup_to writes a usable copy of the schema + data.
  * Reads from the backup don't require the source DB to be present.
  * An existing destination is overwritten atomically (no half-written
    file on failure).
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from db import Database


@pytest.fixture()
def tmp_src(tmp_path: Path) -> Database:
    src_path = tmp_path / "src.db"
    d = Database(str(src_path))
    d.init_schema()
    # Insert a tiny known row so the backup actually has data, not
    # just an empty schema.
    with d._conn() as c:
        c.execute(
            "INSERT INTO categories (id, name) VALUES (?, ?)",
            (42, "smoke-test-category"),
        )
    return d


def test_backup_creates_destination(tmp_src: Database, tmp_path: Path) -> None:
    dest = tmp_path / "dest.db"
    size = tmp_src.backup_to(str(dest))
    assert dest.exists()
    assert size > 0
    assert dest.stat().st_size == size


def test_backup_preserves_data(tmp_src: Database, tmp_path: Path) -> None:
    dest = tmp_path / "dest.db"
    tmp_src.backup_to(str(dest))
    # Open the backup in isolation. The smoke-test row must be there
    # and PRAGMA user_version must match the source.
    conn = sqlite3.connect(str(dest))
    try:
        rows = conn.execute("SELECT id, name FROM categories").fetchall()
        assert (42, "smoke-test-category") in rows
        v = conn.execute("PRAGMA user_version").fetchone()[0]
        assert v >= 1  # at least the baseline migration applied
    finally:
        conn.close()


def test_backup_overwrites_existing(tmp_src: Database, tmp_path: Path) -> None:
    dest = tmp_path / "dest.db"
    # Pre-create with garbage; backup must replace it cleanly.
    dest.write_bytes(b"GARBAGE-NOT-A-VALID-DB")
    tmp_src.backup_to(str(dest))
    # The replacement must be a valid SQLite db, not the garbage.
    conn = sqlite3.connect(str(dest))
    try:
        v = conn.execute("PRAGMA user_version").fetchone()[0]
        assert v >= 1
    finally:
        conn.close()


def test_backup_creates_parent_dir(tmp_src: Database, tmp_path: Path) -> None:
    dest = tmp_path / "nested" / "subdir" / "dest.db"
    assert not dest.parent.exists()
    tmp_src.backup_to(str(dest))
    assert dest.exists()


def test_backup_failure_does_not_leave_tempfile(
    tmp_src: Database, tmp_path: Path, monkeypatch
) -> None:
    """If the backup raises mid-copy, the staging file must be
    cleaned up — we don't want `.par2-backup-*.db` files
    accumulating on disk over time."""
    dest = tmp_path / "dest.db"

    real_replace = os.replace

    def _boom(*args, **kwargs):
        raise OSError("simulated failure")

    monkeypatch.setattr(os, "replace", _boom)
    with pytest.raises(OSError):
        tmp_src.backup_to(str(dest))
    monkeypatch.setattr(os, "replace", real_replace)

    # Walk the directory and assert no leftover staging files.
    leftovers = [p for p in tmp_path.iterdir() if p.name.startswith(".par2-backup-")]
    assert not leftovers, leftovers
