-- Migration 0033: SCHEMA_V35 (src/common/db.py)
-- Auto-translated from the SQLite schema block of the same name
-- (docs/POSTGRES_MIGRATION.md Appendix A). Tables: session_grants
-- Idempotent: CREATE TABLE/INDEX IF NOT EXISTS, ALTER ... ADD COLUMN IF NOT EXISTS.

CREATE TABLE IF NOT EXISTS session_grants (
    id              TEXT PRIMARY KEY,
    command_pattern TEXT NOT NULL,
    granted_by      TEXT,
    reasoning       TEXT,
    scope           TEXT,
    granted_at      REAL,
    expires_at      REAL,
    revoked_at      REAL
);
CREATE INDEX IF NOT EXISTS idx_session_grants_pattern
    ON session_grants(command_pattern, expires_at);
