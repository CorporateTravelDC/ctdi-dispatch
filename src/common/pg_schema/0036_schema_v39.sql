-- Migration 0036: SCHEMA_V39 (src/common/db.py)
-- Auto-translated from the SQLite schema block of the same name
-- (docs/POSTGRES_MIGRATION.md Appendix A). Tables: watchlist_entries
-- Idempotent: CREATE TABLE/INDEX IF NOT EXISTS, ALTER ... ADD COLUMN IF NOT EXISTS.

ALTER TABLE watchlist_entries ADD COLUMN IF NOT EXISTS hex_source TEXT;
ALTER TABLE watchlist_entries ADD COLUMN IF NOT EXISTS hex_updated_at TEXT;
ALTER TABLE watchlist_entries ADD COLUMN IF NOT EXISTS hex_corroborated_at TEXT;
