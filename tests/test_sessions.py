"""Tests for the persistent session store (db/sessions.py).

Contracts pinned:
- Tokens are hashed before they touch disk — the raw bearer token
  never appears in the table.
- session_lookup transparently drops an expired row.
- session_touch pushes expires_at forward.
- session_purge_expired removes everything <= the cutoff and
  reports the count.
- A round-trip across two Database instances (simulating a process
  restart) still recognises the token.
"""

from __future__ import annotations

from db.sessions import _hash_token


def test_hash_token_is_deterministic_and_hex() -> None:
    h1 = _hash_token("abc123")
    h2 = _hash_token("abc123")
    assert h1 == h2
    assert len(h1) == 64
    int(h1, 16)  # raises ValueError if not pure hex


def test_hash_token_differs_per_input() -> None:
    assert _hash_token("a") != _hash_token("b")


def test_insert_and_lookup_roundtrip(tmp_db) -> None:
    tok = "bearer-abc"
    tmp_db.session_insert(tok, expires_at=2000.0, now=1000.0)
    assert tmp_db.session_lookup(tok, now=1500.0) == 2000.0


def test_lookup_unknown_token_returns_none(tmp_db) -> None:
    assert tmp_db.session_lookup("nope", now=1000.0) is None


def test_lookup_expired_token_returns_none_and_deletes(tmp_db) -> None:
    tok = "bearer-expired"
    tmp_db.session_insert(tok, expires_at=1000.0, now=900.0)
    # now > expires_at -> expired
    assert tmp_db.session_lookup(tok, now=1500.0) is None
    # The row is gone — even a query before expiry would miss now.
    assert tmp_db.session_lookup(tok, now=500.0) is None


def test_touch_extends_expiry(tmp_db) -> None:
    tok = "bearer-touch"
    tmp_db.session_insert(tok, expires_at=2000.0, now=1000.0)
    tmp_db.session_touch(tok, expires_at=5000.0, now=1500.0)
    assert tmp_db.session_lookup(tok, now=4000.0) == 5000.0


def test_delete_removes_row(tmp_db) -> None:
    tok = "bearer-delete"
    tmp_db.session_insert(tok, expires_at=2000.0, now=1000.0)
    tmp_db.session_delete(tok)
    assert tmp_db.session_lookup(tok, now=1500.0) is None


def test_purge_expired_only_removes_past(tmp_db) -> None:
    tmp_db.session_insert("old1", expires_at=1000.0, now=500.0)
    tmp_db.session_insert("old2", expires_at=1500.0, now=500.0)
    tmp_db.session_insert("fresh", expires_at=5000.0, now=500.0)
    reaped = tmp_db.session_purge_expired(now=2000.0)
    assert reaped == 2
    assert tmp_db.session_lookup("fresh", now=2000.0) == 5000.0
    assert tmp_db.session_lookup("old1", now=2000.0) is None
    assert tmp_db.session_lookup("old2", now=2000.0) is None


def test_count_active(tmp_db) -> None:
    tmp_db.session_insert("a", expires_at=2000.0, now=500.0)
    tmp_db.session_insert("b", expires_at=3000.0, now=500.0)
    tmp_db.session_insert("c", expires_at=900.0, now=500.0)  # expired at now=1500
    assert tmp_db.session_count_active(now=1500.0) == 2
    assert tmp_db.session_count_active(now=4000.0) == 0


def test_raw_token_never_stored_in_table(tmp_db) -> None:
    """A leaked db file must NOT leak working bearer tokens."""
    tok = "super-secret-bearer-token"
    tmp_db.session_insert(tok, expires_at=2000.0, now=500.0)
    conn = tmp_db.get_connection()
    try:
        rows = conn.execute("SELECT token_hash FROM sessions").fetchall()
    finally:
        conn.close()
    stored = [r["token_hash"] for r in rows]
    assert tok not in stored
    assert _hash_token(tok) in stored


def test_session_survives_new_database_instance(tmp_path) -> None:
    """Simulates the restart scenario: process A inserts, process B
    (a fresh Database against the same file) still sees the token."""
    from db import Database

    db_path = tmp_path / "restart.db"
    a = Database(str(db_path))
    a.init_schema()
    a.session_insert("survivor", expires_at=9000.0, now=1000.0)

    b = Database(str(db_path))
    b.init_schema()
    assert b.session_lookup("survivor", now=2000.0) == 9000.0


def test_insert_replaces_existing_row(tmp_db) -> None:
    """If the same token is inserted twice (in practice impossible
    with token_hex(32), but the contract is INSERT OR REPLACE), the
    second call wins."""
    tmp_db.session_insert("dup", expires_at=1000.0, now=500.0)
    tmp_db.session_insert("dup", expires_at=9000.0, now=500.0)
    assert tmp_db.session_lookup("dup", now=2000.0) == 9000.0
