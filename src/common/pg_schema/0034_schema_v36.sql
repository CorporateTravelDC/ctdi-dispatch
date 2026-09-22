-- Migration 0034: SCHEMA_V36 (src/common/db.py)
-- Auto-translated from the SQLite schema block of the same name
-- (docs/POSTGRES_MIGRATION.md Appendix A). Tables: nas_programs
-- Idempotent: CREATE TABLE/INDEX IF NOT EXISTS, ALTER ... ADD COLUMN IF NOT EXISTS.

ALTER TABLE nas_programs ADD COLUMN IF NOT EXISTS key_scheme INTEGER;
ALTER TABLE nas_programs ADD COLUMN IF NOT EXISTS legacy_correlate_id TEXT;
UPDATE nas_programs SET key_scheme = 1
    WHERE type IN ('GDP', 'GS') AND key_scheme IS NULL;
