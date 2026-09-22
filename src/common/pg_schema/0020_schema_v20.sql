-- Migration 0020: SCHEMA_V20 (src/common/db.py)
-- Auto-translated from the SQLite schema block of the same name
-- (docs/POSTGRES_MIGRATION.md Appendix A). Tables: bandwidth_priority_state
-- Idempotent: CREATE TABLE/INDEX IF NOT EXISTS, ALTER ... ADD COLUMN IF NOT EXISTS.

CREATE TABLE IF NOT EXISTS bandwidth_priority_state (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    priority    TEXT NOT NULL DEFAULT 'auto',
    reason      TEXT,
    set_by      TEXT,
    set_at      REAL,
    expires_at  REAL
);
