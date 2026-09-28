-- Migration 0005: SCHEMA_V4 (src/common/db.py)
-- Auto-translated from the SQLite schema block of the same name
-- (docs/POSTGRES_MIGRATION.md Appendix A). Tables: flight_events, ustrains_departures
-- Idempotent: CREATE TABLE/INDEX IF NOT EXISTS, ALTER ... ADD COLUMN IF NOT EXISTS.

-- US Train departures snapshot (findtrain.com / ustrains fetcher)
-- One row per train_id per fetch; latest fetch replaces previous rows.
CREATE TABLE IF NOT EXISTS ustrains_departures (
    train_id        TEXT NOT NULL,
    station_id      TEXT NOT NULL,
    destination     TEXT,
    scheduled       TEXT,           -- ISO-8601 departure time
    platform        TEXT,
    status          TEXT,           -- "On time", "15 min late", etc.
    fetched_at      REAL DEFAULT (extract(epoch from now())),
    PRIMARY KEY (train_id, station_id)
);

-- Flight events from FAA SWIM / SFDPS push feed
-- One row per flight (ACID). Updated in-place as push messages arrive.
-- Parser is a stub until a real SFDPS sample is captured.
CREATE TABLE IF NOT EXISTS flight_events (
    flight_id       TEXT PRIMARY KEY,   -- FAA ACID (e.g. AAL123)
    airline         TEXT,
    flight_num      TEXT,
    origin          TEXT,               -- ICAO
    destination     TEXT,               -- ICAO
    aircraft_type   TEXT,
    departure_time  REAL,               -- unix epoch
    arrival_time    REAL,               -- unix epoch (estimated)
    status          TEXT,               -- "active","landed","cancelled", etc.
    position_lat    REAL,
    position_lon    REAL,
    altitude_ft     INTEGER,
    ground_speed_kt INTEGER,
    raw_json        TEXT,
    updated_at      REAL DEFAULT (extract(epoch from now()))
);
CREATE INDEX IF NOT EXISTS idx_flight_events_dest
    ON flight_events(destination);
CREATE INDEX IF NOT EXISTS idx_flight_events_origin
    ON flight_events(origin);
