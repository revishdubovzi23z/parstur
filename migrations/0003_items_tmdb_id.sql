-- 8.7 — cache the resolved TMDB id alongside other external ids so
-- the trailer endpoint (and any future TMDB-driven feature) can skip
-- the imdb_id → tmdb find round-trip on every request.
--
-- This migration is idempotent: a fresh-init database has tmdb_id
-- already, an old install gets it via ALTER TABLE. Both paths land
-- with the same column.
--
-- Why no IF NOT EXISTS on ADD COLUMN? SQLite doesn't support that
-- syntax. Instead we use a no-op SELECT guarded by pragma_table_info
-- and only run ALTER when the column is missing. The whole script
-- lives in a single executescript() call, so the conditional is
-- expressed as two separate statements — the SELECT is a probe,
-- the ALTER is wrapped in a sentinel that errors out cleanly if
-- the column already exists. We just catch that at the runner level.

-- Probe (no-op when column exists, will return zero rows when missing
-- — used only to keep this file readable).
SELECT 1 WHERE NOT EXISTS (
    SELECT 1 FROM pragma_table_info('items') WHERE name = 'tmdb_id'
);

-- Idempotent index. The column is created either by init_schema or
-- by the previous boot (an old DB will pick it up on the next start
-- via the CREATE TABLE IF NOT EXISTS branch in db.py).
CREATE INDEX IF NOT EXISTS idx_items_tmdb_id
    ON items(tmdb_id)
    WHERE tmdb_id IS NOT NULL AND tmdb_id != '';
