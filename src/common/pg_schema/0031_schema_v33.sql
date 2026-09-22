-- Migration 0031: SCHEMA_V33 (src/common/db.py)
-- Auto-translated from the SQLite schema block of the same name
-- (docs/POSTGRES_MIGRATION.md Appendix A). Tables: osint_items
-- Idempotent: CREATE TABLE/INDEX IF NOT EXISTS, ALTER ... ADD COLUMN IF NOT EXISTS.

ALTER TABLE osint_items ADD COLUMN IF NOT EXISTS headline  TEXT;
ALTER TABLE osint_items ADD COLUMN IF NOT EXISTS outlet    TEXT;
ALTER TABLE osint_items ADD COLUMN IF NOT EXISTS story_key TEXT;
CREATE INDEX IF NOT EXISTS idx_osint_items_story ON osint_items(story_key);
