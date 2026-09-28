-- Migration 0026: SCHEMA_V28 (src/common/db.py)
-- Auto-translated from the SQLite schema block of the same name
-- (docs/POSTGRES_MIGRATION.md Appendix A). Tables: stdds_safety_status, surface_movement_events
-- Idempotent: CREATE TABLE/INDEX IF NOT EXISTS, ALTER ... ADD COLUMN IF NOT EXISTS.

CREATE TABLE IF NOT EXISTS stdds_safety_status (
    airport         TEXT PRIMARY KEY,
    control         TEXT,
    status_bitmask  TEXT NOT NULL,
    last_seen       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS surface_movement_events (
    track_id             TEXT NOT NULL,
    airport              TEXT NOT NULL,
    callsign             TEXT,
    event                TEXT,
    status               TEXT,
    runway               TEXT,
    latitude             REAL,
    longitude            REAL,
    altitude_ft          REAL,
    event_time           TEXT,
    departure_airport    TEXT,
    destination_airport  TEXT,
    last_seen            TEXT NOT NULL,
    PRIMARY KEY (airport, track_id)
);
CREATE INDEX IF NOT EXISTS idx_surface_movement_events_status
    ON surface_movement_events(airport, status);
