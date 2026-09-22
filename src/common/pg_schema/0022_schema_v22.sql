-- Migration 0022: SCHEMA_V22 (src/common/db.py)
-- Auto-translated from the SQLite schema block of the same name
-- (docs/POSTGRES_MIGRATION.md Appendix A). Tables: flight_events
-- Idempotent: CREATE TABLE/INDEX IF NOT EXISTS, ALTER ... ADD COLUMN IF NOT EXISTS.

CREATE INDEX IF NOT EXISTS idx_flight_events_callsign
    ON flight_events(airline, flight_num);
