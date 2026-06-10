"""
Database layer — SQLite, single file, append-friendly.
Schema is authoritative here. Migrations are additive (ALTER TABLE only).
"""

import json
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Generator

from common import config


def _db_path() -> Path:
    p = Path(config.db_path())
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


@contextmanager
def conn() -> Generator[sqlite3.Connection, None, None]:
    """Context manager: autocommit on success, rollback on exception."""
    c = sqlite3.connect(str(_db_path()), timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys=ON")
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


# ── Schema ─────────────────────────────────────────────────────────────────────

SCHEMA = """
-- Feed freshness tracking
CREATE TABLE IF NOT EXISTS feed_state (
    feed_name       TEXT PRIMARY KEY,
    fetched_at      REAL,           -- Unix timestamp
    error           TEXT,           -- NULL on success
    consecutive_failures INTEGER DEFAULT 0,
    payload_hash    TEXT            -- SHA-256 of raw payload (change detection)
);

-- TFRs: raw + enriched
CREATE TABLE IF NOT EXISTS tfrs (
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

-- METAR / weather snapshot (latest only per station)
CREATE TABLE IF NOT EXISTS metar_snapshot (
    station         TEXT PRIMARY KEY,
    raw_metar       TEXT NOT NULL,
    ceiling_ft      INTEGER,
    visibility_sm   REAL,
    wind_kt         INTEGER,
    precip_code     TEXT,           -- RA / SN / TS / etc. — NULL if clear
    obs_time        REAL,
    fetched_at      REAL
);

-- NAS ground stops / GDPs
CREATE TABLE IF NOT EXISTS nas_programs (
    program_id      TEXT PRIMARY KEY,
    type            TEXT,           -- GDP | GS | AAR
    facility        TEXT,
    raw_json        TEXT,
    active          INTEGER DEFAULT 1,
    fetched_at      REAL
);

-- CPS scores (history + latest)
CREATE TABLE IF NOT EXISTS cps_scores (
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

-- Route impact narrative (latest)
CREATE TABLE IF NOT EXISTS hot_alerts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    computed_at     REAL DEFAULT (unixepoch()),
    route_narrative TEXT,
    active_tfrs     TEXT,           -- JSON array of TFR IDs
    vip_flags       TEXT            -- JSON array of VIP callsigns matched
);

-- Audit log (append-only, Tier 2 actions)
CREATE TABLE IF NOT EXISTS audit_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_time      REAL DEFAULT (unixepoch()),
    action          TEXT NOT NULL,
    tier            TEXT NOT NULL,
    token_prefix    TEXT,           -- First 8 chars of token (never full token)
    remote_addr     TEXT,
    detail          TEXT            -- JSON
);

-- Issued auth tokens (hash stored, never plaintext)
CREATE TABLE IF NOT EXISTS auth_tokens (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    token_hash      TEXT UNIQUE NOT NULL,
    token_prefix    TEXT NOT NULL,  -- ctdc_<user>_ prefix for display
    user_label      TEXT NOT NULL,
    tier            TEXT NOT NULL,  -- cert | shares | admin
    device_label    TEXT,
    created_at      REAL DEFAULT (unixepoch()),
    expires_at      REAL,           -- NULL = no expiry
    revoked_at      REAL            -- NULL = active
);

-- Trigger queue (admin mutations)
CREATE TABLE IF NOT EXISTS trigger_log (
    id              TEXT PRIMARY KEY,   -- UUID
    trigger_type    TEXT NOT NULL,
    payload         TEXT,               -- JSON
    queued_at       REAL DEFAULT (unixepoch()),
    outcome         TEXT DEFAULT 'in_flight',   -- in_flight | success | failed
    resolved_at     REAL,
    error_msg       TEXT
);
"""


def init_db() -> None:
    """Create schema if not present. Safe to call on every startup."""
    with conn() as c:
        c.executescript(SCHEMA)


# ── Feed state helpers ─────────────────────────────────────────────────────────

def upsert_feed(feed_name: str, fetched_at: float, error: str | None,
                payload_hash: str | None = None) -> None:
    with conn() as c:
        if error:
            c.execute("""
                INSERT INTO feed_state (feed_name, fetched_at, error, consecutive_failures, payload_hash)
                VALUES (?, ?, ?, 1, ?)
                ON CONFLICT(feed_name) DO UPDATE SET
                    fetched_at=excluded.fetched_at,
                    error=excluded.error,
                    consecutive_failures=consecutive_failures+1,
                    payload_hash=excluded.payload_hash
            """, (feed_name, fetched_at, error, payload_hash))
        else:
            c.execute("""
                INSERT INTO feed_state (feed_name, fetched_at, error, consecutive_failures, payload_hash)
                VALUES (?, ?, NULL, 0, ?)
                ON CONFLICT(feed_name) DO UPDATE SET
                    fetched_at=excluded.fetched_at,
                    error=NULL,
                    consecutive_failures=0,
                    payload_hash=excluded.payload_hash
            """, (feed_name, fetched_at, payload_hash))


def upsert_feed_skip(feed_name: str, fetched_at: float, reason: str) -> None:
    """Record a deliberate skip (e.g. awaiting_credentials). Resets consecutive_failures to 0."""
    with conn() as c:
        c.execute("""
            INSERT INTO feed_state (feed_name, fetched_at, error, consecutive_failures, payload_hash)
            VALUES (?, ?, ?, 0, NULL)
            ON CONFLICT(feed_name) DO UPDATE SET
                fetched_at=excluded.fetched_at,
                error=excluded.error,
                consecutive_failures=0,
                payload_hash=NULL
        """, (feed_name, fetched_at, reason))


def get_feed_states() -> list[dict]:
    with conn() as c:
        rows = c.execute("SELECT * FROM feed_state ORDER BY feed_name").fetchall()
        return [dict(r) for r in rows]


# ── TFR helpers ───────────────────────────────────────────────────────────────

def upsert_tfr(tfr_id: str, raw_json: str, is_vip: bool,
               effective_start: float | None = None,
               effective_end: float | None = None) -> None:
    with conn() as c:
        c.execute("""
            INSERT INTO tfrs (tfr_id, raw_json, is_vip, effective_start, effective_end)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(tfr_id) DO UPDATE SET
                raw_json=excluded.raw_json,
                is_vip=excluded.is_vip,
                effective_start=excluded.effective_start,
                effective_end=excluded.effective_end
        """, (tfr_id, raw_json, int(is_vip), effective_start, effective_end))


def get_active_tfrs() -> list[dict]:
    now = time.time()
    with conn() as c:
        rows = c.execute("""
            SELECT * FROM tfrs
            WHERE (effective_end IS NULL OR effective_end > ?)
            ORDER BY effective_start DESC
        """, (now,)).fetchall()
        return [dict(r) for r in rows]


def mark_tfr_notified(tfr_id: str) -> None:
    with conn() as c:
        c.execute("UPDATE tfrs SET notified=1 WHERE tfr_id=?", (tfr_id,))


def set_tfr_enrichment(tfr_id: str, text: str) -> None:
    with conn() as c:
        c.execute("""
            UPDATE tfrs SET enriched_text=?, enriched_at=unixepoch()
            WHERE tfr_id=?
        """, (text, tfr_id))


# ── METAR helpers ─────────────────────────────────────────────────────────────

def upsert_metar(station: str, raw_metar: str, ceiling_ft: int | None,
                 visibility_sm: float | None, wind_kt: int | None,
                 precip_code: str | None, obs_time: float) -> None:
    with conn() as c:
        c.execute("""
            INSERT INTO metar_snapshot
                (station, raw_metar, ceiling_ft, visibility_sm, wind_kt,
                 precip_code, obs_time, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, unixepoch())
            ON CONFLICT(station) DO UPDATE SET
                raw_metar=excluded.raw_metar,
                ceiling_ft=excluded.ceiling_ft,
                visibility_sm=excluded.visibility_sm,
                wind_kt=excluded.wind_kt,
                precip_code=excluded.precip_code,
                obs_time=excluded.obs_time,
                fetched_at=excluded.fetched_at
        """, (station, raw_metar, ceiling_ft, visibility_sm, wind_kt,
              precip_code, obs_time))


def get_metar_snapshot() -> list[dict]:
    with conn() as c:
        rows = c.execute("SELECT * FROM metar_snapshot ORDER BY station").fetchall()
        return [dict(r) for r in rows]


# ── NAS helpers ───────────────────────────────────────────────────────────────

def upsert_nas_program(program_id: str, prog_type: str, facility: str,
                       raw_json: str) -> None:
    with conn() as c:
        c.execute("""
            INSERT INTO nas_programs (program_id, type, facility, raw_json, active, fetched_at)
            VALUES (?, ?, ?, ?, 1, unixepoch())
            ON CONFLICT(program_id) DO UPDATE SET
                type=excluded.type,
                facility=excluded.facility,
                raw_json=excluded.raw_json,
                active=1,
                fetched_at=excluded.fetched_at
        """, (program_id, prog_type, facility, raw_json))


def deactivate_absent_programs(active_ids: list[str]) -> None:
    if not active_ids:
        return
    with conn() as c:
        placeholders = ",".join("?" * len(active_ids))
        c.execute(f"""
            UPDATE nas_programs SET active=0
            WHERE program_id NOT IN ({placeholders})
        """, active_ids)


def get_active_nas_programs() -> list[dict]:
    with conn() as c:
        rows = c.execute(
            "SELECT * FROM nas_programs WHERE active=1 ORDER BY type, facility"
        ).fetchall()
        return [dict(r) for r in rows]


# ── CPS helpers ───────────────────────────────────────────────────────────────

def insert_cps(score: str, label: str, factors: dict, narrative: str) -> None:
    with conn() as c:
        c.execute("""
            INSERT INTO cps_scores
                (score, label, ceiling_factor, visibility_factor, wind_factor,
                 precip_factor, airspace_factor, gdp_factor, narrative)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            score, label,
            factors.get("ceiling"), factors.get("visibility"),
            factors.get("wind"), factors.get("precip"),
            factors.get("airspace"), factors.get("gdp"),
            narrative,
        ))


def get_latest_cps() -> dict | None:
    with conn() as c:
        row = c.execute(
            "SELECT * FROM cps_scores ORDER BY computed_at DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None


# ── Hot alerts helpers ────────────────────────────────────────────────────────

def insert_route_narrative(narrative: str, active_tfrs: list[str],
                           vip_flags: list[str]) -> None:
    with conn() as c:
        c.execute("""
            INSERT INTO hot_alerts (route_narrative, active_tfrs, vip_flags)
            VALUES (?, ?, ?)
        """, (narrative, json.dumps(active_tfrs), json.dumps(vip_flags)))


def get_latest_route_narrative() -> dict | None:
    with conn() as c:
        row = c.execute(
            "SELECT * FROM hot_alerts ORDER BY computed_at DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None


# ── Audit log helpers ─────────────────────────────────────────────────────────

def audit(action: str, tier: str, token_prefix: str | None,
          remote_addr: str | None, detail: dict | None = None) -> None:
    with conn() as c:
        c.execute("""
            INSERT INTO audit_log (action, tier, token_prefix, remote_addr, detail)
            VALUES (?, ?, ?, ?, ?)
        """, (action, tier, token_prefix, remote_addr,
              json.dumps(detail) if detail else None))


def get_audit_log(limit: int = 50, since: float | None = None) -> list[dict]:
    with conn() as c:
        if since:
            rows = c.execute("""
                SELECT * FROM audit_log WHERE event_time >= ?
                ORDER BY event_time DESC LIMIT ?
            """, (since, limit)).fetchall()
        else:
            rows = c.execute("""
                SELECT * FROM audit_log ORDER BY event_time DESC LIMIT ?
            """, (limit,)).fetchall()
        return [dict(r) for r in rows]


def audit_count_24h() -> int:
    cutoff = time.time() - 86400
    with conn() as c:
        row = c.execute(
            "SELECT COUNT(*) FROM audit_log WHERE event_time >= ?", (cutoff,)
        ).fetchone()
        return row[0] if row else 0


# ── Auth token helpers ────────────────────────────────────────────────────────

def insert_token(token_hash: str, token_prefix: str, user_label: str,
                 tier: str, device_label: str | None,
                 expires_at: float | None) -> None:
    with conn() as c:
        c.execute("""
            INSERT INTO auth_tokens
                (token_hash, token_prefix, user_label, tier, device_label, expires_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (token_hash, token_prefix, user_label, tier, device_label, expires_at))


def lookup_token(token_hash: str) -> dict | None:
    with conn() as c:
        row = c.execute("""
            SELECT * FROM auth_tokens
            WHERE token_hash=? AND revoked_at IS NULL
              AND (expires_at IS NULL OR expires_at > unixepoch())
        """, (token_hash,)).fetchone()
        return dict(row) if row else None


def revoke_token(token_prefix: str) -> int:
    """Revoke all active tokens matching prefix. Returns count revoked."""
    with conn() as c:
        c.execute("""
            UPDATE auth_tokens SET revoked_at=unixepoch()
            WHERE token_prefix LIKE ? AND revoked_at IS NULL
        """, (token_prefix + "%",))
        return c.execute("SELECT changes()").fetchone()[0]


def list_tokens(active_only: bool = True) -> list[dict]:
    with conn() as c:
        if active_only:
            rows = c.execute("""
                SELECT * FROM auth_tokens
                WHERE revoked_at IS NULL
                  AND (expires_at IS NULL OR expires_at > unixepoch())
                ORDER BY created_at DESC
            """).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM auth_tokens ORDER BY created_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]


def active_token_count() -> int:
    with conn() as c:
        row = c.execute("""
            SELECT COUNT(*) FROM auth_tokens
            WHERE revoked_at IS NULL
              AND (expires_at IS NULL OR expires_at > unixepoch())
        """).fetchone()
        return row[0] if row else 0


# ── Trigger log helpers ───────────────────────────────────────────────────────

def insert_trigger(trigger_id: str, trigger_type: str,
                   payload: dict | None) -> None:
    with conn() as c:
        c.execute("""
            INSERT INTO trigger_log (id, trigger_type, payload)
            VALUES (?, ?, ?)
        """, (trigger_id, trigger_type, json.dumps(payload) if payload else None))


def resolve_trigger(trigger_id: str, outcome: str,
                    error_msg: str | None = None) -> None:
    with conn() as c:
        c.execute("""
            UPDATE trigger_log
            SET outcome=?, resolved_at=unixepoch(), error_msg=?
            WHERE id=?
        """, (outcome, error_msg, trigger_id))


def get_triggers(outcome: str | None = None, limit: int = 20) -> list[dict]:
    with conn() as c:
        if outcome == "in_flight":
            rows = c.execute("""
                SELECT * FROM trigger_log WHERE outcome='in_flight'
                ORDER BY queued_at DESC LIMIT ?
            """, (limit,)).fetchall()
        elif outcome:
            rows = c.execute("""
                SELECT * FROM trigger_log WHERE outcome=?
                ORDER BY resolved_at DESC LIMIT ?
            """, (outcome, limit)).fetchall()
        else:
            rows = c.execute("""
                SELECT * FROM trigger_log ORDER BY queued_at DESC LIMIT ?
            """, (limit,)).fetchall()
        return [dict(r) for r in rows]


# ── Schema additions for new feeds ────────────────────────────────────────────

SCHEMA_V2 = """
-- FAA NOTAMs
CREATE TABLE IF NOT EXISTS notams (
    notam_id        TEXT PRIMARY KEY,
    raw_json        TEXT NOT NULL,
    facility        TEXT,
    classification  TEXT,           -- NOTAM-D, FDC, POINTER, etc.
    effective_start REAL,
    effective_end   REAL,
    text_body       TEXT,
    inserted_at     REAL DEFAULT (unixepoch())
);

-- NWS active hazardous weather alerts (DC/MD/VA)
CREATE TABLE IF NOT EXISTS nws_alerts (
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

-- NWS zone forecast (latest only per zone)
CREATE TABLE IF NOT EXISTS nws_forecast (
    zone            TEXT PRIMARY KEY,
    forecast_json   TEXT,
    fetched_at      REAL DEFAULT (unixepoch())
);

-- Amtrak status for DC-area trains
CREATE TABLE IF NOT EXISTS amtrak_status (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    fetched_at      REAL DEFAULT (unixepoch()),
    trains_json     TEXT,           -- JSON array of train status objects
    delay_summary   TEXT            -- Human-readable delay summary
);

-- Ops plan (operator-populated scheduled trips)
CREATE TABLE IF NOT EXISTS ops_plan (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_date       TEXT,           -- YYYY-MM-DD
    raw_json        TEXT,           -- Full plan JSON
    trip_count      INTEGER,
    loaded_at       REAL DEFAULT (unixepoch())
);
"""


def init_db_v2() -> None:
    """Apply v2 schema additions. Called alongside init_db() at startup."""
    with conn() as c:
        c.executescript(SCHEMA_V2)


# ── NOTAM helpers ─────────────────────────────────────────────────────────────

def upsert_notam(notam_id: str, raw_json: str, facility: str,
                 classification: str, effective_start: float | None,
                 effective_end: float | None, text_body: str) -> None:
    with conn() as c:
        c.execute("""
            INSERT INTO notams
                (notam_id, raw_json, facility, classification,
                 effective_start, effective_end, text_body)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(notam_id) DO UPDATE SET
                raw_json=excluded.raw_json,
                facility=excluded.facility,
                classification=excluded.classification,
                effective_start=excluded.effective_start,
                effective_end=excluded.effective_end,
                text_body=excluded.text_body
        """, (notam_id, raw_json, facility, classification,
              effective_start, effective_end, text_body))


def get_active_notams() -> list[dict]:
    now = time.time()
    with conn() as c:
        rows = c.execute("""
            SELECT * FROM notams
            WHERE effective_end IS NULL OR effective_end > ?
            ORDER BY effective_start DESC
        """, (now,)).fetchall()
        return [dict(r) for r in rows]


# ── NWS helpers ───────────────────────────────────────────────────────────────

def upsert_nws_alert(alert_id: str, event_type: str, area_desc: str,
                     severity: str, certainty: str, effective: float,
                     expires: float, headline: str, description: str) -> None:
    with conn() as c:
        c.execute("""
            INSERT INTO nws_alerts
                (alert_id, event_type, area_desc, severity, certainty,
                 effective, expires, headline, description)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(alert_id) DO UPDATE SET
                event_type=excluded.event_type,
                area_desc=excluded.area_desc,
                severity=excluded.severity,
                certainty=excluded.certainty,
                effective=excluded.effective,
                expires=excluded.expires,
                headline=excluded.headline,
                description=excluded.description,
                fetched_at=unixepoch()
        """, (alert_id, event_type, area_desc, severity, certainty,
              effective, expires, headline, description))


def expire_nws_alerts(active_ids: list[str]) -> None:
    """Remove alerts no longer in the feed."""
    if not active_ids:
        return
    with conn() as c:
        placeholders = ",".join("?" * len(active_ids))
        c.execute(f"DELETE FROM nws_alerts WHERE alert_id NOT IN ({placeholders})",
                  active_ids)


def get_active_nws_alerts() -> list[dict]:
    now = time.time()
    with conn() as c:
        rows = c.execute("""
            SELECT * FROM nws_alerts
            WHERE expires > ?
            ORDER BY severity DESC, effective DESC
        """, (now,)).fetchall()
        return [dict(r) for r in rows]


# ── Amtrak helpers ────────────────────────────────────────────────────────────

def insert_amtrak_status(trains_json: str, delay_summary: str) -> None:
    with conn() as c:
        c.execute("""
            INSERT INTO amtrak_status (trains_json, delay_summary)
            VALUES (?, ?)
        """, (trains_json, delay_summary))


def get_latest_amtrak_status() -> dict | None:
    with conn() as c:
        row = c.execute("""
            SELECT * FROM amtrak_status ORDER BY fetched_at DESC LIMIT 1
        """).fetchone()
        return dict(row) if row else None


# ── Ops plan helpers ──────────────────────────────────────────────────────────

def upsert_ops_plan(plan_date: str, raw_json: str, trip_count: int) -> None:
    with conn() as c:
        c.execute("""
            INSERT INTO ops_plan (plan_date, raw_json, trip_count)
            VALUES (?, ?, ?)
        """, (plan_date, raw_json, trip_count))


def get_ops_plan(plan_date: str | None = None) -> dict | None:
    with conn() as c:
        if plan_date:
            row = c.execute("""
                SELECT * FROM ops_plan WHERE plan_date=?
                ORDER BY loaded_at DESC LIMIT 1
            """, (plan_date,)).fetchone()
        else:
            row = c.execute("""
                SELECT * FROM ops_plan ORDER BY loaded_at DESC LIMIT 1
            """).fetchone()
        return dict(row) if row else None


# ── Runsheet + Watchlist schema ────────────────────────────────────────────────

SCHEMA_V3 = """
-- Daily runsheet (scheduled trips for a given calendar day)
CREATE TABLE IF NOT EXISTS runsheet (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date        TEXT NOT NULL,      -- YYYY-MM-DD
    scheduled_trips TEXT,               -- JSON array of trip objects
    trip_count      INTEGER DEFAULT 0,
    loaded_at       REAL DEFAULT (unixepoch())
);

-- Watchlist sessions — flight, train, or custom subject monitoring
-- Active sessions are polled each cycle; terminated sessions write summary to runsheet
CREATE TABLE IF NOT EXISTS watchlist_sessions (
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

-- ATCSCC daily ops plan snapshot (kept indefinitely — pattern analysis)
CREATE TABLE IF NOT EXISTS atcscc_opsplan (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_date       TEXT NOT NULL,      -- YYYY-MM-DD
    nas_programs    TEXT,               -- JSON — GDP/GS/AAR snapshot for the day
    notam_count     INTEGER DEFAULT 0,
    active_airports TEXT,               -- JSON array of affected airports
    pattern_tags    TEXT,               -- JSON array: ['weather-gdp','volume-delay',...]
    weather_summary TEXT,               -- Brief METAR summary at time of snapshot
    fetched_at      REAL DEFAULT (unixepoch())
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_atcscc_opsplan_date
    ON atcscc_opsplan(plan_date);
"""


def init_db_v3() -> None:
    """Apply v3 schema. Called at startup alongside v1 and v2."""
    with conn() as c:
        c.executescript(SCHEMA_V3)


# ── Runsheet helpers ──────────────────────────────────────────────────────────

def upsert_runsheet(run_date: str, scheduled_trips: list,
                    trip_count: int) -> None:
    with conn() as c:
        c.execute("""
            INSERT INTO runsheet (run_date, scheduled_trips, trip_count)
            VALUES (?, ?, ?)
        """, (run_date, json.dumps(scheduled_trips), trip_count))


def get_runsheet(run_date: str | None = None) -> dict | None:
    with conn() as c:
        if run_date:
            row = c.execute("""
                SELECT * FROM runsheet WHERE run_date=?
                ORDER BY loaded_at DESC LIMIT 1
            """, (run_date,)).fetchone()
        else:
            row = c.execute("""
                SELECT * FROM runsheet ORDER BY run_date DESC, loaded_at DESC LIMIT 1
            """).fetchone()
        return dict(row) if row else None


# ── Watchlist session helpers ──────────────────────────────────────────────────

def create_watchlist_session(session_id: str, session_type: str,
                             subject: str, run_date: str) -> None:
    with conn() as c:
        c.execute("""
            INSERT INTO watchlist_sessions (id, session_type, subject, run_date)
            VALUES (?, ?, ?, ?)
        """, (session_id, session_type, subject, run_date))


def update_watchlist_session_data(session_id: str,
                                  session_data: dict) -> None:
    with conn() as c:
        c.execute("""
            UPDATE watchlist_sessions SET session_data=? WHERE id=?
        """, (json.dumps(session_data), session_id))


def terminate_watchlist_session(session_id: str,
                                terminal_summary: str) -> None:
    with conn() as c:
        c.execute("""
            UPDATE watchlist_sessions
            SET status='terminated',
                terminated_at=unixepoch(),
                terminal_summary=?
            WHERE id=?
        """, (terminal_summary, session_id))


def get_active_watchlists(run_date: str | None = None) -> list[dict]:
    with conn() as c:
        if run_date:
            rows = c.execute("""
                SELECT * FROM watchlist_sessions
                WHERE status='active' AND run_date=?
                ORDER BY started_at DESC
            """, (run_date,)).fetchall()
        else:
            rows = c.execute("""
                SELECT * FROM watchlist_sessions
                WHERE status='active'
                ORDER BY started_at DESC
            """).fetchall()
        return [dict(r) for r in rows]


def get_watchlist_session(session_id: str) -> dict | None:
    with conn() as c:
        row = c.execute(
            "SELECT * FROM watchlist_sessions WHERE id=?", (session_id,)
        ).fetchone()
        return dict(row) if row else None


def get_terminated_watchlists(run_date: str) -> list[dict]:
    with conn() as c:
        rows = c.execute("""
            SELECT * FROM watchlist_sessions
            WHERE status='terminated' AND run_date=?
            ORDER BY terminated_at DESC
        """, (run_date,)).fetchall()
        return [dict(r) for r in rows]


# ── ATCSCC ops plan helpers ───────────────────────────────────────────────────

def upsert_atcscc_opsplan(plan_date: str, nas_programs: list,
                          notam_count: int, active_airports: list,
                          pattern_tags: list,
                          weather_summary: str) -> None:
    with conn() as c:
        c.execute("""
            INSERT INTO atcscc_opsplan
                (plan_date, nas_programs, notam_count, active_airports,
                 pattern_tags, weather_summary)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(plan_date) DO UPDATE SET
                nas_programs=excluded.nas_programs,
                notam_count=excluded.notam_count,
                active_airports=excluded.active_airports,
                pattern_tags=excluded.pattern_tags,
                weather_summary=excluded.weather_summary,
                fetched_at=unixepoch()
        """, (plan_date, json.dumps(nas_programs), notam_count,
              json.dumps(active_airports), json.dumps(pattern_tags),
              weather_summary))


def get_atcscc_opsplan(plan_date: str | None = None) -> dict | None:
    with conn() as c:
        if plan_date:
            row = c.execute("""
                SELECT * FROM atcscc_opsplan WHERE plan_date=?
            """, (plan_date,)).fetchone()
        else:
            row = c.execute("""
                SELECT * FROM atcscc_opsplan ORDER BY plan_date DESC LIMIT 1
            """).fetchone()
        return dict(row) if row else None


def get_atcscc_opsplan_range(start_date: str,
                             end_date: str) -> list[dict]:
    with conn() as c:
        rows = c.execute("""
            SELECT * FROM atcscc_opsplan
            WHERE plan_date BETWEEN ? AND ?
            ORDER BY plan_date DESC
        """, (start_date, end_date)).fetchall()
        return [dict(r) for r in rows]


# ── Schema V4 — train and flight event tables ─────────────────────────────────

SCHEMA_V4 = """
-- US Train departures snapshot (findtrain.com / ustrains fetcher)
-- One row per train_id per fetch; latest fetch replaces previous rows.
CREATE TABLE IF NOT EXISTS ustrains_departures (
    train_id        TEXT NOT NULL,
    station_id      TEXT NOT NULL,
    destination     TEXT,
    scheduled       TEXT,           -- ISO-8601 departure time
    platform        TEXT,
    status          TEXT,           -- "On time", "15 min late", etc.
    fetched_at      REAL DEFAULT (unixepoch()),
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
    updated_at      REAL DEFAULT (unixepoch())
);
CREATE INDEX IF NOT EXISTS idx_flight_events_dest
    ON flight_events(destination);
CREATE INDEX IF NOT EXISTS idx_flight_events_origin
    ON flight_events(origin);
"""


def init_db_v4() -> None:
    """Apply v4 schema. Called at startup alongside v1/v2/v3."""
    with conn() as c:
        c.executescript(SCHEMA_V4)


# ── Schema V5 — NMS SWIM tracks, POTUS alerts, watchlist ─────────────────────

SCHEMA_V5 = """
-- ASDE-X surface movement tracks from SMES (STDDS)
CREATE TABLE IF NOT EXISTS surface_tracks (
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

-- Terminal radar tracks from TAIS (PCT TRACON via STDDS)
CREATE TABLE IF NOT EXISTS terminal_tracks (
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

-- SWIM POTUS/VIP hot alerts from FDPS Marine One detection
-- alert_type is PRIMARY KEY: INSERT OR REPLACE keeps only the latest per type.
CREATE TABLE IF NOT EXISTS swim_alerts (
    alert_type      TEXT PRIMARY KEY,
    payload         TEXT,           -- JSON
    expires_at      TEXT NOT NULL   -- ISO 8601
);

-- Active watchlist entries (permanent + transient, both live here)
CREATE TABLE IF NOT EXISTS watchlist_entries (
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
);
CREATE INDEX IF NOT EXISTS idx_watchlist_entries_type
    ON watchlist_entries(entry_type);
CREATE INDEX IF NOT EXISTS idx_watchlist_entries_ident
    ON watchlist_entries(identifier);

-- Event log: alert_fired, auto_expired, manual_removed, permanent_removed
CREATE TABLE IF NOT EXISTS watchlist_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id        TEXT NOT NULL,
    entry_type      TEXT NOT NULL,
    identifier      TEXT NOT NULL,
    event_type      TEXT NOT NULL,
    event_summary   TEXT,
    event_detail    TEXT,           -- JSON
    fired_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_watchlist_history_entry
    ON watchlist_history(entry_id);
"""


def init_db_v5() -> None:
    """Apply v5 schema. Called at startup alongside v1–v4."""
    with conn() as c:
        c.executescript(SCHEMA_V5)


# ── Surface track helpers ─────────────────────────────────────────────────────

def upsert_surface_track(track_id: str, airport: str, callsign: str | None,
                         squawk: str | None, aircraft_type: str | None,
                         target_type: str | None, latitude: float,
                         longitude: float, altitude_ft: float | None,
                         speed_kts: int | None, heading_deg: float | None,
                         eram_gufi: str | None, last_seen: str) -> None:
    with conn() as c:
        c.execute("""
            INSERT INTO surface_tracks
                (track_id, airport, callsign, squawk, aircraft_type, target_type,
                 latitude, longitude, altitude_ft, speed_kts, heading_deg,
                 eram_gufi, last_seen)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(airport, track_id) DO UPDATE SET
                callsign=excluded.callsign,
                squawk=excluded.squawk,
                aircraft_type=excluded.aircraft_type,
                target_type=excluded.target_type,
                latitude=excluded.latitude,
                longitude=excluded.longitude,
                altitude_ft=excluded.altitude_ft,
                speed_kts=excluded.speed_kts,
                heading_deg=excluded.heading_deg,
                eram_gufi=excluded.eram_gufi,
                last_seen=excluded.last_seen
        """, (track_id, airport, callsign, squawk, aircraft_type, target_type,
              latitude, longitude, altitude_ft, speed_kts, heading_deg,
              eram_gufi, last_seen))


def get_surface_tracks(airport: str | None = None) -> list[dict]:
    with conn() as c:
        if airport:
            rows = c.execute(
                "SELECT * FROM surface_tracks WHERE airport=? ORDER BY callsign",
                (airport,)
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM surface_tracks ORDER BY airport, callsign"
            ).fetchall()
        return [dict(r) for r in rows]


# ── Terminal track helpers ────────────────────────────────────────────────────

def upsert_terminal_track(track_id: str, facility: str, callsign: str | None,
                          squawk: str | None, mode_s: str | None,
                          latitude: float | None, longitude: float | None,
                          altitude_ft: float | None, ground_speed: int | None,
                          last_seen: str) -> None:
    with conn() as c:
        c.execute("""
            INSERT INTO terminal_tracks
                (track_id, facility, callsign, squawk, mode_s,
                 latitude, longitude, altitude_ft, ground_speed, last_seen)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(facility, track_id) DO UPDATE SET
                callsign=excluded.callsign,
                squawk=excluded.squawk,
                mode_s=excluded.mode_s,
                latitude=excluded.latitude,
                longitude=excluded.longitude,
                altitude_ft=excluded.altitude_ft,
                ground_speed=excluded.ground_speed,
                last_seen=excluded.last_seen
        """, (track_id, facility, callsign, squawk, mode_s,
              latitude, longitude, altitude_ft, ground_speed, last_seen))


# ── SWIM alert helpers ────────────────────────────────────────────────────────

def upsert_swim_alert(alert_type: str, payload: dict, expires_at: str) -> None:
    with conn() as c:
        c.execute("""
            INSERT INTO swim_alerts (alert_type, payload, expires_at)
            VALUES (?, ?, ?)
            ON CONFLICT(alert_type) DO UPDATE SET
                payload=excluded.payload,
                expires_at=excluded.expires_at
        """, (alert_type, json.dumps(payload), expires_at))


def get_active_swim_alerts() -> list[dict]:
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with conn() as c:
        rows = c.execute("""
            SELECT * FROM swim_alerts WHERE expires_at > ?
        """, (now_iso,)).fetchall()
        return [dict(r) for r in rows]


# ── Watchlist entry helpers ───────────────────────────────────────────────────

def upsert_watchlist_entry(entry: dict) -> None:
    with conn() as c:
        c.execute("""
            INSERT INTO watchlist_entries
                (id, entry_type, tier, identifier, origin, destination,
                 route_name, scheduled_departure, scheduled_arrival,
                 auto_remove_at, added_at, added_by, notes,
                 last_event_at, last_event_summary)
            VALUES (:id, :entry_type, :tier, :identifier, :origin,
                    :destination, :route_name, :scheduled_departure,
                    :scheduled_arrival, :auto_remove_at, :added_at,
                    :added_by, :notes, :last_event_at, :last_event_summary)
            ON CONFLICT(id) DO UPDATE SET
                identifier=excluded.identifier,
                origin=excluded.origin,
                destination=excluded.destination,
                route_name=excluded.route_name,
                scheduled_departure=excluded.scheduled_departure,
                scheduled_arrival=excluded.scheduled_arrival,
                auto_remove_at=excluded.auto_remove_at,
                notes=excluded.notes
        """, {
            "id": entry["id"],
            "entry_type": entry["entry_type"],
            "tier": entry["tier"],
            "identifier": entry["identifier"],
            "origin": entry.get("origin"),
            "destination": entry.get("destination"),
            "route_name": entry.get("route_name"),
            "scheduled_departure": entry.get("scheduled_departure"),
            "scheduled_arrival": entry.get("scheduled_arrival"),
            "auto_remove_at": entry.get("auto_remove_at"),
            "added_at": entry.get("added_at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())),
            "added_by": entry.get("added_by", "system"),
            "notes": entry.get("notes"),
            "last_event_at": entry.get("last_event_at"),
            "last_event_summary": entry.get("last_event_summary"),
        })


def get_watchlist_entries(entry_type: str | None = None,
                          tier: str | None = None) -> list[dict]:
    """Return active watchlist entries (not yet auto_remove_at expired)."""
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with conn() as c:
        base = """
            SELECT * FROM watchlist_entries
            WHERE (auto_remove_at IS NULL OR auto_remove_at > ?)
        """
        params: list = [now_iso]
        if entry_type:
            base += " AND entry_type=?"
            params.append(entry_type)
        if tier:
            base += " AND tier=?"
            params.append(tier)
        base += " ORDER BY added_at DESC"
        rows = c.execute(base, params).fetchall()
        return [dict(r) for r in rows]


def delete_watchlist_entry(entry_id: str) -> dict | None:
    with conn() as c:
        row = c.execute(
            "SELECT * FROM watchlist_entries WHERE id=?", (entry_id,)
        ).fetchone()
        if not row:
            return None
        entry = dict(row)
        c.execute("DELETE FROM watchlist_entries WHERE id=?", (entry_id,))
        return entry


def update_watchlist_last_event(entry_id: str, summary: str,
                                event_at: str) -> None:
    with conn() as c:
        c.execute("""
            UPDATE watchlist_entries
            SET last_event_at=?, last_event_summary=?
            WHERE id=?
        """, (event_at, summary, entry_id))


def sweep_expired_watchlist_entries() -> list[dict]:
    """Remove transient entries past auto_remove_at. Returns removed entries."""
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with conn() as c:
        rows = c.execute("""
            SELECT * FROM watchlist_entries
            WHERE tier='transient' AND auto_remove_at IS NOT NULL
              AND auto_remove_at <= ?
        """, (now_iso,)).fetchall()
        expired = [dict(r) for r in rows]
        if expired:
            ids = [e["id"] for e in expired]
            c.execute(
                f"DELETE FROM watchlist_entries WHERE id IN ({','.join('?'*len(ids))})",
                ids
            )
        return expired


# ── Watchlist history helpers ─────────────────────────────────────────────────

def insert_watchlist_history(entry_id: str, entry_type: str, identifier: str,
                             event_type: str, event_summary: str | None,
                             event_detail: dict | None, fired_at: str) -> None:
    with conn() as c:
        c.execute("""
            INSERT INTO watchlist_history
                (entry_id, entry_type, identifier, event_type,
                 event_summary, event_detail, fired_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (entry_id, entry_type, identifier, event_type, event_summary,
              json.dumps(event_detail) if event_detail else None, fired_at))


def get_watchlist_history(entry_id: str | None = None,
                          limit: int = 50) -> list[dict]:
    with conn() as c:
        if entry_id:
            rows = c.execute("""
                SELECT * FROM watchlist_history
                WHERE entry_id=?
                ORDER BY fired_at DESC LIMIT ?
            """, (entry_id, limit)).fetchall()
        else:
            rows = c.execute("""
                SELECT * FROM watchlist_history
                ORDER BY fired_at DESC LIMIT ?
            """, (limit,)).fetchall()
        return [dict(r) for r in rows]


def get_watchlist_history_unfired(max_age_seconds: int = 900) -> list[dict]:
    cutoff = time.time() - max_age_seconds
    cutoff_iso = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(cutoff))
    with conn() as c:
        rows = c.execute("""
            SELECT * FROM watchlist_history
            WHERE ntfy_fired=0 AND fired_at >= ?
            ORDER BY fired_at ASC
        """, (cutoff_iso,)).fetchall()
        return [dict(r) for r in rows]


def mark_watchlist_history_fired(row_id: int) -> None:
    with conn() as c:
        c.execute("UPDATE watchlist_history SET ntfy_fired=1 WHERE id=?", (row_id,))


# ── UStrains departure helpers ────────────────────────────────────────────────

def upsert_ustrains_departure(train_id: str, station_id: str,
                              destination: str | None, scheduled: str | None,
                              platform: str | None, status: str | None,
                              fetched_at: float) -> None:
    with conn() as c:
        c.execute("""
            INSERT INTO ustrains_departures
                (train_id, station_id, destination, scheduled,
                 platform, status, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(train_id, station_id) DO UPDATE SET
                destination=excluded.destination,
                scheduled=excluded.scheduled,
                platform=excluded.platform,
                status=excluded.status,
                fetched_at=excluded.fetched_at
        """, (train_id, station_id, destination, scheduled,
              platform, status, fetched_at))


def get_ustrains_departures(station_id: str | None = None) -> list[dict]:
    """Return current departure snapshot, optionally filtered by station."""
    with conn() as c:
        if station_id:
            rows = c.execute("""
                SELECT * FROM ustrains_departures
                WHERE station_id=?
                ORDER BY scheduled ASC
            """, (station_id,)).fetchall()
        else:
            rows = c.execute("""
                SELECT * FROM ustrains_departures
                ORDER BY station_id, scheduled ASC
            """).fetchall()
        return [dict(r) for r in rows]


def clear_ustrains_departures(station_id: str) -> None:
    """Purge stale rows before re-inserting a fresh snapshot."""
    with conn() as c:
        c.execute("DELETE FROM ustrains_departures WHERE station_id=?",
                  (station_id,))


# ── Flight event helpers ──────────────────────────────────────────────────────

def upsert_flight_event(flight_id: str, airline: str | None,
                        flight_num: str | None, origin: str | None,
                        destination: str | None, aircraft_type: str | None,
                        departure_time: float | None, arrival_time: float | None,
                        status: str | None, position_lat: float | None,
                        position_lon: float | None, altitude_ft: int | None,
                        ground_speed_kt: int | None, raw_json: str) -> None:
    with conn() as c:
        c.execute("""
            INSERT INTO flight_events
                (flight_id, airline, flight_num, origin, destination,
                 aircraft_type, departure_time, arrival_time, status,
                 position_lat, position_lon, altitude_ft, ground_speed_kt,
                 raw_json, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, unixepoch())
            ON CONFLICT(flight_id) DO UPDATE SET
                airline=excluded.airline,
                flight_num=excluded.flight_num,
                origin=excluded.origin,
                destination=excluded.destination,
                aircraft_type=excluded.aircraft_type,
                departure_time=excluded.departure_time,
                arrival_time=excluded.arrival_time,
                status=excluded.status,
                position_lat=excluded.position_lat,
                position_lon=excluded.position_lon,
                altitude_ft=excluded.altitude_ft,
                ground_speed_kt=excluded.ground_speed_kt,
                raw_json=excluded.raw_json,
                updated_at=unixepoch()
        """, (flight_id, airline, flight_num, origin, destination,
              aircraft_type, departure_time, arrival_time, status,
              position_lat, position_lon, altitude_ft, ground_speed_kt,
              raw_json))


SCHEMA_V6 = """
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
"""


def init_db_v6() -> None:
    """Apply v6 schema. Called at startup alongside v1–v5."""
    with conn() as c:
        c.executescript(SCHEMA_V6)


def upsert_tbfm_sequence(meter_fix: str, facility: str, flight_id: str,
                         eta: str, sequence_num: int | None,
                         assigned_speed: int | None) -> None:
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    with conn() as c:
        c.execute("""
            INSERT INTO tbfm_sequences
                (meter_fix, facility, flight_id, eta, sequence_num, assigned_speed, last_seen)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(meter_fix, flight_id) DO UPDATE SET
                facility=excluded.facility,
                eta=excluded.eta,
                sequence_num=excluded.sequence_num,
                assigned_speed=excluded.assigned_speed,
                last_seen=excluded.last_seen
        """, (meter_fix, facility, flight_id, eta, sequence_num, assigned_speed, now))


SCHEMA_V7 = """
-- Aircraft seen by local UltraFeeder ADS-B receiver.
-- One row per ICAO hex, updated in-place on each position report.
CREATE TABLE IF NOT EXISTS local_aircraft (
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

-- ACARS messages decoded by acarsdec and routed through acarsrouter.
CREATE TABLE IF NOT EXISTS acars_messages (
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

-- Local airspace proximity and emergency alerts (separate from watchlist_history).
CREATE TABLE IF NOT EXISTS local_airspace_alerts (
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
"""


def init_db_v7() -> None:
    """Apply v7 schema (local ADS-B + ACARS tables). Called at startup."""
    with conn() as c:
        c.executescript(SCHEMA_V7)


SCHEMA_V8 = """
ALTER TABLE watchlist_history ADD COLUMN ntfy_fired   INTEGER DEFAULT 1;
ALTER TABLE watchlist_history ADD COLUMN ntfy_priority INTEGER DEFAULT 3;
"""


def init_db_v8() -> None:
    """Apply v8 schema — adds ntfy_fired/ntfy_priority to watchlist_history."""
    with conn() as c:
        for stmt in SCHEMA_V8.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                try:
                    c.execute(stmt)
                except Exception:
                    pass  # column already exists on subsequent startups


def upsert_local_aircraft(icao_hex: str, callsign: str | None,
                          registration: str | None, aircraft_type: str | None,
                          latitude: float | None, longitude: float | None,
                          altitude_ft: int | None, ground_speed: int | None,
                          track_deg: float | None, squawk: str | None,
                          on_ground: int, rssi: float | None,
                          distance_nm: float | None, last_seen: str,
                          source: str = "ultrafeeder") -> None:
    now = last_seen
    with conn() as c:
        c.execute("""
            INSERT INTO local_aircraft
                (icao_hex, callsign, registration, aircraft_type, latitude,
                 longitude, altitude_ft, ground_speed, track_deg, squawk,
                 on_ground, rssi, distance_nm, last_seen, first_seen, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(icao_hex) DO UPDATE SET
                callsign=excluded.callsign,
                registration=excluded.registration,
                aircraft_type=excluded.aircraft_type,
                latitude=excluded.latitude,
                longitude=excluded.longitude,
                altitude_ft=excluded.altitude_ft,
                ground_speed=excluded.ground_speed,
                track_deg=excluded.track_deg,
                squawk=excluded.squawk,
                on_ground=excluded.on_ground,
                rssi=excluded.rssi,
                distance_nm=excluded.distance_nm,
                last_seen=excluded.last_seen,
                source=excluded.source
        """, (icao_hex, callsign, registration, aircraft_type, latitude,
              longitude, altitude_ft, ground_speed, track_deg, squawk,
              on_ground, rssi, distance_nm, last_seen, now, source))


def get_local_aircraft(max_age_seconds: int = 120) -> list[dict]:
    """Return aircraft seen within max_age_seconds."""
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=max_age_seconds)
              ).strftime("%Y-%m-%dT%H:%M:%SZ")
    with conn() as c:
        rows = c.execute("""
            SELECT * FROM local_aircraft
            WHERE last_seen >= ?
            ORDER BY distance_nm ASC
        """, (cutoff,)).fetchall()
        return [dict(r) for r in rows]


def insert_acars_message(received_at: str, freq_mhz: float | None,
                         icao_hex: str | None, tail: str | None,
                         flight: str | None, msg_type: str | None,
                         label: str | None, block_id: str | None,
                         ack: str | None, mode: str | None,
                         msg_text: str | None, raw: str | None,
                         watchlist_hit: int = 0,
                         watchlist_entry_id: str | None = None) -> None:
    with conn() as c:
        c.execute("""
            INSERT INTO acars_messages
                (received_at, freq_mhz, icao_hex, tail, flight, msg_type,
                 label, block_id, ack, mode, msg_text, raw,
                 watchlist_hit, watchlist_entry_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (received_at, freq_mhz, icao_hex, tail, flight, msg_type,
              label, block_id, ack, mode, msg_text, raw,
              watchlist_hit, watchlist_entry_id))


def insert_local_airspace_alert(fired_at: str, alert_type: str,
                                icao_hex: str | None, callsign: str | None,
                                registration: str | None,
                                distance_nm: float | None,
                                altitude_ft: int | None, squawk: str | None,
                                watchlist_entry_id: str | None,
                                payload: dict | None,
                                ntfy_fired: int = 0) -> None:
    with conn() as c:
        c.execute("""
            INSERT INTO local_airspace_alerts
                (fired_at, alert_type, icao_hex, callsign, registration,
                 distance_nm, altitude_ft, squawk, watchlist_entry_id,
                 payload, ntfy_fired)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (fired_at, alert_type, icao_hex, callsign, registration,
              distance_nm, altitude_ft, squawk, watchlist_entry_id,
              json.dumps(payload) if payload else None, ntfy_fired))


def get_local_airspace_alerts_recent(entry_id: str, alert_type: str,
                                     max_age_seconds: int = 300) -> list[dict]:
    """Check if an alert fired recently (for deduplication)."""
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=max_age_seconds)
              ).strftime("%Y-%m-%dT%H:%M:%SZ")
    with conn() as c:
        rows = c.execute("""
            SELECT * FROM local_airspace_alerts
            WHERE watchlist_entry_id=? AND alert_type=? AND fired_at >= ?
        """, (entry_id, alert_type, cutoff)).fetchall()
        return [dict(r) for r in rows]


def upsert_itws_alert(airport: str, product_type: str, severity: int | None,
                      detail: str | None, valid_time: str,
                      expires_time: str | None, raw_json: str) -> None:
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    with conn() as c:
        c.execute("""
            INSERT INTO itws_alerts
                (airport, product_type, severity, detail, valid_time,
                 expires_time, raw_json, last_seen)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(airport, product_type) DO UPDATE SET
                severity=excluded.severity,
                detail=excluded.detail,
                valid_time=excluded.valid_time,
                expires_time=excluded.expires_time,
                raw_json=excluded.raw_json,
                last_seen=excluded.last_seen
        """, (airport, product_type, severity, detail, valid_time,
              expires_time, raw_json, now))


def get_active_itws_alerts(airport: str | None = None) -> list[dict]:
    with conn() as c:
        if airport:
            rows = c.execute(
                "SELECT * FROM itws_alerts WHERE airport=? ORDER BY valid_time DESC",
                (airport,)).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM itws_alerts ORDER BY airport, valid_time DESC"
            ).fetchall()
        return [dict(r) for r in rows]


def get_active_flight_events(airports: list[str] | None = None,
                             max_age_seconds: int = 3600) -> list[dict]:
    """Return flight events updated within max_age_seconds, optionally for given airports."""
    cutoff = time.time() - max_age_seconds
    with conn() as c:
        if airports:
            placeholders = ",".join("?" * len(airports))
            rows = c.execute(f"""
                SELECT * FROM flight_events
                WHERE updated_at > ?
                  AND (origin IN ({placeholders}) OR destination IN ({placeholders}))
                ORDER BY arrival_time ASC
            """, (cutoff, *airports, *airports)).fetchall()
        else:
            rows = c.execute("""
                SELECT * FROM flight_events
                WHERE updated_at > ?
                ORDER BY arrival_time ASC
            """, (cutoff,)).fetchall()
        return [dict(r) for r in rows]


# ── v9: brief_archive ─────────────────────────────────────────────────────────

SCHEMA_V9 = """
CREATE TABLE IF NOT EXISTS brief_archive (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    generated_at TEXT NOT NULL,          -- ISO-8601 UTC
    brief_type   TEXT NOT NULL DEFAULT 'ops',  -- 'ops' | 'daily'
    content      TEXT NOT NULL,
    source       TEXT NOT NULL DEFAULT 'skill'  -- 'skill' | 'manual'
);
CREATE INDEX IF NOT EXISTS idx_brief_archive_ts ON brief_archive (generated_at DESC);
"""


def init_db_v9() -> None:
    """Apply v9 schema — brief_archive table."""
    with conn() as c:
        c.executescript(SCHEMA_V9)


def archive_brief(content: str, brief_type: str = "ops",
                  source: str = "skill") -> None:
    """Store a brief in brief_archive. Called by ops_brief skill after write."""
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    with conn() as c:
        c.execute(
            "INSERT INTO brief_archive (generated_at, brief_type, content, source) VALUES (?,?,?,?)",
            (now, brief_type, content, source)
        )


def get_brief_history(limit: int = 7) -> list[dict]:
    """Return the last `limit` briefs, newest first."""
    with conn() as c:
        rows = c.execute(
            "SELECT id, generated_at, brief_type, source FROM brief_archive ORDER BY generated_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_brief_by_id(brief_id: int) -> dict | None:
    """Return a single archived brief by ID."""
    with conn() as c:
        row = c.execute(
            "SELECT * FROM brief_archive WHERE id=?", (brief_id,)
        ).fetchone()
        return dict(row) if row else None


# ── Schema V10 — OSINT scopes and items ───────────────────────────────────────

SCHEMA_V10 = """
CREATE TABLE IF NOT EXISTS osint_scopes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    label           TEXT    NOT NULL,
    scope_type      TEXT    NOT NULL DEFAULT 'keyword',
    query_terms     TEXT    NOT NULL,
    feed_urls       TEXT    NOT NULL DEFAULT '',
    push_threshold  TEXT    NOT NULL DEFAULT 'HIGH',
    enabled         INTEGER NOT NULL DEFAULT 1,
    created_at      REAL    NOT NULL
);

CREATE TABLE IF NOT EXISTS osint_items (
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
);
CREATE INDEX IF NOT EXISTS idx_osint_items_scope
    ON osint_items(scope_id);
CREATE INDEX IF NOT EXISTS idx_osint_items_score
    ON osint_items(score DESC);
CREATE INDEX IF NOT EXISTS idx_osint_items_ingested
    ON osint_items(ingested_at DESC);
"""


def init_db_v10() -> None:
    """Apply v10 schema — OSINT scopes and items."""
    with conn() as c:
        c.executescript(SCHEMA_V10)


# ── OSINT scope helpers ────────────────────────────────────────────────────────

def osint_add_scope(label: str, scope_type: str, query_terms: str,
                    feed_urls: str = "", push_threshold: str = "HIGH") -> int:
    """Create a new OSINT scope. Returns the new id."""
    import time as _time
    with conn() as c:
        cur = c.execute(
            """INSERT INTO osint_scopes
               (label, scope_type, query_terms, feed_urls, push_threshold, enabled, created_at)
               VALUES (?,?,?,?,?,1,?)""",
            (label, scope_type, query_terms, feed_urls, push_threshold, _time.time()),
        )
        return cur.lastrowid


def osint_get_scopes(enabled_only: bool = True) -> list[dict]:
    with conn() as c:
        c.row_factory = sqlite3.Row
        if enabled_only:
            rows = c.execute(
                "SELECT * FROM osint_scopes WHERE enabled=1 ORDER BY label"
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM osint_scopes ORDER BY label"
            ).fetchall()
    return [dict(r) for r in rows]


def osint_get_scope(scope_id: int) -> dict | None:
    with conn() as c:
        c.row_factory = sqlite3.Row
        row = c.execute("SELECT * FROM osint_scopes WHERE id=?", (scope_id,)).fetchone()
    return dict(row) if row else None


def osint_update_scope(scope_id: int, **kwargs) -> bool:
    """Update specific fields on a scope. Allowed: label, scope_type, query_terms,
    feed_urls, push_threshold, enabled."""
    allowed = {"label", "scope_type", "query_terms", "feed_urls", "push_threshold", "enabled"}
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return False
    set_clause = ", ".join(f"{k}=?" for k in updates)
    with conn() as c:
        c.execute(f"UPDATE osint_scopes SET {set_clause} WHERE id=?",
                  (*updates.values(), scope_id))
    return True


def osint_delete_scope(scope_id: int) -> bool:
    with conn() as c:
        c.execute("DELETE FROM osint_scopes WHERE id=?", (scope_id,))
    return True


# ── OSINT item helpers ─────────────────────────────────────────────────────────

def osint_save_item(scope_id: int, title: str, url: str, source_name: str | None,
                    published_at: float | None, score: int, score_label: str,
                    narrative: str | None, content_hash: str) -> bool:
    """
    Persist one scored OSINT item. Returns True if new, False if already exists.
    Uses INSERT OR IGNORE so duplicate content_hash is a silent no-op.
    """
    import time as _time
    with conn() as c:
        cur = c.execute(
            """INSERT OR IGNORE INTO osint_items
               (scope_id, title, url, source_name, published_at,
                ingested_at, score, score_label, narrative, content_hash)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (scope_id, title, url, source_name, published_at,
             _time.time(), score, score_label, narrative, content_hash),
        )
        return cur.rowcount > 0


def osint_get_feed(scope_id: int | None = None, min_score: int = 0,
                   limit: int = 50) -> list[dict]:
    """Return recent OSINT items, newest first. Optionally filtered by scope."""
    with conn() as c:
        c.row_factory = sqlite3.Row
        if scope_id is not None:
            rows = c.execute(
                """SELECT i.*, s.label AS scope_label
                   FROM osint_items i JOIN osint_scopes s ON s.id=i.scope_id
                   WHERE i.scope_id=? AND i.score>=?
                   ORDER BY i.ingested_at DESC LIMIT ?""",
                (scope_id, min_score, limit),
            ).fetchall()
        else:
            rows = c.execute(
                """SELECT i.*, s.label AS scope_label
                   FROM osint_items i JOIN osint_scopes s ON s.id=i.scope_id
                   WHERE i.score>=?
                   ORDER BY i.ingested_at DESC LIMIT ?""",
                (min_score, limit),
            ).fetchall()
    return [dict(r) for r in rows]


def osint_get_unpushed(min_score: int = 7) -> list[dict]:
    """Items that have never been pushed and meet the score threshold."""
    with conn() as c:
        c.row_factory = sqlite3.Row
        rows = c.execute(
            """SELECT i.*, s.label AS scope_label, s.push_threshold
               FROM osint_items i JOIN osint_scopes s ON s.id=i.scope_id
               WHERE i.pushed_at IS NULL
                 AND i.score >= ?
                 AND s.enabled = 1
               ORDER BY i.score DESC, i.ingested_at DESC""",
            (min_score,),
        ).fetchall()
    return [dict(r) for r in rows]


def osint_mark_pushed(item_id: int) -> None:
    import time as _time
    with conn() as c:
        c.execute("UPDATE osint_items SET pushed_at=? WHERE id=?",
                  (_time.time(), item_id))


def osint_prune_items(max_age_days: int = 30) -> int:
    """Delete items older than max_age_days. Returns count deleted."""
    import time as _time
    cutoff = _time.time() - (max_age_days * 86400)
    with conn() as c:
        cur = c.execute("DELETE FROM osint_items WHERE ingested_at < ?", (cutoff,))
    return cur.rowcount
