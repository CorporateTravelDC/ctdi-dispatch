-- Migration 0010: SCHEMA_V8 (src/common/db.py)
-- Auto-translated from the SQLite schema block of the same name
-- (docs/POSTGRES_MIGRATION.md Appendix A). Tables: watchlist_history
-- Idempotent: CREATE TABLE/INDEX IF NOT EXISTS, ALTER ... ADD COLUMN IF NOT EXISTS.

ALTER TABLE watchlist_history ADD COLUMN IF NOT EXISTS ntfy_fired   INTEGER DEFAULT 1;
ALTER TABLE watchlist_history ADD COLUMN IF NOT EXISTS ntfy_priority INTEGER DEFAULT 3;
