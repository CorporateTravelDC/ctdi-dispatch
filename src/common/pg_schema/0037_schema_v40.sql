-- Migration 0037: SCHEMA_V40 (src/common/db.py)
-- Auto-translated from the SQLite schema block of the same name
-- (docs/POSTGRES_MIGRATION.md Appendix A). Tables: watchlist_entries
-- Idempotent: CREATE TABLE/INDEX IF NOT EXISTS, ALTER ... ADD COLUMN IF NOT EXISTS.

ALTER TABLE watchlist_entries ADD COLUMN IF NOT EXISTS oooi_source TEXT;
ALTER TABLE watchlist_entries ADD COLUMN IF NOT EXISTS last_tbfm_status TEXT;
ALTER TABLE watchlist_entries ADD COLUMN IF NOT EXISTS last_tbfm_updated_at TEXT;
