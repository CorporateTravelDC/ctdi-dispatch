-- Migration 0029: SCHEMA_V31 (src/common/db.py)
-- Auto-translated from the SQLite schema block of the same name
-- (docs/POSTGRES_MIGRATION.md Appendix A). Tables: vessel_events
-- Idempotent: CREATE TABLE/INDEX IF NOT EXISTS, ALTER ... ADD COLUMN IF NOT EXISTS.

ALTER TABLE vessel_events ADD COLUMN IF NOT EXISTS loa_m     REAL;
ALTER TABLE vessel_events ADD COLUMN IF NOT EXISTS beam_m    REAL;
ALTER TABLE vessel_events ADD COLUMN IF NOT EXISTS draught_m REAL;
