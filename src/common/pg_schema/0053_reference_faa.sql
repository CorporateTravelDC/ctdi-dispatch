-- Migration 0053: FAA registry reference tables -- same reversal and
-- rationale as 0052's header (JOIN-driven, not a policy override).
-- faa_aircraft_registry/faa_aircraft_reference get hot-JOINed by
-- geometric reasoning's airframe/operator correlation against
-- flight_events.icao24 and opensky_aircraft_registry (0054).
-- Idempotent: CREATE TABLE/INDEX IF NOT EXISTS.

CREATE TABLE IF NOT EXISTS faa_aircraft_registry (
    n_number          TEXT    PRIMARY KEY,
    mode_s_hex        TEXT,
    serial_number     TEXT,
    mfr_mdl_code      TEXT,
    year_mfr          TEXT,
    registrant_name   TEXT,
    city              TEXT,
    state             TEXT,
    status_code       TEXT,
    type_aircraft     TEXT,
    type_engine       TEXT,
    expiration_date   TEXT,
    last_action_date  TEXT,
    cert_issue_date   TEXT,
    updated_at        DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_faa_reg_hex
    ON faa_aircraft_registry(mode_s_hex);
CREATE INDEX IF NOT EXISTS idx_faa_reg_status
    ON faa_aircraft_registry(status_code);
CREATE INDEX IF NOT EXISTS idx_faa_registry_mode_s_hex_lower
    ON faa_aircraft_registry(LOWER(mode_s_hex));

CREATE TABLE IF NOT EXISTS faa_aircraft_reference (
    code            TEXT    PRIMARY KEY,
    manufacturer    TEXT,
    model           TEXT,
    type_acft       TEXT,
    type_engine     TEXT,
    ac_category     TEXT,
    no_engines      TEXT,
    no_seats        TEXT,
    ac_weight       TEXT,
    speed           TEXT,
    updated_at      DOUBLE PRECISION NOT NULL
);

CREATE TABLE IF NOT EXISTS faa_registry_meta (
    key             TEXT    PRIMARY KEY,
    value           TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS faa_ladd_aircraft (
    n_number        TEXT    PRIMARY KEY,
    updated_at      DOUBLE PRECISION NOT NULL
);
