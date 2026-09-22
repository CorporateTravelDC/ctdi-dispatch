-- Migration 0023: SCHEMA_V23 (src/common/db.py)
-- Auto-translated from the SQLite schema block of the same name
-- (docs/POSTGRES_MIGRATION.md Appendix A). Tables: watchlist_entries
-- Idempotent: CREATE TABLE/INDEX IF NOT EXISTS, ALTER ... ADD COLUMN IF NOT EXISTS.

ALTER TABLE watchlist_entries ADD COLUMN IF NOT EXISTS last_fdps_status TEXT;
ALTER TABLE watchlist_entries ADD COLUMN IF NOT EXISTS last_fdps_updated_at TEXT;
ALTER TABLE watchlist_entries ADD COLUMN IF NOT EXISTS last_fids_status TEXT;
ALTER TABLE watchlist_entries ADD COLUMN IF NOT EXISTS last_fids_updated_at TEXT;
