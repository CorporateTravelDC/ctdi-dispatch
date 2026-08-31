"""
common.db_swim -- SCHEMA_V41 "unread SWIM fields" tables + accessors.

Added 2026-08-30 during the SWIM ingest audit (external "unread SWIM
fields" document + an independent blind sweep of every parser against the
real captured samples under /var/lib/corporatetraveldc/*_debug*).

WHY A SEPARATE MODULE instead of the usual SCHEMA_Vn block in
common/db.py: db.py also carries the deliberately-hardened OOOI-authority
and watchlist-identity logic (_OOOI_SOURCE_PRIORITY /
update_watchlist_oooi_phase_authoritative / hex-corroboration), which the
2026-08-30 audit was explicitly barred from touching while a parallel
workstream was actively editing that file. Keeping this pass's purely
additive schema in its own module makes "this change cannot have touched
that logic" checkable from the diff alone. An operator merge of this
module's contents into db.py as a numbered SCHEMA_Vn later is fine and
expected -- nothing here depends on living in a separate file, and
init_db_swim_v41() is idempotent either way. NOTE: because this is not a
db.py SCHEMA_Vn member, db.init_db_all()'s introspection does NOT pick it
up -- callers that need these tables (ingest/main.py, tests) must call
init_db_swim_v41() explicitly.

Every table stores data CONFIRMED -- against real captured messages on
this box, not the external document's claims -- to already be arriving on
an existing SWIM queue and previously dropped by a handler:

  stdds_rvr                -- APDS RVRDataUpdateMessage on the STDDS queue
                              (smes_debug/RVRDataUpdateMessage_*.xml).
                              Per-runway touchdown/midpoint/rollout RVR.
  tdes_departure_events    -- TDES TowerDepartureEventMessage: parking gate
                              + clearance-delivery time per departure
                              (smes_debug/TowerDepartureEventMessage_*).
  tdls_messages            -- TDES TDLSCSPMessage (PDC/CPDLC/dispatch
                              text). Envelope + raw body only; body-text
                              regex parsing (EDCT/route extraction) is a
                              deliberate non-goal for this pass.
  datis_snapshots          -- TDES DATISData (digital ATIS text; carries
                              the active runway configuration in prose).
  tfms_edct_slots          -- TFMS FADT fadtBcast slot lists: per-flight
                              controlled departure/arrival times (EDCTs)
                              for GS/GDP programs
                              (tfms_debug_unknown_msgtype/FADT_*.xml).
  fdps_destination_changes -- append-only log of a filed plan's
                              destination changing for the same GUFI
                              (diversion / re-file signal; groundwork for
                              alternate-saturation analysis). Storage
                              only, no alert -- watched flights already
                              get a corroborated diversion alert via
                              poller/main.py's ADS-B + FDPS cross-check.

flight_events gains squawk/registration/controlling_facility -- all three
were already parsed by fdps_parser._parse_fdps_message_fixm30 and then
dropped at write time (registration was explicitly flagged in that file's
own docstring as a wanted schema addition since 2026-07-20). They are
written via update_flight_event_extras() below (a follow-up UPDATE with
COALESCE keep-last-known semantics) rather than by widening db.py's
upsert_flight_event(), for the same don't-touch-db.py reason.
"""
from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timedelta

# Reuse db.py's connection factory (WAL settings, row factory, busy
# timeout) so every write here participates in the exact same SQLite
# discipline as the rest of the platform -- importing the function, not
# copying it, so a future tuning change there applies here automatically.
from common.db import conn

SCHEMA_SWIM_V41 = """
CREATE TABLE IF NOT EXISTS stdds_rvr (
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

CREATE TABLE IF NOT EXISTS tdes_departure_events (
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

CREATE TABLE IF NOT EXISTS tdls_messages (
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
);
CREATE INDEX IF NOT EXISTS idx_tdls_messages_airport_received
    ON tdls_messages(airport, received_at);
CREATE INDEX IF NOT EXISTS idx_tdls_messages_callsign
    ON tdls_messages(callsign);

CREATE TABLE IF NOT EXISTS datis_snapshots (
    airport     TEXT PRIMARY KEY,
    atis_code   TEXT,
    edit_type   TEXT,
    datis_time  TEXT,
    body        TEXT,
    last_seen   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tfms_edct_slots (
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

CREATE TABLE IF NOT EXISTS fdps_destination_changes (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    flight_id        TEXT NOT NULL,
    callsign         TEXT,
    origin           TEXT,
    old_destination  TEXT NOT NULL,
    new_destination  TEXT NOT NULL,
    source           TEXT,
    detected_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fdps_dest_changes_detected
    ON fdps_destination_changes(detected_at);

ALTER TABLE flight_events ADD COLUMN squawk TEXT;
ALTER TABLE flight_events ADD COLUMN registration TEXT;
ALTER TABLE flight_events ADD COLUMN controlling_facility TEXT;
"""


# ── SCHEMA_SWIM_V42 -- 2026-08-30 afternoon pass (audit backlog items) ───────
#
# Same separate-module rationale as V41 above. Adds:
#
#   tfms_param_delay_stats -- TFMS PARAM messages (paramGsUpdt /
#       paramAfpGdpUpdt): TFMS's own modeled delay statistics for an
#       active/proposed GS/GDP/AFP -- total/affected flight counts and
#       min/max/avg delay before+after the current revision. 15 real
#       captures (tfms_debug_unknown_msgtype/PARAM_*.xml) confirm both
#       variant tags share every field stored here. This is the "how bad
#       is this program actually" quantification the bare nas_programs
#       declaration row lacks.
#   tfms_reroutes -- TFMS REROUTE advisory general data (rerouteGeneralData
#       + a waypoint-free per-segment summary). The full rerouteSegmentData
#       waypoint lists (up to 264 waypoints/segment observed) are
#       deliberately NOT decoded -- advisory-level state (what is rerouted,
#       from/to where, when, ACTIVE/CANCELLED) is the dispatch signal;
#       lat/lon plotting is not (yet) a consumer here.
#   tdls_messages parsed columns -- regex-extracted PDC/CPDLC-DCL fields
#       (SID/transition/expected runway/altitudes/frequency/EDCT), all
#       nullable, populated by smes_parser.parse_tdls_dcl_body() from the
#       raw data_body which remains stored verbatim alongside -- every
#       parse is re-derivable from the raw text.

SCHEMA_SWIM_V42 = """
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

ALTER TABLE tdls_messages ADD COLUMN dcl_type TEXT;
ALTER TABLE tdls_messages ADD COLUMN response_type TEXT;
ALTER TABLE tdls_messages ADD COLUMN registration TEXT;
ALTER TABLE tdls_messages ADD COLUMN cleared_to TEXT;
ALTER TABLE tdls_messages ADD COLUMN sid TEXT;
ALTER TABLE tdls_messages ADD COLUMN sid_transition TEXT;
ALTER TABLE tdls_messages ADD COLUMN expected_runway TEXT;
ALTER TABLE tdls_messages ADD COLUMN climb_via_sid INTEGER;
ALTER TABLE tdls_messages ADD COLUMN initial_altitude_ft INTEGER;
ALTER TABLE tdls_messages ADD COLUMN cruise_fl TEXT;
ALTER TABLE tdls_messages ADD COLUMN dep_frequency TEXT;
ALTER TABLE tdls_messages ADD COLUMN proposed_dep_time TEXT;
ALTER TABLE tdls_messages ADD COLUMN edct_time TEXT;
ALTER TABLE tdls_messages ADD COLUMN route_text TEXT;
"""


# ── SCHEMA_SWIM_V44 -- 2026-08-30 night pass (backlog: continuation pairs) ───
#
# (v43 is db.py's uas_phase columns, added the same pass -- the shared
# number line continues across both modules, same as v41 following db.py's
# v40.) One table:
#
#   fdps_diversion_continuations -- confirmed diversion-continuation
#       pairs: a flight in fdps_destination_changes changed destination
#       B -> C (a diversion), and a LATER, DIFFERENT-GUFI filing by the
#       same callsign (or same registration) files C -> B -- the leg that
#       only exists because of the diversion. Detected inline by
#       fdps_parser._check_diversion_continuation() on each first-sighting
#       FH/AH filing. `confidence` records whether a corroborating ACARS
#       message (acars_messages, same registration, route/destination-
#       consistent text in the window) was found -- a BONUS signal, never
#       a requirement (operator refinement: most aircraft aren't
#       ACARS-equipped; the FDPS follow-on filing alone is valid).
#       UNIQUE(diverted_flight_id, continuation_flight_id) makes the
#       insert itself the once-only gate for the pair's single alert.

SCHEMA_SWIM_V44 = """
CREATE TABLE IF NOT EXISTS fdps_diversion_continuations (
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
    detected_at             TEXT NOT NULL,
    UNIQUE (diverted_flight_id, continuation_flight_id)
);
CREATE INDEX IF NOT EXISTS idx_fdps_div_cont_detected
    ON fdps_diversion_continuations(detected_at);
"""


# ── SCHEMA_SWIM_V45 -- 2026-08-30 late pass (operator-class gate) ────────────
#
# One nullable column: fdps_diversion_continuations.operator_class --
# 'scheduled' | 'fractional' | 'ga_tail' as classified by
# fdps_parser._operator_class() at detection time. Per the external SWIM
# diversion-detection document, ~85% of raw continuation-shaped candidates
# nationwide are fractional/charter (airline-shaped EJA/LXJ/... callsigns)
# or tail-number GA running normal multi-leg trips; those pairs are still
# STORED (a tech stop is a real fact about an airframe) but only
# 'scheduled' pairs may fire the fdps_diversion_continuation alert.
# NULL = row predates this column (no live rows existed when it shipped).

SCHEMA_SWIM_V45 = """
ALTER TABLE fdps_diversion_continuations ADD COLUMN operator_class TEXT;
"""


# ── SCHEMA_SWIM_V46 -- 2026-08-30 late-night pass (Detectors C & D) ──────────
#
# Two tables, both driven by data CONFIRMED already flowing on this box
# (same discipline as v41-v45 -- nothing here is built against a guessed
# shape):
#
#   tfms_plan_removals -- every TFMS fltdMessage
#       msgType="flightPlanCancellation" (Detector C's raw material).
#       Until this pass the handler only ever fired a watchlist alert for
#       watched callsigns and DROPPED the message for everyone else --
#       fdTrigger was extracted into an alert detail dict and never
#       stored, so no fly-rate-per-trigger measurement was possible.
#       There is NO cancellation message in the NAS, only plan removal
#       for several reasons (fdTrigger); `kind` records the closed-
#       vocabulary classification (see tfms_parser._REMOVAL_TRIGGER_KINDS
#       for the vocabulary and its external-document provenance) and
#       `evidence` accumulates the did-it-actually-fly observations
#       (departure/track/arrival messages, replan/reinstatement) that
#       let US measure OUR OWN flew-anyway rate per trigger instead of
#       trusting the reference system's percentages. The UNIQUE key is a
#       LEG key (callsign+igtd+airport pair), never flightRef -- the
#       external document's own measurement inverted entirely when keyed
#       per plan reference, because one leg can mint several plan refs.
#       igtd is normalized to '' when absent so the UNIQUE key stays
#       usable (SQLite treats NULLs as distinct in UNIQUE constraints).
#       Rows are national scope (no DC geo-gate): the per-airport
#       cancellation-cluster detection this feeds needs cross-airport
#       baselines, and volume is bounded (reference system: ~8.5k
#       removals/day nationwide). NO prune job covers this table yet --
#       flagged in the audit notes, same open item as
#       fdps_destination_changes.
#
#   fdps_route_versions -- incremental table of DISTINCT
#       (flight_id, route_text) pairs with first/last seen (Detector D's
#       foundational structure, prescribed by the external document
#       precisely because re-scanning raw message history for
#       multi-route-version flights does not scale). route_text is FIXM
#       3.0 `agreed > route/@nasRouteText` -- confirmed in real captures
#       (fdps_debug_fixm30/sample_4.xml + sample_6.xml: the SAME GUFI
#       carrying "KDCA.CLTCH3.MAULS..." and then "KDCA./.MAULS...", a
#       real re-expression pair) and, until this pass, never extracted by
#       the parser at all. Writes ride INSIDE write_flight_event's
#       DC-area geo gate, so growth is bounded to relevant traffic.
#       change_class is fdps_parser._classify_route_change()'s verdict
#       vs the previous distinct version (NULL on each flight's first
#       version); eta_delta_min is the arrival-estimate movement vs the
#       previous version where both carried one -- the document's "rank
#       by the tail, never alert on occurrence" cost signal. No alert
#       fires from this table in this pass (storage/classification only).

SCHEMA_SWIM_V46 = """
CREATE TABLE IF NOT EXISTS tfms_plan_removals (
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
CREATE INDEX IF NOT EXISTS idx_tfms_plan_removals_detected
    ON tfms_plan_removals(detected_at);
CREATE INDEX IF NOT EXISTS idx_tfms_plan_removals_pending
    ON tfms_plan_removals(confirmed_at, reinstated_at, kind);

CREATE TABLE IF NOT EXISTS fdps_route_versions (
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
CREATE INDEX IF NOT EXISTS idx_fdps_route_versions_flight
    ON fdps_route_versions(flight_id, version_num);
"""


# ── SCHEMA_SWIM_V47 -- 2026-08-31 pass (convective SIGMET archive) ───────────
#
# One table: convective_sigmet_archive -- the durable, append-only
# convective-SIGMET polygon history that the 2026-08-30 late-night pass
# identified as Detector D's missing weather half and correctly left
# unbuilt ("weather attribution requires an ARCHIVED, timestamped
# convective-SIGMET polygon history and none exists"). Fed by
# poller/skills/convective_sigmet_archiver.py from AWC's
# /api/data/airsigmet JSON endpoint (the same unblocked Data API
# web/main.py's live map overlay already uses -- that overlay is a
# 5-minute in-memory cache that stores nothing, which is correct for a
# UI snapshot and useless for "was there weather when THIS reroute
# happened").
#
# Keying -- deliberately NOT the composite id alone: live-verified
# 2026-08-31 that convective SIGMET series numbers recycle (today's
# KKCI-38E-E names a different storm than tomorrow's KKCI-38E-E), so
# uniqueness is UNIQUE(sigmet_id, valid_from). Within one issuance the
# product is immutable (a revision arrives as a NEW series number), so
# writes are INSERT OR IGNORE -- insert-once, never an upsert that could
# silently rewrite the first-seen archival record.
#
# hazard is stored even though the archiver filters to CONVECTIVE (the
# only hazard relevant to reroute-weather attribution per the external
# Detector D document) -- future flexibility if the filter ever widens.
# polygon is a JSON-serialized [[lat,lon],...] list (>=3 points,
# guaranteed by common/airsigmet.py's normalizer -- the same guard the
# web overlay has always applied). valid_from/valid_to/issued_at are
# platform-standard ISO-8601 Z strings (AWC serves epoch ints;
# airsigmet.epoch_to_iso converts). raw_text is the FULL product text,
# not the overlay's 600-char truncation.
#
# NO PRUNE JOB may ever cover this table without operator sign-off: it
# must outlive the reroute detectors' lookback horizon and accumulate
# enough seasons of history for attribution backtesting. It is
# deliberately absent from poller/skills/retention_prune.py's explicit
# opt-in _PRUNE_JOBS list (verified: that sweep only touches tables
# listed there). Growth is modest: ~tens of active convective SIGMETs
# nationwide at a summer peak, each archived once per issuance.
#
# Owned by the POLLER container (the archiver skill calls
# init_db_swim_v47() itself); ingest/main.py deliberately does NOT call
# it -- the v43 "web/poller-owned, not needed here" precedent.

SCHEMA_SWIM_V47 = """
CREATE TABLE IF NOT EXISTS convective_sigmet_archive (
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
CREATE INDEX IF NOT EXISTS idx_convective_sigmet_archive_window
    ON convective_sigmet_archive(valid_from, valid_to);
"""


def _apply_schema(schema: str) -> None:
    with conn() as c:
        for stmt in schema.strip().split(";"):
            stmt = stmt.strip()
            if not stmt:
                continue
            try:
                c.execute(stmt)
            except sqlite3.OperationalError as e:
                if "duplicate column" not in str(e).lower():
                    raise


def init_db_swim_v41() -> None:
    """Apply the v41 SWIM-audit schema -- see module docstring. Purely
    additive (new tables + three nullable flight_events columns);
    duplicate-column errors on re-run are swallowed the same way db.py's
    init_db_v37/v39/v40 do for their ALTERs."""
    _apply_schema(SCHEMA_SWIM_V41)


def init_db_swim_v42() -> None:
    """Apply the v42 additions (PARAM delay stats, REROUTE advisories,
    tdls_messages parsed columns) -- purely additive, idempotent, same
    duplicate-column tolerance as v41. Callers that call v41 explicitly
    (ingest/main.py, tests) call this right after it."""
    _apply_schema(SCHEMA_SWIM_V42)


def init_db_swim_v44() -> None:
    """Apply the v44 addition (fdps_diversion_continuations) -- purely
    additive, idempotent, same caller contract as v41/v42 above. (v43 is
    db.py's uas_phase columns -- shared number line, different module.)"""
    _apply_schema(SCHEMA_SWIM_V44)


def init_db_swim_v45() -> None:
    """Apply the v45 addition (operator_class on
    fdps_diversion_continuations) -- purely additive, idempotent
    (duplicate-column swallowed), same caller contract as v41/v42/v44."""
    _apply_schema(SCHEMA_SWIM_V45)


def init_db_swim_v46() -> None:
    """Apply the v46 additions (tfms_plan_removals, fdps_route_versions)
    -- purely additive, idempotent, same caller contract as v41-v45."""
    _apply_schema(SCHEMA_SWIM_V46)


def init_db_swim_v47() -> None:
    """Apply the v47 addition (convective_sigmet_archive) -- purely
    additive, idempotent. Caller contract DIFFERS from v41-v46: the
    poller-side archiver skill calls this itself each run (fresh-DB
    safe); ingest/main.py does not, per the v43 owned-elsewhere
    precedent -- see the SCHEMA_SWIM_V47 comment block."""
    _apply_schema(SCHEMA_SWIM_V47)


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def upsert_stdds_rvr(airport: str, runway: str,
                     touchdown_rvr_ft: int | None, touchdown_trend: str | None,
                     midpoint_rvr_ft: int | None, midpoint_trend: str | None,
                     rollout_rvr_ft: int | None, rollout_trend: str | None,
                     edge_light_setting: str | None,
                     centerline_light_setting: str | None,
                     last_seen: str) -> None:
    """One current-state row per (airport, runway). NULL RVR values mean
    'sensor offline / no report', never 0 -- see smes_parser.py's
    parse_rvr_data_update_message() for the normalization rules and why
    storing 0 would be dangerous downstream (a dead sensor must not read
    as worst-possible visibility)."""
    with conn() as c:
        c.execute("""
            INSERT INTO stdds_rvr
                (airport, runway, touchdown_rvr_ft, touchdown_trend,
                 midpoint_rvr_ft, midpoint_trend, rollout_rvr_ft, rollout_trend,
                 edge_light_setting, centerline_light_setting, last_seen)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(airport, runway) DO UPDATE SET
                touchdown_rvr_ft=excluded.touchdown_rvr_ft,
                touchdown_trend=excluded.touchdown_trend,
                midpoint_rvr_ft=excluded.midpoint_rvr_ft,
                midpoint_trend=excluded.midpoint_trend,
                rollout_rvr_ft=excluded.rollout_rvr_ft,
                rollout_trend=excluded.rollout_trend,
                edge_light_setting=excluded.edge_light_setting,
                centerline_light_setting=excluded.centerline_light_setting,
                last_seen=excluded.last_seen
        """, (airport, runway, touchdown_rvr_ft, touchdown_trend,
              midpoint_rvr_ft, midpoint_trend, rollout_rvr_ft, rollout_trend,
              edge_light_setting, centerline_light_setting, last_seen))


def get_stdds_rvr(airport: str, max_age_seconds: float = 900) -> list[dict]:
    """Current per-runway RVR rows for one airport, fresh within
    max_age_seconds (default 15 min -- RVR updates continuously while a
    sensor is reporting, so anything older is a stale/stopped sensor and
    callers like a future CPS integration must fall back to METAR
    visibility rather than trust it)."""
    cutoff = (datetime.utcnow() - timedelta(seconds=max_age_seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with conn() as c:
        rows = c.execute(
            "SELECT * FROM stdds_rvr WHERE airport=? AND last_seen > ?",
            (airport, cutoff),
        ).fetchall()
    return [dict(r) for r in rows]


def insert_tdes_departure_event(airport: str, callsign: str, event_time: str,
                                beacon_code: str | None, aircraft_type: str | None,
                                computer_id: str | None,
                                clearance_delivery_time: str | None,
                                parking_gate: str | None,
                                eram_gufi: str | None, sfdps_gufi: str | None,
                                destination_airport: str | None,
                                last_seen: str) -> bool:
    """Append-only, keyed (airport, callsign, event_time) -- a rebroadcast
    of the same event is a no-op (INSERT OR IGNORE), a genuinely new event
    (new event_time) is a new row. Returns True if a new row was written."""
    with conn() as c:
        cur = c.execute("""
            INSERT OR IGNORE INTO tdes_departure_events
                (airport, callsign, event_time, beacon_code, aircraft_type,
                 computer_id, clearance_delivery_time, parking_gate,
                 eram_gufi, sfdps_gufi, destination_airport, last_seen)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (airport, callsign, event_time, beacon_code, aircraft_type,
              computer_id, clearance_delivery_time, parking_gate,
              eram_gufi, sfdps_gufi, destination_airport, last_seen))
        return bool(cur.rowcount and cur.rowcount > 0)


def insert_tdls_message(airport: str, callsign: str | None, message_time: str | None,
                        beacon_code: str | None, aircraft_type: str | None,
                        computer_id: str | None, data_header: str | None,
                        data_body: str | None, eram_gufi: str | None,
                        sfdps_gufi: str | None, destination_airport: str | None,
                        received_at: str, *,
                        parsed: dict | None = None) -> None:
    """Append-only raw TDLS/PDC/CPDLC message envelope + body text. DC-area
    scoping happens at the parser (see smes_parser.py) so this table's
    growth stays bounded by local traffic, not the nationwide feed.

    2026-08-30 (v42): `parsed` optionally carries the regex-extracted
    PDC/DCL fields from smes_parser.parse_tdls_dcl_body() -- keys matching
    the v42 tdls_messages columns. Keyword-only + defaulting to None so
    every pre-v42 caller keeps working unchanged, and a raw body is
    ALWAYS stored verbatim whether or not anything parsed out of it."""
    parsed = parsed or {}
    with conn() as c:
        c.execute("""
            INSERT INTO tdls_messages
                (airport, callsign, message_time, beacon_code, aircraft_type,
                 computer_id, data_header, data_body, eram_gufi, sfdps_gufi,
                 destination_airport, received_at,
                 dcl_type, response_type, registration, cleared_to, sid,
                 sid_transition, expected_runway, climb_via_sid,
                 initial_altitude_ft, cruise_fl, dep_frequency,
                 proposed_dep_time, edct_time, route_text)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (airport, callsign, message_time, beacon_code, aircraft_type,
              computer_id, data_header, data_body, eram_gufi, sfdps_gufi,
              destination_airport, received_at,
              parsed.get("dcl_type"), parsed.get("response_type"),
              parsed.get("registration"), parsed.get("cleared_to"),
              parsed.get("sid"), parsed.get("sid_transition"),
              parsed.get("expected_runway"), parsed.get("climb_via_sid"),
              parsed.get("initial_altitude_ft"), parsed.get("cruise_fl"),
              parsed.get("dep_frequency"), parsed.get("proposed_dep_time"),
              parsed.get("edct_time"), parsed.get("route_text")))


def upsert_datis_snapshot(airport: str, atis_code: str | None,
                          edit_type: str | None, datis_time: str | None,
                          body: str | None, last_seen: str) -> None:
    """Latest D-ATIS text per airport (one row, overwritten -- the ATIS
    code letter itself versions the broadcast)."""
    with conn() as c:
        c.execute("""
            INSERT INTO datis_snapshots
                (airport, atis_code, edit_type, datis_time, body, last_seen)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(airport) DO UPDATE SET
                atis_code=excluded.atis_code,
                edit_type=excluded.edit_type,
                datis_time=excluded.datis_time,
                body=excluded.body,
                last_seen=excluded.last_seen
        """, (airport, atis_code, edit_type, datis_time, body, last_seen))


def upsert_tfms_edct_slot(control_element: str, aircraft_id: str,
                          control_type: str | None, program_parameter: str | None,
                          delay_mode: str | None,
                          departure_airport: str | None, arrival_airport: str | None,
                          slot_time: str | None, controlled_departure_time: str | None,
                          controlled_arrival_time: str | None,
                          controlled_departure_iso: str | None,
                          exempt_flag: int | None, cancel_flag: int | None,
                          slot_hold_flag: int | None,
                          earliest_arrival_or_entry: str | None,
                          initial_gate_departure_time: str | None,
                          report_time: str | None, last_seen: str) -> None:
    """One row per (control_element, aircraft_id) -- FADT broadcasts resend
    the full slot list on every program revision, so an upsert keeps the
    latest controlled times without unbounded growth. Raw DDHHMM strings
    are stored alongside the best-effort ISO normalization (see
    tfms_parser._fadt_ddhhmm_to_iso) so a normalization bug can always be
    re-derived from the raw value."""
    with conn() as c:
        c.execute("""
            INSERT INTO tfms_edct_slots
                (control_element, aircraft_id, control_type, program_parameter,
                 delay_mode, departure_airport, arrival_airport, slot_time,
                 controlled_departure_time, controlled_arrival_time,
                 controlled_departure_iso, exempt_flag, cancel_flag,
                 slot_hold_flag, earliest_arrival_or_entry,
                 initial_gate_departure_time, report_time, last_seen)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(control_element, aircraft_id) DO UPDATE SET
                control_type=excluded.control_type,
                program_parameter=excluded.program_parameter,
                delay_mode=excluded.delay_mode,
                departure_airport=excluded.departure_airport,
                arrival_airport=excluded.arrival_airport,
                slot_time=excluded.slot_time,
                controlled_departure_time=excluded.controlled_departure_time,
                controlled_arrival_time=excluded.controlled_arrival_time,
                controlled_departure_iso=excluded.controlled_departure_iso,
                exempt_flag=excluded.exempt_flag,
                cancel_flag=excluded.cancel_flag,
                slot_hold_flag=excluded.slot_hold_flag,
                earliest_arrival_or_entry=excluded.earliest_arrival_or_entry,
                initial_gate_departure_time=excluded.initial_gate_departure_time,
                report_time=excluded.report_time,
                last_seen=excluded.last_seen
        """, (control_element, aircraft_id, control_type, program_parameter,
              delay_mode, departure_airport, arrival_airport, slot_time,
              controlled_departure_time, controlled_arrival_time,
              controlled_departure_iso, exempt_flag, cancel_flag,
              slot_hold_flag, earliest_arrival_or_entry,
              initial_gate_departure_time, report_time, last_seen))


def upsert_tfms_param_delay_stats(elem_name: str, parameters_type: str,
                                  tmi_state: str, elem_type: str | None,
                                  ctl_program: str | None,
                                  event_start_time: str | None,
                                  event_end_time: str | None,
                                  cumulative_start_time: str | None,
                                  cumulative_end_time: str | None,
                                  impacting_condition_code: str | None,
                                  total_flights: int | None,
                                  affected_flights: int | None,
                                  total_delay_before_min: int | None,
                                  total_delay_after_min: int | None,
                                  max_delay_before_min: int | None,
                                  max_delay_after_min: int | None,
                                  avg_delay_before_min: float | None,
                                  avg_delay_after_min: float | None,
                                  delay_mode: str | None,
                                  report_time: str | None,
                                  last_seen: str) -> None:
    """One current-state row per (elem_name, parameters_type, tmi_state) --
    PARAM rebroadcasts full statistics on every program revision, and a
    PROPOSED model run must never overwrite the ACTUAL program's numbers
    (both states observed live for the same SAN GDP), hence tmi_state in
    the key."""
    with conn() as c:
        c.execute("""
            INSERT INTO tfms_param_delay_stats
                (elem_name, parameters_type, tmi_state, elem_type, ctl_program,
                 event_start_time, event_end_time, cumulative_start_time,
                 cumulative_end_time, impacting_condition_code, total_flights,
                 affected_flights, total_delay_before_min, total_delay_after_min,
                 max_delay_before_min, max_delay_after_min, avg_delay_before_min,
                 avg_delay_after_min, delay_mode, report_time, last_seen)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(elem_name, parameters_type, tmi_state) DO UPDATE SET
                elem_type=excluded.elem_type,
                ctl_program=excluded.ctl_program,
                event_start_time=excluded.event_start_time,
                event_end_time=excluded.event_end_time,
                cumulative_start_time=excluded.cumulative_start_time,
                cumulative_end_time=excluded.cumulative_end_time,
                impacting_condition_code=excluded.impacting_condition_code,
                total_flights=excluded.total_flights,
                affected_flights=excluded.affected_flights,
                total_delay_before_min=excluded.total_delay_before_min,
                total_delay_after_min=excluded.total_delay_after_min,
                max_delay_before_min=excluded.max_delay_before_min,
                max_delay_after_min=excluded.max_delay_after_min,
                avg_delay_before_min=excluded.avg_delay_before_min,
                avg_delay_after_min=excluded.avg_delay_after_min,
                delay_mode=excluded.delay_mode,
                report_time=excluded.report_time,
                last_seen=excluded.last_seen
        """, (elem_name, parameters_type, tmi_state, elem_type, ctl_program,
              event_start_time, event_end_time, cumulative_start_time,
              cumulative_end_time, impacting_condition_code, total_flights,
              affected_flights, total_delay_before_min, total_delay_after_min,
              max_delay_before_min, max_delay_after_min, avg_delay_before_min,
              avg_delay_after_min, delay_mode, report_time, last_seen))


def upsert_tfms_reroute(reroute_id: str, reroute_name: str | None,
                        reroute_status: str | None, tmi_id: str | None,
                        tmi_status: str | None, reroute_airborne: str | None,
                        time_type: str | None, start_time: str | None,
                        end_time: str | None, fca_name: str | None,
                        original_create_time: str | None,
                        last_update_time: str | None,
                        segment_count: int | None, dc_relevant: int | None,
                        segments_json: str | None, last_seen: str) -> None:
    """One current-state row per rerouteId. Advisories are rebroadcast on
    every update (tmiStatus UPDATED) and on cancellation, so an upsert
    keyed on the FAA's own rerouteId tracks each advisory's lifecycle
    without unbounded growth."""
    with conn() as c:
        c.execute("""
            INSERT INTO tfms_reroutes
                (reroute_id, reroute_name, reroute_status, tmi_id, tmi_status,
                 reroute_airborne, time_type, start_time, end_time, fca_name,
                 original_create_time, last_update_time, segment_count,
                 dc_relevant, segments_json, last_seen)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(reroute_id) DO UPDATE SET
                reroute_name=excluded.reroute_name,
                reroute_status=excluded.reroute_status,
                tmi_id=excluded.tmi_id,
                tmi_status=excluded.tmi_status,
                reroute_airborne=excluded.reroute_airborne,
                time_type=excluded.time_type,
                start_time=excluded.start_time,
                end_time=excluded.end_time,
                fca_name=excluded.fca_name,
                original_create_time=excluded.original_create_time,
                last_update_time=excluded.last_update_time,
                segment_count=excluded.segment_count,
                dc_relevant=excluded.dc_relevant,
                segments_json=excluded.segments_json,
                last_seen=excluded.last_seen
        """, (reroute_id, reroute_name, reroute_status, tmi_id, tmi_status,
              reroute_airborne, time_type, start_time, end_time, fca_name,
              original_create_time, last_update_time, segment_count,
              dc_relevant, segments_json, last_seen))


def get_flight_event_destination(flight_id: str) -> str | None:
    """Cheap PK lookup of the currently-stored destination for one
    flight_events row (GUFI key). Used by fdps_parser.write_flight_event's
    destination-change detection -- deliberately a single-column SELECT so
    the per-message cost on the highest-volume feed stays minimal, and
    only ever called for messages that actually carry a destination
    (FH/AH-family, a small fraction of FDPS traffic)."""
    with conn() as c:
        row = c.execute(
            "SELECT destination FROM flight_events WHERE flight_id=?",
            (flight_id,),
        ).fetchone()
    return row["destination"] if row else None


def insert_fdps_destination_change(flight_id: str, callsign: str | None,
                                   origin: str | None, old_destination: str,
                                   new_destination: str, source: str | None) -> None:
    """Append one destination-change observation (same GUFI, filed
    destination moved). Keyed on GUFI so callsign reuse across legs can
    never masquerade as a diversion (each leg has its own GUFI). Rows
    where new_destination == origin are stored too (positioning /
    return-to-field shapes) -- consumers filter origin != new_destination
    for the diversion-shaped subset."""
    with conn() as c:
        c.execute("""
            INSERT INTO fdps_destination_changes
                (flight_id, callsign, origin, old_destination, new_destination,
                 source, detected_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (flight_id, callsign, origin, old_destination, new_destination,
              source, _now_iso()))


def get_recent_destination_changes(window_secs: float = 3600) -> list[dict]:
    """Destination-change rows detected within the last window_secs,
    cheapest possible shape (no raw blobs live in this table). Added
    2026-08-30 evening for fdps_parser._check_alternate_saturation --
    the caller groups/normalizes in Python because airport spellings in
    old/new/origin mix FAA 3-letter and ICAO 4-letter forms (see that
    function) and SQL-side normalization would be unreadable for a table
    whose in-window row count is single digits. Uses
    idx_fdps_dest_changes_detected."""
    cutoff = (datetime.utcnow() - timedelta(seconds=window_secs)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    with conn() as c:
        # `id` added 2026-08-30 night pass so the continuation detector can
        # record which change row a pair was built from (change_id FK-ish
        # column) -- additive, existing callers read by key name.
        rows = c.execute("""
            SELECT id, flight_id, callsign, origin, old_destination,
                   new_destination, source, detected_at
            FROM fdps_destination_changes
            WHERE detected_at > ?
        """, (cutoff,)).fetchall()
    return [dict(r) for r in rows]


def update_flight_event_extras(flight_id: str, squawk: str | None,
                               registration: str | None,
                               controlling_facility: str | None) -> None:
    """Write the three v41 flight_events columns as a follow-up UPDATE to
    db.upsert_flight_event() (which this pass could not widen -- see
    module docstring). COALESCE keeps the last known non-NULL value: a TH
    position message without an aircraftDescription block must not erase
    the registration a prior FH filing populated."""
    if squawk is None and registration is None and controlling_facility is None:
        return
    with conn() as c:
        c.execute("""
            UPDATE flight_events SET
                squawk=COALESCE(?, squawk),
                registration=COALESCE(?, registration),
                controlling_facility=COALESCE(?, controlling_facility)
            WHERE flight_id=?
        """, (squawk, registration, controlling_facility, flight_id))


# ── Diversion-continuation pairs (2026-08-30 night pass) ─────────────────────

def get_flight_event_registration(flight_id: str) -> str | None:
    """Cheap PK lookup of the stored registration for one flight_events
    row -- same single-column shape as get_flight_event_destination()
    above, same reason (called on the FDPS hot path, but only for
    first-sighting filings). The column is v41's COALESCE-kept extras
    write, so a TH ping can't have erased what an FH filing populated."""
    with conn() as c:
        row = c.execute(
            "SELECT registration FROM flight_events WHERE flight_id=?",
            (flight_id,),
        ).fetchone()
    return row["registration"] if row else None


def find_acars_corroboration(registration: str, airports: list[str],
                             window_secs: float = 10800) -> dict | None:
    """Best-effort ACARS corroboration for a continuation pair: the most
    recent acars_messages row in the window whose tail matches
    `registration` (dash-insensitive) and whose text mentions any of the
    (already-normalized) airport codes -- checked in both FAA 3-letter and
    ICAO K-prefixed spellings -- or a divert-family keyword. Returns
    {"id", "received_at", "msg_text"} or None.

    A None here is the NORMAL case, not a failure: acars_messages has had
    zero rows ever on this box (the local acars_router emits nothing --
    see ingest/README.md's local-airspace section), and even with a live
    feed most aircraft aren't ACARS-equipped. The continuation detector
    treats this strictly as a confidence bonus (operator refinement),
    never a gate."""
    reg = (registration or "").upper().replace("-", "").strip()
    if not reg:
        return None
    cutoff = (datetime.utcnow() - timedelta(seconds=window_secs)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    try:
        with conn() as c:
            rows = c.execute("""
                SELECT id, received_at, tail, msg_text
                FROM acars_messages
                WHERE received_at > ? AND tail IS NOT NULL AND msg_text IS NOT NULL
                ORDER BY id DESC LIMIT 200
            """, (cutoff,)).fetchall()
    except sqlite3.OperationalError:
        # acars_messages lives in db.py's base SCHEMA -- absent only in a
        # partially-initialized test DB. Treat as no corroboration.
        return None
    tokens: list[str] = []
    for a in airports:
        if not a:
            continue
        a = a.upper()
        tokens.append(a)
        if len(a) == 3:
            tokens.append("K" + a)
    tokens += ["DIVERT", "DVRT", "ALTERNATE", "ALTN"]
    for r in rows:
        if (r["tail"] or "").upper().replace("-", "").strip() != reg:
            continue
        text = (r["msg_text"] or "").upper()
        if any(t in text for t in tokens):
            return {"id": r["id"], "received_at": r["received_at"],
                    "msg_text": r["msg_text"]}
    return None


def insert_diversion_continuation(change_id: int | None,
                                  diverted_flight_id: str,
                                  continuation_flight_id: str,
                                  callsign: str | None,
                                  continuation_callsign: str | None,
                                  match_basis: str,
                                  registration: str | None,
                                  origin: str | None,
                                  original_destination: str,
                                  diversion_airport: str,
                                  acars_msg_id: int | None,
                                  confidence: str,
                                  diversion_detected_at: str | None,
                                  operator_class: str | None = None) -> bool:
    """Record one diversion-continuation pair. Returns True only when the
    row is NEW (the UNIQUE(diverted_flight_id, continuation_flight_id)
    constraint absorbed nothing) -- callers use that as the once-only gate
    for the pair's single alert, so an FDPS rebroadcast of the same
    continuation filing can never re-fire it. operator_class (v45,
    'scheduled'/'fractional'/'ga_tail') records the alert-gate decision
    made at detection time; only 'scheduled' pairs are alerted."""
    with conn() as c:
        cur = c.execute("""
            INSERT OR IGNORE INTO fdps_diversion_continuations
                (change_id, diverted_flight_id, continuation_flight_id,
                 callsign, continuation_callsign, match_basis, registration,
                 origin, original_destination, diversion_airport,
                 acars_msg_id, confidence, diversion_detected_at, detected_at,
                 operator_class)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (change_id, diverted_flight_id, continuation_flight_id,
              callsign, continuation_callsign, match_basis, registration,
              origin, original_destination, diversion_airport,
              acars_msg_id, confidence, diversion_detected_at, _now_iso(),
              operator_class))
        return cur.rowcount > 0


# ── Plan removals / cancellation classification (2026-08-30 late-night) ──────

def upsert_tfms_plan_removal(callsign: str, igtd: str | None,
                             carrier: str | None, origin: str | None,
                             destination: str | None, flight_ref: str | None,
                             removed_at: str | None, removal_trigger: str | None,
                             kind: str, source_facility: str | None,
                             filed_lead_h: float | None,
                             origin_surveilled: bool) -> int | None:
    """Record one plan-removal observation, keyed on the LEG
    (callsign+igtd+airport pair -- see SCHEMA_SWIM_V46's comment for why
    the key is never flightRef). A re-removal of a leg that was
    previously reinstated re-opens the cycle: removed_at/trigger/kind
    are refreshed and confirmed_at/reinstated_at are cleared, but
    accumulated evidence is KEPT (a flight that already produced a
    departure message stays measured as having flown). Returns the row
    id, or None on failure (callers treat this as non-fatal)."""
    igtd_key = (igtd or "").strip()
    with conn() as c:
        c.execute("""
            INSERT INTO tfms_plan_removals
                (callsign, igtd, carrier, origin, destination, flight_ref,
                 removed_at, removal_trigger, kind, source_facility,
                 filed_lead_h, origin_surveilled, detected_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(callsign, igtd, origin, destination) DO UPDATE SET
                removed_at=excluded.removed_at,
                removal_trigger=excluded.removal_trigger,
                kind=excluded.kind,
                flight_ref=excluded.flight_ref,
                source_facility=excluded.source_facility,
                filed_lead_h=excluded.filed_lead_h,
                origin_surveilled=excluded.origin_surveilled,
                detected_at=excluded.detected_at,
                confirmed_at=NULL,
                reinstated_at=NULL
        """, (callsign, igtd_key, carrier, origin, destination, flight_ref,
              removed_at, removal_trigger, kind, source_facility,
              filed_lead_h, 1 if origin_surveilled else 0, _now_iso()))
        row = c.execute("""
            SELECT id FROM tfms_plan_removals
            WHERE callsign=? AND igtd=? AND origin IS ? AND destination IS ?
        """, (callsign, igtd_key, origin, destination)).fetchone()
    return row["id"] if row else None


def get_removal_activity_watch(window_secs: float = 48 * 3600) -> list[dict]:
    """Rows the ingest-side activity watch should still be listening for:
    recent removals with no flew-evidence yet (a reinstated plan is still
    watched -- whether it ultimately FLEW is exactly the measurement).
    The evidence LIKE filter matches the canonical '"flew": true' JSON
    fragment record_removal_activity() writes (json.dumps default
    separators)."""
    cutoff = (datetime.utcnow() - timedelta(seconds=window_secs)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    with conn() as c:
        rows = c.execute("""
            SELECT id, callsign, igtd, origin, destination, kind,
                   reinstated_at
            FROM tfms_plan_removals
            WHERE detected_at > ? AND evidence NOT LIKE '%"flew": true%'
        """, (cutoff,)).fetchall()
    return [dict(r) for r in rows]


def record_removal_activity(row_id: int, evidence_key: str,
                            value, flew: bool, reinstated: bool) -> None:
    """Merge one activity observation into a removal row's evidence JSON.
    flew=True additionally stamps the canonical '"flew": true' marker the
    watch/sweep filters key on; reinstated=True sets reinstated_at once
    (never overwriting an earlier reinstatement timestamp)."""
    with conn() as c:
        row = c.execute(
            "SELECT evidence, reinstated_at FROM tfms_plan_removals WHERE id=?",
            (row_id,),
        ).fetchone()
        if row is None:
            return
        try:
            evidence = json.loads(row["evidence"] or "{}")
        except (ValueError, TypeError):
            evidence = {}
        evidence[evidence_key] = value
        if flew:
            evidence["flew"] = True
        c.execute("UPDATE tfms_plan_removals SET evidence=? WHERE id=?",
                  (json.dumps(evidence), row_id))
        if reinstated and not row["reinstated_at"]:
            c.execute(
                "UPDATE tfms_plan_removals SET reinstated_at=? WHERE id=?",
                (_now_iso(), row_id))


def sweep_confirm_removals(settle_secs: float = 3600,
                           now_dt: datetime | None = None) -> int:
    """Detector C's confirmation test, applied in bulk (the external
    document's ALL-must-hold conjunction): kind is a real cancellation
    trigger (classified at insert), origin surveilled (departure evidence
    would have been visible), no flew-evidence, not reinstated, and the
    settle window has passed since BOTH igtd and the removal itself (a
    removal is not final -- the reference system saw removals reinstated
    57% of the time, so a detector that never waits is wrong more often
    than right). Rows with no igtd can never confirm (the settle clock
    has no anchor). ISO-8601 Z strings compare correctly as text.
    Returns the number of rows confirmed."""
    now = now_dt or datetime.utcnow()
    now_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    cutoff = (now - timedelta(seconds=settle_secs)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    with conn() as c:
        cur = c.execute("""
            UPDATE tfms_plan_removals SET confirmed_at=?
            WHERE kind='cancellation'
              AND origin_surveilled=1
              AND confirmed_at IS NULL
              AND reinstated_at IS NULL
              AND evidence NOT LIKE '%"flew": true%'
              AND igtd != '' AND igtd <= ?
              AND (removed_at IS NULL OR removed_at <= ?)
        """, (now_iso, cutoff, cutoff))
        return cur.rowcount


def measure_removal_fly_rates(days: float = 7) -> list[dict]:
    """OUR system's flew-anyway rate per removal trigger -- the external
    document's own methodology (take every removal over a window, ask
    whether the flight subsequently flew, tabulate by trigger), run
    against OUR rows instead of trusting the reference percentages.
    Meaningless until real removals accumulate; returns per-trigger
    {removal_trigger, kind, legs, flew, reinstated, flew_pct}."""
    cutoff = (datetime.utcnow() - timedelta(days=days)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    with conn() as c:
        rows = c.execute("""
            SELECT removal_trigger, kind, COUNT(*) AS legs,
                   SUM(CASE WHEN evidence LIKE '%"flew": true%' THEN 1 ELSE 0 END) AS flew,
                   SUM(CASE WHEN reinstated_at IS NOT NULL THEN 1 ELSE 0 END) AS reinstated
            FROM tfms_plan_removals
            WHERE detected_at > ?
            GROUP BY removal_trigger, kind
            ORDER BY legs DESC
        """, (cutoff,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["flew_pct"] = round(100.0 * (d["flew"] or 0) / d["legs"], 1) if d["legs"] else None
        out.append(d)
    return out


# ── FDPS route versions (2026-08-30 late-night, Detector D groundwork) ───────

def upsert_fdps_route_version(flight_id: str, callsign: str | None,
                              origin: str | None, destination: str | None,
                              route_text: str, source: str | None,
                              eta: str | None) -> tuple[bool, dict | None]:
    """Incremental distinct-(flight, route)-pairs upsert (the external
    document's bounded-read structure -- re-scanning raw message history
    for multi-version flights does not scale). A rebroadcast of a known
    route only bumps last_seen/times_seen/eta_last. Returns
    (is_new_version, previous_latest_version_row_or_None) so the caller
    can classify the change (fdps_parser._classify_route_change) and
    store the verdict via set_route_version_class() -- classification is
    kept out of this module so the route grammar lives next to the
    parser that produces the strings."""
    now = _now_iso()
    with conn() as c:
        existing = c.execute("""
            SELECT id FROM fdps_route_versions
            WHERE flight_id=? AND route_text=?
        """, (flight_id, route_text)).fetchone()
        if existing:
            c.execute("""
                UPDATE fdps_route_versions
                SET last_seen=?, times_seen=times_seen+1,
                    eta_last=COALESCE(?, eta_last)
                WHERE id=?
            """, (now, eta, existing["id"]))
            return False, None
        prev = c.execute("""
            SELECT * FROM fdps_route_versions
            WHERE flight_id=? ORDER BY version_num DESC LIMIT 1
        """, (flight_id,)).fetchone()
        version_num = (prev["version_num"] + 1) if prev else 1
        c.execute("""
            INSERT INTO fdps_route_versions
                (flight_id, callsign, origin, destination, route_text,
                 source, version_num, eta_first, eta_last, first_seen,
                 last_seen)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (flight_id, callsign, origin, destination, route_text, source,
              version_num, eta, eta, now, now))
        return True, (dict(prev) if prev else None)


def set_route_version_class(flight_id: str, route_text: str,
                            change_class: str | None,
                            eta_delta_min: float | None) -> None:
    """Stamp the classifier verdict (and arrival-estimate movement, when
    both versions carried one) onto a just-inserted route version."""
    with conn() as c:
        c.execute("""
            UPDATE fdps_route_versions
            SET change_class=?, eta_delta_min=?
            WHERE flight_id=? AND route_text=?
        """, (change_class, eta_delta_min, flight_id, route_text))


# ── convective_sigmet_archive writer (v47, 2026-08-31) ───────────────────────

def archive_convective_sigmets(records: list[dict]) -> tuple[int, int]:
    """Append normalized airsigmet records (common/airsigmet.py
    normalize_airsigmet() shape) to convective_sigmet_archive.

    INSERT OR IGNORE on UNIQUE(sigmet_id, valid_from): insert-once per
    issuance, never an upsert -- a re-fetch of a still-active SIGMET must
    not rewrite the row's first_seen archival timestamp, and a SIGMET's
    coords/validity never change within one issuance (a revision arrives
    as a new series number). See the SCHEMA_SWIM_V47 comment for why the
    composite id alone is NOT the key (series numbers recycle daily,
    live-verified 2026-08-31).

    Per-record defensive: one malformed record is skipped with a log
    line, never allowed to abort the rest of the batch (this runs inside
    a scheduled production skill). Returns (inserted, skipped)."""
    import logging
    from common.airsigmet import epoch_to_iso
    log = logging.getLogger(__name__)
    inserted = 0
    skipped = 0
    now = _now_iso()
    with conn() as c:
        for rec in records:
            try:
                polygon = rec.get("coords")
                if not polygon or len(polygon) < 3:
                    # Normalizer already guarantees this; belt-and-
                    # suspenders for direct callers/tests.
                    skipped += 1
                    continue
                cur = c.execute("""
                    INSERT OR IGNORE INTO convective_sigmet_archive
                        (sigmet_id, airsigmet_type, hazard, severity,
                         altitude_low_ft, altitude_high_ft, issued_at,
                         valid_from, valid_to, movement_dir_deg,
                         movement_spd_kt, polygon, raw_text, first_seen)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    rec.get("id") or "",
                    rec.get("type"),
                    rec.get("hazard") or "",
                    rec.get("severity"),
                    rec.get("altitude_low"),
                    rec.get("altitude_high"),
                    epoch_to_iso(rec.get("issued_at")),
                    epoch_to_iso(rec.get("valid_from")),
                    epoch_to_iso(rec.get("valid_to")),
                    rec.get("movement_dir"),
                    rec.get("movement_spd"),
                    json.dumps(polygon),
                    rec.get("raw_text"),
                    now,
                ))
                inserted += cur.rowcount
            except Exception as e:
                skipped += 1
                log.warning("archive_convective_sigmets: skipped record "
                            "%s: %s", rec.get("id", "?"), e)
    return inserted, skipped
