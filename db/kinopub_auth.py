"""kino.pub OAuth token store.

One-row table `kinopub_auth` defined in migrations/0006_kinopub.sql.
The "one operator, one subscription" model mirrors the existing
single-user design (sessions, settings_db, …).

Tokens are stored in plaintext alongside `sessions.token_hash` —
protecting `app_data.db` itself is the operator's responsibility.

`client_secret_sha256` is the SHA-256 of the `client_secret` that was
used to mint the current token pair. If the operator rotates the
secret in env, `runtime/kinopub.py` compares the hash on every load
and triggers a re-auth instead of 401-ing on the next refresh.
"""

from __future__ import annotations

import hashlib


def _hash_secret(client_secret: str) -> str:
    return hashlib.sha256(client_secret.encode("utf-8")).hexdigest()


class DbKinopubAuthMixin:
    def kinopub_auth_get(self, conn=None) -> dict | None:
        """Return the current row as a dict, or None if not authenticated."""
        with self._conn(conn) as c:
            row = c.execute(
                "SELECT access_token, refresh_token, expires_at, "
                "       client_id, client_secret_sha256, updated_at "
                "FROM kinopub_auth WHERE id = 1"
            ).fetchone()
        if row is None:
            return None
        return {
            "access_token": str(row[0]),
            "refresh_token": str(row[1]),
            "expires_at": float(row[2]),
            "client_id": str(row[3]),
            "client_secret_sha256": str(row[4]),
            "updated_at": float(row[5]),
        }

    def kinopub_auth_set(
        self,
        *,
        access_token: str,
        refresh_token: str,
        expires_at: float,
        client_id: str,
        client_secret: str,
        now: float,
        conn=None,
    ) -> None:
        """Insert or replace the single auth row.

        Hashes `client_secret` before storing — the secret itself is
        never persisted (it's already on disk in `.env`, but storing
        it twice is gratuitous attack surface).
        """
        with self._conn(conn) as c:
            c.execute(
                "INSERT OR REPLACE INTO kinopub_auth "
                "(id, access_token, refresh_token, expires_at, "
                " client_id, client_secret_sha256, updated_at) "
                "VALUES (1, ?, ?, ?, ?, ?, ?)",
                (
                    access_token,
                    refresh_token,
                    expires_at,
                    client_id,
                    _hash_secret(client_secret),
                    now,
                ),
            )

    def kinopub_auth_clear(self, conn=None) -> None:
        """Remove the auth row. Used by /api/kinopub/logout and by
        runtime/kinopub.py when the refresh_token is rejected."""
        with self._conn(conn) as c:
            c.execute("DELETE FROM kinopub_auth WHERE id = 1")

    def kinopub_auth_secret_matches(self, client_secret: str, conn=None) -> bool:
        """Return True if the stored secret hash matches `client_secret`.

        Used at boot to detect operator-rotated credentials: if False,
        the stored tokens are unusable and runtime should drop them
        and re-trigger the Device Flow.
        """
        row = self.kinopub_auth_get(conn=conn)
        if row is None:
            return False
        return row["client_secret_sha256"] == _hash_secret(client_secret)
