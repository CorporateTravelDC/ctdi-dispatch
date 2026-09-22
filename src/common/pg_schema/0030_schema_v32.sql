-- Migration 0030: SCHEMA_V32 (src/common/db.py)
-- Auto-translated from the SQLite schema block of the same name
-- (docs/POSTGRES_MIGRATION.md Appendix A). Tables: osint_scopes
-- Idempotent: CREATE TABLE/INDEX IF NOT EXISTS, ALTER ... ADD COLUMN IF NOT EXISTS.

ALTER TABLE osint_scopes ADD COLUMN IF NOT EXISTS event_name TEXT;
ALTER TABLE osint_scopes ADD COLUMN IF NOT EXISTS audience   TEXT;
ALTER TABLE osint_scopes ADD COLUMN IF NOT EXISTS genre      TEXT;
