-- legacy-sqlite-schema.sql -- structural record of the pre-Postgres SQLite database.
--
-- Captured 2026-09-23 from /var/lib/corporatetraveldc/corporatetraveldc.db (23 GB) before
-- that file is reclaimed. DDL ONLY: 78 tables, zero INSERT statements, zero credential
-- values -- verified at capture time. The live database has been Postgres exclusively
-- since the 2026-09-06 cutover (DISPATCH_DB_BACKEND=postgres, 65 pg_schema migrations).
--
-- WHY THIS EXISTS: architecture and documentation only. The SQLite file itself was NOT
-- committed and must never be -- it held live auth_tokens (22 rows), board_tokens (1)
-- and session_grants (3), and this repo is force-mirrored public. 23 GB of rows carries
-- no architectural information the 48 KB of DDL below does not.
--
-- Diff against src/common/pg_schema/*.sql to see how the model moved across the cutover.

CREATE TABLE feed_state (
    feed_name       TEXT PRIMARY KEY,
    fetched_at      REAL,           -- Unix timestamp
    error           TEXT,           -- NULL on success
    consecutive_failures INTEGER DEFAULT 0,
    payload_hash    TEXT            -- SHA-256 of raw payload (change detection)
);
CREATE TABLE tfrs (
    tfr_id          TEXT PRIMARY KEY,
    raw_json        TEXT NOT NULL,
    enriched_text   TEXT,           -- NULL until tfr-enrichment runs
    enriched_at     REAL,
    effective_start REAL,
    effective_end   REAL,
    is_vip          INTEGER DEFAULT 0,
    notified        INTEGER DEFAULT 0,
    inserted_at     REAL DEFAULT (unixepoch())
);
CREATE TABLE metar_snapshot (
    station         TEXT PRIMARY KEY,
    raw_metar       TEXT NOT NULL,
    ceiling_ft      INTEGER,
    visibility_sm   REAL,
    wind_kt         INTEGER,
    precip_code     TEXT,           -- RA / SN / TS / etc. — NULL if clear
    obs_time        REAL,
    fetched_at      REAL
);
CREATE TABLE nas_programs (
    program_id      TEXT PRIMARY KEY,
    type            TEXT,           -- GDP | GS | AAR
    facility        TEXT,
    raw_json        TEXT,
    active          INTEGER DEFAULT 1,
    fetched_at      REAL
, key_scheme INTEGER, legacy_correlate_id TEXT);
CREATE TABLE cps_scores (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    computed_at     REAL DEFAULT (unixepoch()),
    score           TEXT NOT NULL,  -- GREEN | YELLOW | RED
    label           TEXT NOT NULL,  -- GO | MARGINAL | NO-GO
    ceiling_factor  TEXT,
    visibility_factor TEXT,
    wind_factor     TEXT,
    precip_factor   TEXT,
    airspace_factor TEXT,
    gdp_factor      TEXT,
    narrative       TEXT
);
CREATE TABLE sqlite_sequence(name,seq);
CREATE TABLE hot_alerts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    computed_at     REAL DEFAULT (unixepoch()),
    route_narrative TEXT,
    active_tfrs     TEXT,           -- JSON array of TFR IDs
    vip_flags       TEXT,           -- JSON array of VIP callsigns matched
    source          TEXT DEFAULT 'route'  -- 'route' | 'flight' | 'train'
);
CREATE TABLE audit_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_time      REAL DEFAULT (unixepoch()),
    action          TEXT NOT NULL,
    tier            TEXT NOT NULL,
    token_prefix    TEXT,           -- First 8 chars of token (never full token)
    remote_addr     TEXT,
    detail          TEXT            -- JSON
, egress_status TEXT NOT NULL DEFAULT 'pending', egress_attempts INTEGER NOT NULL DEFAULT 0, egress_last_error TEXT);
CREATE TABLE auth_tokens (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    token_hash      TEXT UNIQUE NOT NULL,
    token_prefix    TEXT NOT NULL,  -- ctdc_<user>_ prefix for display
    user_label      TEXT NOT NULL,
    tier            TEXT NOT NULL,  -- cert | shares | admin
    device_label    TEXT,
    created_at      REAL DEFAULT (unixepoch()),
    expires_at      REAL,           -- NULL = no expiry
    revoked_at      REAL            -- NULL = active
, department TEXT);
CREATE TABLE trigger_log (
    id              TEXT PRIMARY KEY,   -- UUID
    trigger_type    TEXT NOT NULL,
    payload         TEXT,               -- JSON
    queued_at       REAL DEFAULT (unixepoch()),
    outcome         TEXT DEFAULT 'in_flight',   -- in_flight | success | failed
    resolved_at     REAL,
    error_msg       TEXT
);
CREATE TABLE notams (
    notam_id        TEXT PRIMARY KEY,
    raw_json        TEXT NOT NULL,
    facility        TEXT,
    classification  TEXT,           -- NOTAM-D, FDC, POINTER, etc.
    effective_start REAL,
    effective_end   REAL,
    text_body       TEXT,
    inserted_at     REAL DEFAULT (unixepoch())
, last_seen_at REAL DEFAULT NULL);
CREATE TABLE nws_alerts (
    alert_id        TEXT PRIMARY KEY,
    event_type      TEXT,           -- Winter Storm Warning, Tornado Watch, etc.
    area_desc       TEXT,
    severity        TEXT,           -- Extreme / Severe / Moderate / Minor
    certainty       TEXT,
    effective       REAL,
    expires         REAL,
    headline        TEXT,
    description     TEXT,
    fetched_at      REAL DEFAULT (unixepoch())
);
CREATE TABLE nws_forecast (
    zone            TEXT PRIMARY KEY,
    forecast_json   TEXT,
    fetched_at      REAL DEFAULT (unixepoch())
);
CREATE TABLE amtrak_status (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    fetched_at      REAL DEFAULT (unixepoch()),
    trains_json     TEXT,           -- JSON array of train status objects
    delay_summary   TEXT            -- Human-readable delay summary
);
CREATE TABLE ops_plan (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_date       TEXT,           -- YYYY-MM-DD
    raw_json        TEXT,           -- Full plan JSON
    trip_count      INTEGER,
    loaded_at       REAL DEFAULT (unixepoch())
);
CREATE TABLE runsheet (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date        TEXT NOT NULL,      -- YYYY-MM-DD
    scheduled_trips TEXT,               -- JSON array of trip objects
    trip_count      INTEGER DEFAULT 0,
    loaded_at       REAL DEFAULT (unixepoch())
);
CREATE TABLE watchlist_sessions (
    id              TEXT PRIMARY KEY,   -- UUID
    session_type    TEXT NOT NULL,      -- 'flight' | 'train' | 'custom'
    subject         TEXT NOT NULL,      -- Flight number, train ID, tail number, etc.
    run_date        TEXT NOT NULL,      -- YYYY-MM-DD — links to runsheet
    status          TEXT DEFAULT 'active',  -- 'active' | 'terminated'
    started_at      REAL DEFAULT (unixepoch()),
    terminated_at   REAL,
    session_data    TEXT,               -- JSON — accumulated poll results
    terminal_summary TEXT              -- Plain text summary written on termination
);
CREATE TABLE atcscc_opsplan (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_date       TEXT NOT NULL,      -- YYYY-MM-DD
    nas_programs    TEXT,               -- JSON — GDP/GS/AAR snapshot for the day
    notam_count     INTEGER DEFAULT 0,
    active_airports TEXT,               -- JSON array of affected airports
    pattern_tags    TEXT,               -- JSON array: ['weather-gdp','volume-delay',...]
    weather_summary TEXT,               -- Brief METAR summary at time of snapshot
    fetched_at      REAL DEFAULT (unixepoch())
);
CREATE UNIQUE INDEX idx_atcscc_opsplan_date
    ON atcscc_opsplan(plan_date);
CREATE TABLE ustrains_departures (
    train_id        TEXT NOT NULL,
    station_id      TEXT NOT NULL,
    destination     TEXT,
    scheduled       TEXT,           -- ISO-8601 departure time
    platform        TEXT,
    status          TEXT,           -- "On time", "15 min late", etc.
    fetched_at      REAL DEFAULT (unixepoch()),
    PRIMARY KEY (train_id, station_id)
);
CREATE TABLE flight_events (
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
    updated_at      REAL DEFAULT (unixepoch())
, squawk TEXT, registration TEXT, controlling_facility TEXT);
CREATE INDEX idx_flight_events_dest
    ON flight_events(destination);
CREATE INDEX idx_flight_events_origin
    ON flight_events(origin);
CREATE TABLE surface_tracks (
    track_id        TEXT NOT NULL,
    airport         TEXT NOT NULL,
    callsign        TEXT,
    squawk          TEXT,
    aircraft_type   TEXT,
    target_type     TEXT,
    latitude        REAL NOT NULL,
    longitude       REAL NOT NULL,
    altitude_ft     REAL,
    speed_kts       INTEGER,
    heading_deg     REAL,
    eram_gufi       TEXT,
    last_seen       TEXT NOT NULL,
    PRIMARY KEY (airport, track_id)
);
CREATE TABLE terminal_tracks (
    track_id        TEXT NOT NULL,
    facility        TEXT NOT NULL,
    callsign        TEXT,
    squawk          TEXT,
    mode_s          TEXT,
    latitude        REAL,
    longitude       REAL,
    altitude_ft     REAL,
    ground_speed    INTEGER,
    last_seen       TEXT NOT NULL,
    PRIMARY KEY (facility, track_id)
);
CREATE TABLE swim_alerts (
    alert_type      TEXT PRIMARY KEY,
    payload         TEXT,           -- JSON
    expires_at      TEXT NOT NULL   -- ISO 8601
);
CREATE TABLE watchlist_entries (
    id                  TEXT PRIMARY KEY,
    entry_type          TEXT NOT NULL,   -- "flight" | "train"
    tier                TEXT NOT NULL,   -- "permanent" | "transient"
    identifier          TEXT NOT NULL,
    origin              TEXT,
    destination         TEXT,
    route_name          TEXT,
    scheduled_departure TEXT,
    scheduled_arrival   TEXT,
    auto_remove_at      TEXT,            -- NULL for permanent
    added_at            TEXT NOT NULL,
    added_by            TEXT NOT NULL,
    notes               TEXT,
    last_event_at       TEXT,
    last_event_summary  TEXT
, oooi_phase TEXT, oooi_phase_updated_at TEXT, hex_id TEXT, registration TEXT, subsection TEXT, show_national INTEGER, show_regional INTEGER, days_active TEXT, sister_flight TEXT, last_fdps_status TEXT, last_fdps_updated_at TEXT, last_fids_status TEXT, last_fids_updated_at TEXT, departure_delay_min INTEGER, hex_source TEXT, hex_updated_at TEXT, hex_corroborated_at TEXT, oooi_source TEXT, last_tbfm_status TEXT, last_tbfm_updated_at TEXT, uas_phase TEXT, uas_phase_updated_at TEXT, uas_phase_source TEXT);
CREATE INDEX idx_watchlist_entries_type
    ON watchlist_entries(entry_type);
CREATE INDEX idx_watchlist_entries_ident
    ON watchlist_entries(identifier);
CREATE TABLE watchlist_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id        TEXT NOT NULL,
    entry_type      TEXT NOT NULL,
    identifier      TEXT NOT NULL,
    event_type      TEXT NOT NULL,
    event_summary   TEXT,
    event_detail    TEXT,           -- JSON
    fired_at        TEXT NOT NULL
, ntfy_fired   INTEGER DEFAULT 1, ntfy_priority INTEGER DEFAULT 3);
CREATE INDEX idx_watchlist_history_entry
    ON watchlist_history(entry_id);
CREATE TABLE tbfm_sequences (
    meter_fix       TEXT NOT NULL,
    facility        TEXT NOT NULL,
    flight_id       TEXT NOT NULL,
    eta             TEXT NOT NULL,          -- ISO 8601
    sequence_num    INTEGER,
    assigned_speed  INTEGER,
    last_seen       TEXT NOT NULL, eta_kind TEXT,
    PRIMARY KEY (meter_fix, flight_id)
);
CREATE TABLE itws_alerts (
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
CREATE TABLE local_aircraft (
    icao_hex        TEXT PRIMARY KEY,
    callsign        TEXT,
    registration    TEXT,
    aircraft_type   TEXT,
    operator        TEXT,
    latitude        REAL,
    longitude       REAL,
    altitude_ft     INTEGER,
    ground_speed    INTEGER,
    track_deg       REAL,
    squawk          TEXT,
    on_ground       INTEGER DEFAULT 0,
    rssi            REAL,
    distance_nm     REAL,
    last_seen       TEXT NOT NULL,
    first_seen      TEXT NOT NULL,
    source          TEXT DEFAULT 'ultrafeeder'
);
CREATE TABLE acars_messages (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    received_at         TEXT NOT NULL,
    freq_mhz            REAL,
    icao_hex            TEXT,
    tail                TEXT,
    flight              TEXT,
    msg_type            TEXT,
    label               TEXT,
    block_id            TEXT,
    ack                 TEXT,
    mode                TEXT,
    msg_text            TEXT,
    raw                 TEXT,
    watchlist_hit       INTEGER DEFAULT 0,
    watchlist_entry_id  TEXT
);
CREATE TABLE local_airspace_alerts (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    fired_at            TEXT NOT NULL,
    alert_type          TEXT NOT NULL,
    icao_hex            TEXT,
    callsign            TEXT,
    registration        TEXT,
    distance_nm         REAL,
    altitude_ft         INTEGER,
    squawk              TEXT,
    watchlist_entry_id  TEXT,
    payload             TEXT,
    ntfy_fired          INTEGER DEFAULT 0
);
CREATE TABLE brief_archive (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    generated_at TEXT NOT NULL,          -- ISO-8601 UTC
    brief_type   TEXT NOT NULL DEFAULT 'ops',  -- 'ops' | 'daily'
    content      TEXT NOT NULL,
    source       TEXT NOT NULL DEFAULT 'skill'  -- 'skill' | 'manual'
);
CREATE INDEX idx_brief_archive_ts ON brief_archive (generated_at DESC);
CREATE TABLE osint_scopes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    label           TEXT    NOT NULL,
    scope_type      TEXT    NOT NULL DEFAULT 'keyword',
    query_terms     TEXT    NOT NULL,
    feed_urls       TEXT    NOT NULL DEFAULT '',
    push_threshold  TEXT    NOT NULL DEFAULT 'HIGH',
    enabled         INTEGER NOT NULL DEFAULT 1,
    created_at      REAL    NOT NULL
, event_name TEXT, audience   TEXT, genre      TEXT);
CREATE TABLE osint_items (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    scope_id        INTEGER REFERENCES osint_scopes(id) ON DELETE CASCADE,
    title           TEXT    NOT NULL,
    url             TEXT    NOT NULL,
    source_name     TEXT,
    published_at    REAL,
    ingested_at     REAL    NOT NULL,
    score           INTEGER NOT NULL DEFAULT 0,
    score_label     TEXT    NOT NULL DEFAULT 'LOW',
    narrative       TEXT,
    pushed_at       REAL,
    content_hash    TEXT    UNIQUE NOT NULL
, headline  TEXT, outlet    TEXT, story_key TEXT);
CREATE INDEX idx_osint_items_scope
    ON osint_items(scope_id);
CREATE INDEX idx_osint_items_score
    ON osint_items(score DESC);
CREATE INDEX idx_osint_items_ingested
    ON osint_items(ingested_at DESC);
CREATE TABLE faa_aircraft_registry (
    n_number        TEXT    PRIMARY KEY,
    mode_s_hex      TEXT,
    serial_number   TEXT,
    mfr_mdl_code    TEXT,
    year_mfr        TEXT,
    registrant_name TEXT,
    city            TEXT,
    state           TEXT,
    status_code     TEXT,
    type_aircraft   TEXT,
    type_engine     TEXT,
    expiration_date TEXT,
    last_action_date TEXT,
    cert_issue_date TEXT,
    updated_at      REAL    NOT NULL
);
CREATE INDEX idx_faa_reg_hex
    ON faa_aircraft_registry(mode_s_hex);
CREATE INDEX idx_faa_reg_status
    ON faa_aircraft_registry(status_code);
CREATE TABLE faa_ladd_aircraft (
    n_number        TEXT    PRIMARY KEY,
    updated_at      REAL    NOT NULL
);
CREATE TABLE faa_registry_meta (
    key             TEXT    PRIMARY KEY,
    value           TEXT    NOT NULL
);
CREATE TABLE wpc_discussions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    awips_id        TEXT NOT NULL,
    product_label   TEXT NOT NULL,
    issued_at       REAL NOT NULL,
    fetched_at      REAL DEFAULT (unixepoch()),
    body            TEXT NOT NULL
);
CREATE INDEX idx_wpc_discussions_awips
    ON wpc_discussions(awips_id, issued_at DESC);
CREATE TABLE feed_data_usage (
    feed_name        TEXT PRIMARY KEY,
    bytes_in         INTEGER DEFAULT 0,   -- raw bytes from source (pre-filter)
    records_in       INTEGER DEFAULT 0,   -- messages/records received
    records_accepted INTEGER DEFAULT 0,   -- records that passed filter and were stored
    window_start     REAL,               -- unix epoch when window opened (reset on restart)
    updated_at       REAL DEFAULT (unixepoch())
);
CREATE TABLE webhook_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source          TEXT    NOT NULL,   -- 'limoanywhere' | 'ringcentral' | '3cx'
    event_type      TEXT    NOT NULL,
    external_ref    TEXT,               -- source's own event/reservation/call id
    payload         TEXT    NOT NULL,   -- raw JSON as received
    received_at     REAL    NOT NULL DEFAULT (unixepoch())
);
CREATE INDEX idx_webhook_events_source
    ON webhook_events(source, received_at);
CREATE INDEX idx_webhook_events_ref
    ON webhook_events(external_ref);
CREATE TABLE international_aviation_feed (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source          TEXT    NOT NULL,   -- 'eurocontrol' | 'jasdat'
    record_type     TEXT    NOT NULL,   -- 'notam' | 'sigmet' | 'flow_measure' | etc.
    external_ref    TEXT,
    raw_json        TEXT    NOT NULL,
    fetched_at      REAL    NOT NULL DEFAULT (unixepoch())
);
CREATE INDEX idx_intl_aviation_source
    ON international_aviation_feed(source, fetched_at);
CREATE TABLE opensky_aircraft_registry (
    icao24          TEXT    PRIMARY KEY,
    registration    TEXT,
    manufacturer_icao TEXT,
    manufacturer_name TEXT,
    model           TEXT,
    typecode        TEXT,
    serial_number   TEXT,
    icao_aircraft_type TEXT,
    operator        TEXT,
    operator_icao   TEXT,
    operator_iata   TEXT,
    owner           TEXT,
    registered      TEXT,
    reg_until       TEXT,
    status          TEXT,
    built           TEXT,
    updated_at      REAL    NOT NULL
);
CREATE INDEX idx_opensky_reg_registration
    ON opensky_aircraft_registry(registration);
CREATE TABLE opensky_registry_meta (
    key             TEXT    PRIMARY KEY,
    value           TEXT    NOT NULL
);
CREATE INDEX idx_watchlist_entries_hex ON watchlist_entries(hex_id);
CREATE TABLE bandwidth_priority_state (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    priority    TEXT NOT NULL DEFAULT 'auto',
    reason      TEXT,
    set_by      TEXT,
    set_at      REAL,
    expires_at  REAL
);
CREATE TABLE approval_requests (
    id               TEXT PRIMARY KEY,
    command_pattern  TEXT NOT NULL,
    command          TEXT NOT NULL,
    reasoning        TEXT,
    status           TEXT NOT NULL DEFAULT 'pending',
    created_at       REAL NOT NULL,
    resolved_at      REAL,
    expires_at       REAL NOT NULL
);
CREATE INDEX idx_approval_requests_pattern
    ON approval_requests(command_pattern, status, created_at);
CREATE INDEX idx_flight_events_callsign
    ON flight_events(airline, flight_num);
CREATE INDEX idx_flight_events_updated_at ON flight_events(updated_at);
CREATE TABLE codeshare_map (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    marketing_carrier TEXT NOT NULL,
    marketing_flight_num TEXT,
    operating_carrier TEXT,
    operating_flight_num TEXT,
    origin TEXT,
    destination TEXT,
    confidence INTEGER DEFAULT 1,
    source TEXT,
    first_seen_at TEXT,
    last_confirmed_at TEXT
);
CREATE INDEX idx_codeshare_marketing
    ON codeshare_map(marketing_carrier, marketing_flight_num);
CREATE INDEX idx_codeshare_operating
    ON codeshare_map(operating_carrier, operating_flight_num);
CREATE TABLE train_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    train_number    TEXT,
    train_name      TEXT,
    route_name      TEXT,
    direction       TEXT,
    scheduled_time  TEXT,
    estimated_time  TEXT,
    status          TEXT,
    delay_minutes   INTEGER,
    platform        TEXT,
    fetched_at      REAL DEFAULT (unixepoch())
, origin TEXT, destination TEXT, station_code TEXT, station_name TEXT);
CREATE INDEX idx_train_events_number
    ON train_events(train_number, fetched_at);
CREATE TABLE vessel_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    mmsi        TEXT,
    name        TEXT,
    lat         REAL,
    lon         REAL,
    sog         REAL,
    cog         REAL,
    hdg         REAL,
    nav_status  TEXT,
    ship_type   TEXT,
    source      TEXT,
    fetched_at  REAL DEFAULT (unixepoch())
, loa_m     REAL, beam_m    REAL, draught_m REAL);
CREATE INDEX idx_vessel_events_mmsi
    ON vessel_events(mmsi, fetched_at);
CREATE INDEX idx_train_events_number_station
    ON train_events(train_number, station_code, fetched_at);
CREATE TABLE faa_aircraft_reference (
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
    updated_at      REAL    NOT NULL
);
CREATE TABLE stdds_safety_status (
    airport         TEXT PRIMARY KEY,
    control         TEXT,
    status_bitmask  TEXT NOT NULL,
    last_seen       TEXT NOT NULL
);
CREATE TABLE surface_movement_events (
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
CREATE INDEX idx_surface_movement_events_status
    ON surface_movement_events(airport, status);
CREATE INDEX idx_audit_log_egress_status ON audit_log(egress_status);
CREATE TABLE stdds_safety_status_history (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    airport           TEXT NOT NULL,
    control           TEXT,
    previous_bitmask  TEXT,
    new_bitmask       TEXT NOT NULL,
    changed_at        TEXT NOT NULL
);
CREATE INDEX idx_stdds_safety_status_history_airport_time
    ON stdds_safety_status_history(airport, changed_at);
CREATE TABLE pull_path_status (
            feed_name   TEXT PRIMARY KEY,
            checked_at  REAL,
            ok          INTEGER,   -- 1 = pull path viable, 0 = failed
            state       TEXT,      -- verified|auth_gated|rate_limited|degraded|failed
            http_code   INTEGER,
            latency_ms  INTEGER,
            detail      TEXT
        );
CREATE TABLE board_messages (
            seq         INTEGER PRIMARY KEY AUTOINCREMENT,
            id          TEXT UNIQUE NOT NULL,
            ts          TEXT NOT NULL,        -- UTC ISO
            from_side   TEXT,
            to_side     TEXT,
            thread      TEXT NOT NULL,
            subject     TEXT,
            body        TEXT,
            refs        TEXT,                 -- JSON array
            in_reply_to TEXT,
            remote_addr TEXT
        );
CREATE INDEX idx_board_thread_seq ON board_messages(thread, seq);
CREATE TABLE board_enroll_nonces (
            nonce_hash        TEXT PRIMARY KEY,
            created_at        REAL,
            expires_at        REAL,
            consumed_at       REAL,
            minted_token_hash TEXT,
            label             TEXT
        );
CREATE TABLE board_tokens (
            token_hash  TEXT PRIMARY KEY,
            created_at  REAL,
            expires_at  REAL,
            scope       TEXT,
            label       TEXT,
            via_nonce   TEXT
        );
CREATE INDEX idx_osint_items_story ON osint_items(story_key);
CREATE TABLE board_presence (
            id                INTEGER PRIMARY KEY CHECK (id = 1),
            attestation_text  TEXT,
            issued_at         REAL,
            valid_until       REAL,
            key_fingerprint   TEXT,
            recorded_at       REAL
        );
CREATE TABLE flight_ooooi_times (
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
    updated_at          REAL DEFAULT (unixepoch())
);
CREATE INDEX idx_flight_ooooi_times_num ON flight_ooooi_times(airline, flight_num);
CREATE TABLE session_grants (
    id              TEXT PRIMARY KEY,
    command_pattern TEXT NOT NULL,
    granted_by      TEXT,
    reasoning       TEXT,
    scope           TEXT,
    granted_at      REAL,
    expires_at      REAL,
    revoked_at      REAL
);
CREATE INDEX idx_session_grants_pattern
    ON session_grants(command_pattern, expires_at);
CREATE INDEX idx_faa_registry_mode_s_hex_lower
    ON faa_aircraft_registry(LOWER(mode_s_hex));
CREATE INDEX idx_opensky_registry_icao24_lower
    ON opensky_aircraft_registry(LOWER(icao24));
CREATE INDEX idx_opensky_registry_registration_upper_nodash
    ON opensky_aircraft_registry(UPPER(REPLACE(registration, '-', '')));
CREATE TABLE stdds_rvr (
    airport                  TEXT NOT NULL,
    runway                   TEXT NOT NULL,
    touchdown_rvr_ft         INTEGER,
    touchdown_trend          TEXT,
    midpoint_rvr_ft          INTEGER,
    midpoint_trend           TEXT,
    rollout_rvr_ft           INTEGER,
    rollout_trend            TEXT,
    edge_light_setting       TEXT,
    centerline_light_setting TEXT,
    last_seen                TEXT NOT NULL,
    PRIMARY KEY (airport, runway)
);
CREATE TABLE tdes_departure_events (
    airport                  TEXT NOT NULL,
    callsign                 TEXT NOT NULL,
    event_time               TEXT NOT NULL,
    beacon_code              TEXT,
    aircraft_type            TEXT,
    computer_id              TEXT,
    clearance_delivery_time  TEXT,
    parking_gate             TEXT,
    eram_gufi                TEXT,
    sfdps_gufi               TEXT,
    destination_airport      TEXT,
    last_seen                TEXT NOT NULL,
    PRIMARY KEY (airport, callsign, event_time)
);
CREATE TABLE tdls_messages (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    airport              TEXT NOT NULL,
    callsign             TEXT,
    message_time         TEXT,
    beacon_code          TEXT,
    aircraft_type        TEXT,
    computer_id          TEXT,
    data_header          TEXT,
    data_body            TEXT,
    eram_gufi            TEXT,
    sfdps_gufi           TEXT,
    destination_airport  TEXT,
    received_at          TEXT NOT NULL
, dcl_type TEXT, response_type TEXT, registration TEXT, cleared_to TEXT, sid TEXT, sid_transition TEXT, expected_runway TEXT, climb_via_sid INTEGER, initial_altitude_ft INTEGER, cruise_fl TEXT, dep_frequency TEXT, proposed_dep_time TEXT, edct_time TEXT, route_text TEXT);
CREATE INDEX idx_tdls_messages_airport_received
    ON tdls_messages(airport, received_at);
CREATE INDEX idx_tdls_messages_callsign
    ON tdls_messages(callsign);
CREATE TABLE datis_snapshots (
    airport     TEXT PRIMARY KEY,
    atis_code   TEXT,
    edit_type   TEXT,
    datis_time  TEXT,
    body        TEXT,
    last_seen   TEXT NOT NULL
);
CREATE TABLE tfms_edct_slots (
    control_element              TEXT NOT NULL,
    aircraft_id                  TEXT NOT NULL,
    control_type                 TEXT,
    program_parameter            TEXT,
    delay_mode                   TEXT,
    departure_airport            TEXT,
    arrival_airport              TEXT,
    slot_time                    TEXT,
    controlled_departure_time    TEXT,
    controlled_arrival_time      TEXT,
    controlled_departure_iso     TEXT,
    exempt_flag                  INTEGER,
    cancel_flag                  INTEGER,
    slot_hold_flag               INTEGER,
    earliest_arrival_or_entry    TEXT,
    initial_gate_departure_time  TEXT,
    report_time                  TEXT,
    last_seen                    TEXT NOT NULL,
    PRIMARY KEY (control_element, aircraft_id)
);
CREATE TABLE fdps_destination_changes (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    flight_id        TEXT NOT NULL,
    callsign         TEXT,
    origin           TEXT,
    old_destination  TEXT NOT NULL,
    new_destination  TEXT NOT NULL,
    source           TEXT,
    detected_at      TEXT NOT NULL
);
CREATE INDEX idx_fdps_dest_changes_detected
    ON fdps_destination_changes(detected_at);
CREATE TABLE tfms_param_delay_stats (
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
CREATE TABLE tfms_reroutes (
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
CREATE INDEX idx_tfms_reroutes_status
    ON tfms_reroutes(reroute_status, last_seen);
CREATE TABLE fdps_diversion_continuations (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    change_id               INTEGER,
    diverted_flight_id      TEXT NOT NULL,
    continuation_flight_id  TEXT NOT NULL,
    callsign                TEXT,
    continuation_callsign   TEXT,
    match_basis             TEXT NOT NULL,
    registration            TEXT,
    origin                  TEXT,
    original_destination    TEXT NOT NULL,
    diversion_airport       TEXT NOT NULL,
    acars_msg_id            INTEGER,
    confidence              TEXT NOT NULL,
    diversion_detected_at   TEXT,
    detected_at             TEXT NOT NULL, operator_class TEXT,
    UNIQUE (diverted_flight_id, continuation_flight_id)
);
CREATE INDEX idx_fdps_div_cont_detected
    ON fdps_diversion_continuations(detected_at);
CREATE TABLE tfms_plan_removals (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    callsign          TEXT NOT NULL,
    igtd              TEXT NOT NULL DEFAULT '',
    carrier           TEXT,
    origin            TEXT,
    destination       TEXT,
    flight_ref        TEXT,
    removed_at        TEXT,
    removal_trigger   TEXT,
    kind              TEXT NOT NULL,
    source_facility   TEXT,
    filed_lead_h      REAL,
    origin_surveilled INTEGER NOT NULL DEFAULT 0,
    evidence          TEXT NOT NULL DEFAULT '{}',
    detected_at       TEXT NOT NULL,
    confirmed_at      TEXT,
    reinstated_at     TEXT,
    notified_at       TEXT,
    UNIQUE (callsign, igtd, origin, destination)
);
CREATE INDEX idx_tfms_plan_removals_detected
    ON tfms_plan_removals(detected_at);
CREATE INDEX idx_tfms_plan_removals_pending
    ON tfms_plan_removals(confirmed_at, reinstated_at, kind);
CREATE TABLE fdps_route_versions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    flight_id     TEXT NOT NULL,
    callsign      TEXT,
    origin        TEXT,
    destination   TEXT,
    route_text    TEXT NOT NULL,
    source        TEXT,
    version_num   INTEGER NOT NULL,
    change_class  TEXT,
    eta_first     TEXT,
    eta_last      TEXT,
    eta_delta_min REAL,
    first_seen    TEXT NOT NULL,
    last_seen     TEXT NOT NULL,
    times_seen    INTEGER NOT NULL DEFAULT 1,
    UNIQUE (flight_id, route_text)
);
CREATE INDEX idx_fdps_route_versions_flight
    ON fdps_route_versions(flight_id, version_num);
CREATE TABLE convective_sigmet_archive (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    sigmet_id        TEXT NOT NULL,
    airsigmet_type   TEXT,
    hazard           TEXT NOT NULL,
    severity         INTEGER,
    altitude_low_ft  INTEGER,
    altitude_high_ft INTEGER,
    issued_at        TEXT,
    valid_from       TEXT,
    valid_to         TEXT,
    movement_dir_deg INTEGER,
    movement_spd_kt  INTEGER,
    polygon          TEXT NOT NULL,
    raw_text         TEXT,
    first_seen       TEXT NOT NULL,
    UNIQUE (sigmet_id, valid_from)
);
CREATE INDEX idx_convective_sigmet_archive_window
    ON convective_sigmet_archive(valid_from, valid_to);
CREATE TABLE board_refresh_grace (
            old_token_hash    TEXT PRIMARY KEY,
            new_token         TEXT NOT NULL,
            new_expires_at    REAL NOT NULL,
            grace_expires_at  REAL NOT NULL
        );
CREATE TABLE stdds_rvr_history (
    id                        INTEGER PRIMARY KEY AUTOINCREMENT,
    airport                   TEXT NOT NULL,
    runway                    TEXT NOT NULL,
    touchdown_rvr_ft          INTEGER,
    touchdown_trend           TEXT,
    midpoint_rvr_ft           INTEGER,
    midpoint_trend            TEXT,
    rollout_rvr_ft            INTEGER,
    rollout_trend             TEXT,
    edge_light_setting        TEXT,
    centerline_light_setting  TEXT,
    recorded_at               TEXT NOT NULL
);
CREATE INDEX idx_stdds_rvr_history_airport_runway_time
    ON stdds_rvr_history(airport, runway, recorded_at);
CREATE TABLE metar_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    station         TEXT NOT NULL,
    raw_metar       TEXT NOT NULL,
    ceiling_ft      INTEGER,
    visibility_sm   REAL,
    wind_kt         INTEGER,
    wind_dir_deg    INTEGER,
    precip_code     TEXT,
    obs_time        REAL,
    recorded_at     REAL NOT NULL
);
CREATE INDEX idx_metar_history_station_time
    ON metar_history(station, recorded_at);
CREATE TABLE cifp_fixes (
    ident           TEXT    NOT NULL,
    icao_region     TEXT    NOT NULL,
    type            TEXT    NOT NULL,
    lat             REAL    NOT NULL,
    lon             REAL    NOT NULL,
    name            TEXT,
    parent_airport  TEXT,
    cycle           TEXT    NOT NULL,
    PRIMARY KEY (ident, icao_region)
);
CREATE INDEX idx_cifp_fixes_ident
    ON cifp_fixes(ident);
CREATE TABLE cifp_procedure_legs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    airport         TEXT    NOT NULL,
    proc_type       TEXT    NOT NULL,
    procedure       TEXT    NOT NULL,
    transition      TEXT    NOT NULL,
    seq             INTEGER NOT NULL,
    fix             TEXT,
    fix_region      TEXT,
    fix_type        TEXT,
    path_term       TEXT    NOT NULL,
    turn            TEXT,
    lat             REAL,
    lon             REAL,
    alt_desc        TEXT,
    alt1            TEXT,
    alt2            TEXT,
    speed_limit     TEXT,
    cycle           TEXT    NOT NULL
);
CREATE INDEX idx_cifp_legs_lookup
    ON cifp_procedure_legs(airport, proc_type, procedure, transition, seq);
CREATE TABLE cifp_holds (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    airport                 TEXT    NOT NULL,
    proc_type               TEXT    NOT NULL,
    procedure               TEXT    NOT NULL,
    transition              TEXT,
    seq                     INTEGER NOT NULL,
    fix                     TEXT    NOT NULL,
    fix_type                TEXT,
    lat                     REAL,
    lon                     REAL,
    hold_type               TEXT    NOT NULL,
    hold_meaning            TEXT    NOT NULL,
    turn_direction          TEXT,
    inbound_course_mag      REAL,
    leg_time_min            REAL,
    leg_dist_nm             REAL,
    alt_desc                TEXT,
    alt1                    TEXT,
    alt2                    TEXT,
    speed_limit             TEXT,
    cycle                   TEXT    NOT NULL
);
CREATE INDEX idx_cifp_holds_fix
    ON cifp_holds(fix);
CREATE INDEX idx_cifp_holds_airport
    ON cifp_holds(airport);
CREATE TABLE cifp_meta (
    key             TEXT    PRIMARY KEY,
    value           TEXT    NOT NULL
);
CREATE TABLE sqlite_stat1(tbl,idx,stat);
