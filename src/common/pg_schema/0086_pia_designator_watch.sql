-- 0086_pia_designator_watch.sql -- PIA call-sign prefixes kept current from the FAA
-- (2026-11-02, operator directive). poller/skills/pia_designator_watch.py _ensure()
-- is the SQLite twin (test suite only).
-- Tables: pia_callsign_prefixes, pia_watch_state
--
-- pia_callsign_prefixes: third-party call-sign designators added when the FAA ADS-B
-- Privacy page names a new PIA provider. db.PIA_CALLSIGN_PREFIXES (DCM, FFL, FWR, XAA)
-- is the floor in code; rows here only ever add to it. Nothing is deleted
-- automatically: a prefix missing from JO 7340.2 gets missing_since and an alert.
-- pia_watch_state: last seen JO 7340.2 edition signature and check times.
--
-- Idempotent: CREATE TABLE IF NOT EXISTS.

CREATE TABLE IF NOT EXISTS pia_callsign_prefixes (
    prefix        TEXT PRIMARY KEY,
    provider      TEXT,
    telephony     TEXT,
    source        TEXT NOT NULL,
    added_at      DOUBLE PRECISION NOT NULL,
    last_seen_at  DOUBLE PRECISION,
    missing_since DOUBLE PRECISION
);

CREATE TABLE IF NOT EXISTS pia_watch_state (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at DOUBLE PRECISION NOT NULL
);
