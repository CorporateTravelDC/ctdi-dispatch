-- Migration 0035: SCHEMA_V37 (src/common/db.py)
-- Auto-translated from the SQLite schema block of the same name
-- (docs/POSTGRES_MIGRATION.md Appendix A). Tables: watchlist_entries
-- Idempotent: CREATE TABLE/INDEX IF NOT EXISTS, ALTER ... ADD COLUMN IF NOT EXISTS.

ALTER TABLE watchlist_entries ADD COLUMN IF NOT EXISTS departure_delay_min INTEGER;
