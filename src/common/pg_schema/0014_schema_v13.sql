-- Migration 0014: SCHEMA_V13 (src/common/db.py)
-- Auto-translated from the SQLite schema block of the same name
-- (docs/POSTGRES_MIGRATION.md Appendix A). Tables: notams
-- Idempotent: CREATE TABLE/INDEX IF NOT EXISTS, ALTER ... ADD COLUMN IF NOT EXISTS.

ALTER TABLE notams ADD COLUMN IF NOT EXISTS last_seen_at REAL DEFAULT NULL;
