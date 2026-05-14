-- 0005_sessions.sql — persistent session store.
--
-- Before this migration `routes/auth.py` kept the bearer-token →
-- expiry map in a process-local Python dict. That meant every
-- restart (regular reboot, `/api/self_update`, `/api/database_import`,
-- `/api/reset_database`) silently invalidated every logged-in user
-- and forced a fresh login. With this table the session survives
-- restarts.
--
-- Security notes:
--   * We never store the raw bearer token. The column is a SHA-256
--     hex digest — a compromised DB file does NOT leak working
--     session cookies.
--   * `expires_at` is a Unix epoch (seconds); we use a sliding TTL
--     so every successful `_check_token` pushes it forward.
--   * `created_at` / `last_seen_at` are kept for future audit /
--     "active sessions" UI; not consulted by the auth path itself.
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    expires_at REAL NOT NULL,
    created_at REAL NOT NULL,
    last_seen_at REAL NOT NULL
);

-- The GC loop deletes everything whose expiry is in the past — an
-- index on expires_at keeps that O(log n) instead of a full scan
-- once the table accumulates historical rows.
CREATE INDEX IF NOT EXISTS idx_sessions_expires_at ON sessions(expires_at);
