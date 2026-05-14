"""Persistent bearer-token session store.

Before this module existed `routes/auth.py` held a process-local
`dict[token, expiry]`. Every restart of the app — including the ones
*triggered from the UI itself* (`/api/self_update`,
`/api/database_import`, `/api/reset_database`) — silently wiped that
dict and forced every logged-in user to log back in. This was the
single biggest "what just happened?" moment in the product.

The store deliberately hashes the bearer token before it touches
disk (SHA-256 hex). The token itself is generated with
`secrets.token_hex(32)` (256 bits of randomness, no structure), so
SHA-256 is enough — there's nothing to brute-force a 256-bit random
preimage. A leaked `app_data.db` therefore does NOT leak working
session cookies; an attacker would still need the original bearer
token.

The session row is the source of truth for both expiry and
sliding-TTL refresh. The auth path is roughly:

    expires_at = db.session_lookup(token, now)
    if expires_at is None: 401
    if now > expires_at: 401 (lookup also deletes the row)
    db.session_touch(token, now + TTL)    # sliding refresh
"""

from __future__ import annotations

import hashlib


def _hash_token(token: str) -> str:
    """SHA-256 hex digest of `token`. Stable across processes."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class DbSessionsMixin:
    def session_insert(self, token: str, *, expires_at: float, now: float, conn=None) -> None:
        """Create / replace a session row for `token`.

        `INSERT OR REPLACE` is used so a re-login from the same
        client gracefully replaces the previous row instead of
        raising a UNIQUE-violation; in practice we mint a fresh
        random token on every login so collisions don't happen.
        """
        token_hash = _hash_token(token)
        with self._conn(conn) as c:
            c.execute(
                "INSERT OR REPLACE INTO sessions "
                "(token_hash, expires_at, created_at, last_seen_at) "
                "VALUES (?, ?, ?, ?)",
                (token_hash, expires_at, now, now),
            )

    def session_lookup(self, token: str, *, now: float, conn=None) -> float | None:
        """Return `expires_at` if the token is known **and not expired**.

        Side-effect: an expired row is removed from the table so
        subsequent lookups don't pay the cost of re-checking it.
        """
        token_hash = _hash_token(token)
        with self._conn(conn) as c:
            row = c.execute(
                "SELECT expires_at FROM sessions WHERE token_hash = ?",
                (token_hash,),
            ).fetchone()
            if row is None:
                return None
            expires_at = float(row[0])
            if now > expires_at:
                c.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
                return None
            return expires_at

    def session_touch(self, token: str, *, expires_at: float, now: float, conn=None) -> None:
        """Push `expires_at` forward (sliding TTL) and record `last_seen_at`."""
        token_hash = _hash_token(token)
        with self._conn(conn) as c:
            c.execute(
                "UPDATE sessions SET expires_at = ?, last_seen_at = ? WHERE token_hash = ?",
                (expires_at, now, token_hash),
            )

    def session_delete(self, token: str, conn=None) -> None:
        """Remove the row for `token` if present. Used by /api/logout."""
        token_hash = _hash_token(token)
        with self._conn(conn) as c:
            c.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))

    def session_purge_expired(self, *, now: float, conn=None) -> int:
        """Drop every row whose `expires_at` is in the past. Returns
        the number of deleted rows so the GC loop can log it."""
        with self._conn(conn) as c:
            cur = c.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
            return int(cur.rowcount or 0)

    def session_count_active(self, *, now: float, conn=None) -> int:
        """Return the number of non-expired session rows. Useful for
        the /health-style debug surface."""
        with self._conn(conn) as c:
            row = c.execute(
                "SELECT COUNT(*) FROM sessions WHERE expires_at > ?",
                (now,),
            ).fetchone()
            return int(row[0]) if row else 0
