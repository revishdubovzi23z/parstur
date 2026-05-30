-- 0009_cloud_sync_log.sql — Stage 13: cloud sync (Turso/libSQL) audit log.
--
-- Records every push/pull attempt so the UI can show "last synced"
-- timestamps and surface the most recent error without parsing the
-- application log.

CREATE TABLE IF NOT EXISTS cloud_sync_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    direction TEXT NOT NULL,   -- 'push' | 'pull'
    status TEXT NOT NULL,      -- 'success' | 'error'
    rows INTEGER DEFAULT 0,
    detail TEXT
);

PRAGMA user_version = 9;
