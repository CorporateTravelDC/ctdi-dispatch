-- Migration 0041: SCHEMA_SWIM_V42 (src/common/db_swim.py)
-- Auto-translated from the SQLite schema block of the same name
-- (docs/POSTGRES_MIGRATION.md Appendix A). Tables: tdls_messages, tfms_param_delay_stats, tfms_reroutes
-- Idempotent: CREATE TABLE/INDEX IF NOT EXISTS, ALTER ... ADD COLUMN IF NOT EXISTS.

CREATE TABLE IF NOT EXISTS tfms_param_delay_stats (
    elem_name                TEXT NOT NULL,
    parameters_type          TEXT NOT NULL,
    tmi_state                TEXT NOT NULL,
    elem_type                TEXT,
    ctl_program              TEXT,
    event_start_time         TEXT,
    event_end_time           TEXT,
    cumulative_start_time    TEXT,
    cumulative_end_time      TEXT,
    impacting_condition_code TEXT,
    total_flights            INTEGER,
    affected_flights         INTEGER,
    total_delay_before_min   INTEGER,
    total_delay_after_min    INTEGER,
    max_delay_before_min     INTEGER,
    max_delay_after_min      INTEGER,
    avg_delay_before_min     REAL,
    avg_delay_after_min      REAL,
    delay_mode               TEXT,
    report_time              TEXT,
    last_seen                TEXT NOT NULL,
    PRIMARY KEY (elem_name, parameters_type, tmi_state)
);

CREATE TABLE IF NOT EXISTS tfms_reroutes (
    reroute_id        TEXT PRIMARY KEY,
    reroute_name      TEXT,
    reroute_status    TEXT,
    tmi_id            TEXT,
    tmi_status        TEXT,
    reroute_airborne  TEXT,
    time_type         TEXT,
    start_time        TEXT,
    end_time          TEXT,
    fca_name          TEXT,
    original_create_time TEXT,
    last_update_time  TEXT,
    segment_count     INTEGER,
    dc_relevant       INTEGER,
    segments_json     TEXT,
    last_seen         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tfms_reroutes_status
    ON tfms_reroutes(reroute_status, last_seen);

ALTER TABLE tdls_messages ADD COLUMN IF NOT EXISTS dcl_type TEXT;
ALTER TABLE tdls_messages ADD COLUMN IF NOT EXISTS response_type TEXT;
ALTER TABLE tdls_messages ADD COLUMN IF NOT EXISTS registration TEXT;
ALTER TABLE tdls_messages ADD COLUMN IF NOT EXISTS cleared_to TEXT;
ALTER TABLE tdls_messages ADD COLUMN IF NOT EXISTS sid TEXT;
ALTER TABLE tdls_messages ADD COLUMN IF NOT EXISTS sid_transition TEXT;
ALTER TABLE tdls_messages ADD COLUMN IF NOT EXISTS expected_runway TEXT;
ALTER TABLE tdls_messages ADD COLUMN IF NOT EXISTS climb_via_sid INTEGER;
ALTER TABLE tdls_messages ADD COLUMN IF NOT EXISTS initial_altitude_ft INTEGER;
ALTER TABLE tdls_messages ADD COLUMN IF NOT EXISTS cruise_fl TEXT;
ALTER TABLE tdls_messages ADD COLUMN IF NOT EXISTS dep_frequency TEXT;
ALTER TABLE tdls_messages ADD COLUMN IF NOT EXISTS proposed_dep_time TEXT;
ALTER TABLE tdls_messages ADD COLUMN IF NOT EXISTS edct_time TEXT;
ALTER TABLE tdls_messages ADD COLUMN IF NOT EXISTS route_text TEXT;
