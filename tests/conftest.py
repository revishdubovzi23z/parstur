"""Shared pytest fixtures.

Per item 5.1 — pytest scaffold. Each test file imports the fixtures
from here rather than rebuilding a fresh DB / temp filesystem on its
own.

Note: the project still keeps `app_data.db` in the repo root and a
few modules expect to find it there (TrackerAppCore default,
script_utils.load_checkpoint, …). We DO NOT touch the real file —
each test that needs a database uses the `tmp_db` fixture which
constructs a fully-initialised DB in tmp_path.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Make the repo root importable even when pytest runs from another
# working directory. This is tested by simply running `pytest` from
# anywhere inside the project tree.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


@pytest.fixture()
def tmp_db(tmp_path: Path):
    """Build a freshly-initialised Database against a temp file.

    Yields a ready-to-use `db.Database` instance. The underlying
    SQLite file is wiped along with `tmp_path` after the test.
    """
    from db import Database

    db_path = tmp_path / "tracker.db"
    d = Database(str(db_path))
    d.init_schema()
    yield d
    # Best-effort: SQLite WAL/shm sidecars hang around if a test
    # leaks an open connection; pytest's tmp_path tear-down will
    # eventually delete them, but this keeps the next test clean.
    for sidecar in (".db-wal", ".db-shm"):
        p = str(db_path) + sidecar
        if os.path.exists(p):
            try:
                os.unlink(p)
            except OSError:
                pass
