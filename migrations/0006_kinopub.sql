-- 0006_kinopub.sql — kino.pub integration (PR 1).
--
-- Adds two related pieces of state:
--
-- 1) Per-item kino.pub identifiers, mirroring the existing rezka
--    columns (rezka_url, checked_rezka) so a future фоновой matcher
--    (sync_kinopub.py, PR 4) can populate them, and so the UI can
--    surface an "Open on kino.pub" badge alongside the existing
--    rezka one.
--
--    * kinopub_id      — numeric /v1/items/{id}
--    * kinopub_type    — movie / serial / 3D / concert / documovie / docuserial / tvshow
--    * kinopub_url     — https://kino.pub/item/<slug>; cached so the
--                        UI doesn't have to round-trip the API just to
--                        render a hyperlink.
--    * checked_kinopub — 0/1 flag; the sync_kinopub matcher uses the
--                        same checkpoint pattern as sync_video / rezka_sync
--                        (resume the sweep after a restart by selecting
--                        rows with checked_kinopub = 0).
--
-- 2) A one-row `kinopub_auth` table for the OAuth Device Flow
--    access/refresh tokens (PR 2). Kept minimal: a single global
--    account (the operator's kino.pub subscription), not per-user.
--    Tokens are stored in plaintext alongside `sessions.token_hash`
--    in the same DB file — protecting `app_data.db` itself is the
--    operator's responsibility.
--
--    `client_secret_sha256` is the hash of the client_secret that
--    was used to mint these tokens. If the operator rotates the
--    secret in env, runtime/kinopub.py will compare the hash and
--    re-trigger the Device Flow automatically rather than 401-ing
--    on every refresh attempt.

ALTER TABLE items ADD COLUMN kinopub_id INTEGER;
ALTER TABLE items ADD COLUMN kinopub_type TEXT;
ALTER TABLE items ADD COLUMN kinopub_url TEXT;
ALTER TABLE items ADD COLUMN checked_kinopub INTEGER NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_items_kinopub_id
    ON items(kinopub_id)
    WHERE kinopub_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS kinopub_auth (
    id                   INTEGER PRIMARY KEY CHECK (id = 1),
    access_token         TEXT NOT NULL,
    refresh_token        TEXT NOT NULL,
    expires_at           REAL NOT NULL,
    client_id            TEXT NOT NULL,
    client_secret_sha256 TEXT NOT NULL,
    updated_at           REAL NOT NULL
);
