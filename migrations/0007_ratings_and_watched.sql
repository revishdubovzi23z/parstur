-- 0007_ratings_and_watched.sql — User ratings, watched status and system collections.

ALTER TABLE items ADD COLUMN is_watched INTEGER DEFAULT 0;
ALTER TABLE items ADD COLUMN watched_at TEXT;

ALTER TABLE collections ADD COLUMN is_system INTEGER DEFAULT 0;

INSERT OR IGNORE INTO collections (name, sort_order, is_system)
VALUES ('Просмотренное', 9999, 1);

PRAGMA user_version = 7;
