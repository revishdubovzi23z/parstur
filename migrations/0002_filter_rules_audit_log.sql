-- 8.5 + 8.18 — filter rules and audit log.
--
-- filter_rules: regex/ruleset definitions applied in get_feed.
--   field   — items column the rule reads (title / original_title / description).
--   action  — 'hide' (item dropped from feed) or 'highlight' (item decorated
--             with matched_rules in the response).
--   enabled — 0/1 toggle so a rule can be parked without losing the pattern.
--
-- audit_log: append-only history of mutations the user can undo.
--   action    — 'edit_field', 'reset_metadata', 'delete', etc.
--   field     — items column changed (NULL when action is row-level).
--   old_value — JSON-serialised prior value (used by replay/undo).
--   new_value — JSON-serialised new value (informational).
--   undone    — 0/1; an undone entry stays in the table but is excluded
--               from further undo passes.

CREATE TABLE IF NOT EXISTS filter_rules (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    field      TEXT NOT NULL,
    pattern    TEXT NOT NULL,
    action     TEXT NOT NULL,
    enabled    INTEGER NOT NULL DEFAULT 1,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS audit_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    action     TEXT NOT NULL,
    item_id    INTEGER,
    field      TEXT,
    old_value  TEXT,
    new_value  TEXT,
    undone     INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_audit_log_created
    ON audit_log(created_at DESC);

CREATE INDEX IF NOT EXISTS idx_audit_log_item
    ON audit_log(item_id);
