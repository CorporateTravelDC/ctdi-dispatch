-- Migration 0021: SCHEMA_V21 (src/common/db.py)
-- Auto-translated from the SQLite schema block of the same name
-- (docs/POSTGRES_MIGRATION.md Appendix A). Tables: approval_requests
-- Idempotent: CREATE TABLE/INDEX IF NOT EXISTS, ALTER ... ADD COLUMN IF NOT EXISTS.

CREATE TABLE IF NOT EXISTS approval_requests (
    id               TEXT PRIMARY KEY,
    command_pattern  TEXT NOT NULL,
    command          TEXT NOT NULL,
    reasoning        TEXT,
    status           TEXT NOT NULL DEFAULT 'pending',
    created_at       REAL NOT NULL,
    resolved_at      REAL,
    expires_at       REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_approval_requests_pattern
    ON approval_requests(command_pattern, status, created_at);
