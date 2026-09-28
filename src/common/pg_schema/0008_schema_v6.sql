-- Migration 0008: SCHEMA_V6 (src/common/db.py)
-- Auto-translated from the SQLite schema block of the same name
-- (docs/POSTGRES_MIGRATION.md Appendix A). Tables: itws_alerts, tbfm_sequences
-- Idempotent: CREATE TABLE/INDEX IF NOT EXISTS, ALTER ... ADD COLUMN IF NOT EXISTS.

CREATE TABLE IF NOT EXISTS tbfm_sequences (
    meter_fix       TEXT NOT NULL,
    facility        TEXT NOT NULL,
    flight_id       TEXT NOT NULL,
    eta             TEXT NOT NULL,          -- ISO 8601
    sequence_num    INTEGER,
    assigned_speed  INTEGER,
    last_seen       TEXT NOT NULL,
    PRIMARY KEY (meter_fix, flight_id)
);

CREATE TABLE IF NOT EXISTS itws_alerts (
    airport         TEXT NOT NULL,
    product_type    TEXT NOT NULL,          -- PRECIP | WIND_SHEAR | MICROBURST | LIGHTNING
    severity        INTEGER,                -- 1-6 scale; NULL if n/a
    detail          TEXT,
    valid_time      TEXT NOT NULL,
    expires_time    TEXT,
    raw_json        TEXT,
    last_seen       TEXT NOT NULL,
    PRIMARY KEY (airport, product_type)
);
