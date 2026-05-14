"""Tests for db.kinopub_auth (PR 2 of the kino.pub integration).

Pins behaviour of the kinopub_auth one-row table helpers.
"""

from __future__ import annotations


def test_get_returns_none_when_empty(tmp_db) -> None:
    assert tmp_db.kinopub_auth_get() is None


def test_set_then_get_round_trip(tmp_db) -> None:
    tmp_db.kinopub_auth_set(
        access_token="AT",
        refresh_token="RT",
        expires_at=1_700_000_000.0,
        client_id="xbmc",
        client_secret="s3cr3t",
        now=1_699_999_000.0,
    )
    row = tmp_db.kinopub_auth_get()
    assert row is not None
    assert row["access_token"] == "AT"
    assert row["refresh_token"] == "RT"
    assert row["expires_at"] == 1_700_000_000.0
    assert row["client_id"] == "xbmc"
    # Secret is hashed, not stored in clear.
    assert row["client_secret_sha256"] != "s3cr3t"
    assert len(row["client_secret_sha256"]) == 64  # SHA-256 hex
    assert row["updated_at"] == 1_699_999_000.0


def test_set_is_idempotent_single_row(tmp_db) -> None:
    """Two consecutive set() calls must result in one row, not two."""
    tmp_db.kinopub_auth_set(
        access_token="AT1",
        refresh_token="RT1",
        expires_at=1_700_000_000.0,
        client_id="xbmc",
        client_secret="s",
        now=1.0,
    )
    tmp_db.kinopub_auth_set(
        access_token="AT2",
        refresh_token="RT2",
        expires_at=1_800_000_000.0,
        client_id="xbmc",
        client_secret="s",
        now=2.0,
    )
    # Single row stays at id=1, contents updated.
    with tmp_db._conn() as c:
        rows = c.execute("SELECT id, access_token FROM kinopub_auth").fetchall()
    assert len(rows) == 1
    assert rows[0][0] == 1
    assert rows[0][1] == "AT2"


def test_clear_removes_row(tmp_db) -> None:
    tmp_db.kinopub_auth_set(
        access_token="AT",
        refresh_token="RT",
        expires_at=1.0,
        client_id="xbmc",
        client_secret="s",
        now=0.0,
    )
    assert tmp_db.kinopub_auth_get() is not None
    tmp_db.kinopub_auth_clear()
    assert tmp_db.kinopub_auth_get() is None


def test_clear_is_idempotent_when_empty(tmp_db) -> None:
    """Calling clear() on a table that's already empty must not raise."""
    tmp_db.kinopub_auth_clear()
    tmp_db.kinopub_auth_clear()
    assert tmp_db.kinopub_auth_get() is None


def test_secret_matches_returns_false_when_empty(tmp_db) -> None:
    assert tmp_db.kinopub_auth_secret_matches("anything") is False


def test_secret_matches_true_for_same_secret(tmp_db) -> None:
    tmp_db.kinopub_auth_set(
        access_token="AT",
        refresh_token="RT",
        expires_at=1.0,
        client_id="xbmc",
        client_secret="the-secret",
        now=0.0,
    )
    assert tmp_db.kinopub_auth_secret_matches("the-secret") is True


def test_secret_matches_false_after_rotation(tmp_db) -> None:
    tmp_db.kinopub_auth_set(
        access_token="AT",
        refresh_token="RT",
        expires_at=1.0,
        client_id="xbmc",
        client_secret="old-secret",
        now=0.0,
    )
    # Operator rotated the secret in env.
    assert tmp_db.kinopub_auth_secret_matches("new-secret") is False
