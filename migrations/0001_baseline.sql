-- 0001_baseline.sql
--
-- No-op marker that pins existing installs to user_version = 1.
-- The actual baseline schema is created by Database.init_schema()
-- via CREATE TABLE IF NOT EXISTS — that path predates the
-- migration runner and continues to be the source of truth for a
-- fresh database.
--
-- Future migrations (0002+) should be additive deltas.

-- Intentionally empty.
SELECT 1;
