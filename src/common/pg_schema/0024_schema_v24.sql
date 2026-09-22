-- Migration 0024: SCHEMA_V24 (src/common/db.py)
-- Auto-translated from the SQLite schema block of the same name
-- (docs/POSTGRES_MIGRATION.md Appendix A). Tables: flight_events
-- Idempotent: CREATE TABLE/INDEX IF NOT EXISTS, ALTER ... ADD COLUMN IF NOT EXISTS.

CREATE INDEX IF NOT EXISTS idx_flight_events_updated_at
    ON flight_events(updated_at);
