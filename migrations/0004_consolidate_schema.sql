-- 9.1 — Consolidate schema changes that were previously applied by check_and_migrate_schema
--
-- This migration extracts all ALTER TABLE statements from Python code into pure SQL.
-- The migration runner (_apply_migrations) has been updated to execute these statements
-- one by one and to safely ignore "duplicate column name" errors. This guarantees idempotency
-- on existing databases that may already have these columns added by check_and_migrate_schema.

ALTER TABLE user_ratings ADD COLUMN original_title TEXT;
ALTER TABLE user_ratings ADD COLUMN title_norm TEXT;
ALTER TABLE user_ratings ADD COLUMN original_title_norm TEXT;
ALTER TABLE user_ratings ADD COLUMN imdb_id TEXT;
ALTER TABLE user_ratings ADD COLUMN kp_id TEXT;

ALTER TABLE items ADD COLUMN title_norm TEXT;
ALTER TABLE items ADD COLUMN latest_season INTEGER DEFAULT 0;
ALTER TABLE items ADD COLUMN latest_episode INTEGER DEFAULT 0;

ALTER TABLE releases ADD COLUMN rutor_id TEXT;
ALTER TABLE releases ADD COLUMN magnet TEXT;

ALTER TABLE collection_items ADD COLUMN added_at TEXT;
