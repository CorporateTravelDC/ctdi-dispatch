-- Migration 0032: SCHEMA_V34 (src/common/db.py)
-- Auto-translated from the SQLite schema block of the same name
-- (docs/POSTGRES_MIGRATION.md Appendix A). Tables: flight_ooooi_times
-- Idempotent: CREATE TABLE/INDEX IF NOT EXISTS, ALTER ... ADD COLUMN IF NOT EXISTS.

CREATE TABLE IF NOT EXISTS flight_ooooi_times (
    gufi                TEXT PRIMARY KEY,
    callsign            TEXT,
    airline             TEXT,
    flight_num          TEXT,
    origin              TEXT,
    destination         TEXT,
    airline_out_time    TEXT,
    airline_off_time    TEXT,
    airline_on_time     TEXT,
    airline_in_time     TEXT,
    original_departure  TEXT,
    original_arrival    TEXT,
    flight_status       TEXT,
    updated_at          REAL DEFAULT (extract(epoch from now()))
);
CREATE INDEX IF NOT EXISTS idx_flight_ooooi_times_num ON flight_ooooi_times(airline, flight_num);
