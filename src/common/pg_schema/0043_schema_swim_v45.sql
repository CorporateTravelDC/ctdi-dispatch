-- Migration 0043: SCHEMA_SWIM_V45 (src/common/db_swim.py)
-- Auto-translated from the SQLite schema block of the same name
-- (docs/POSTGRES_MIGRATION.md Appendix A). Tables: fdps_diversion_continuations
-- Idempotent: CREATE TABLE/INDEX IF NOT EXISTS, ALTER ... ADD COLUMN IF NOT EXISTS.

ALTER TABLE fdps_diversion_continuations ADD COLUMN IF NOT EXISTS operator_class TEXT;
