-- Migration 0007: SCHEMA_USAGE (src/common/db.py)
-- Auto-translated from the SQLite schema block of the same name
-- (docs/POSTGRES_MIGRATION.md Appendix A). Tables: feed_data_usage
-- Idempotent: CREATE TABLE/INDEX IF NOT EXISTS, ALTER ... ADD COLUMN IF NOT EXISTS.

CREATE TABLE IF NOT EXISTS feed_data_usage (
    feed_name        TEXT PRIMARY KEY,
    bytes_in         INTEGER DEFAULT 0,   -- raw bytes from source (pre-filter)
    records_in       INTEGER DEFAULT 0,   -- messages/records received
    records_accepted INTEGER DEFAULT 0,   -- records that passed filter and were stored
    window_start     REAL,               -- unix epoch when window opened (reset on restart)
    updated_at       REAL DEFAULT (extract(epoch from now()))
);
