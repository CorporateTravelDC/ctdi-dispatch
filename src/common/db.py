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
    vip_flags       TEXT,           -- JSON array of VIP callsigns matched
    source          TEXT DEFAULT 'route'  -- 'route' | 'flight' | 'train'
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


# ── Pull-path connectivity verification (belt-and-suspenders) ─────────────────
# Populated by poller/skills/pull_path_verify.py: a periodic lightweight probe
# of each PULL-capable feed source's endpoint, INDEPENDENT of push freshness.
# feed_state answers "is data arriving?"; this answers "if the push feed died,
# would the pull fallback path still actually work?". Surfaced in /api/v1/feeds
# as pull_verified so both dimensions are visible ("push: covered, pull: verified").

def _ensure_pull_path_status(c) -> None:
    c.execute(
        """CREATE TABLE IF NOT EXISTS pull_path_status (
            feed_name   TEXT PRIMARY KEY,
            checked_at  REAL,
            ok          INTEGER,   -- 1 = pull path viable, 0 = failed
            state       TEXT,      -- verified|auth_gated|rate_limited|degraded|failed
            http_code   INTEGER,
            latency_ms  INTEGER,
            detail      TEXT
        )"""
    )


def upsert_pull_path_status(feed_name: str, checked_at: float, ok: bool | None, state: str,
                            http_code: int | None, latency_ms: int | None,
                            detail: str | None) -> None:
    # ok is tri-state: True (viable) / False (broken) / None (unconfigured --
    # never a live fallback; stored NULL so it reads distinct from a failure).
    ok_val = None if ok is None else (1 if ok else 0)
    with conn() as c:
        _ensure_pull_path_status(c)
        c.execute(
            """INSERT INTO pull_path_status
                 (feed_name, checked_at, ok, state, http_code, latency_ms, detail)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(feed_name) DO UPDATE SET
                 checked_at=excluded.checked_at, ok=excluded.ok, state=excluded.state,
                 http_code=excluded.http_code, latency_ms=excluded.latency_ms,
                 detail=excluded.detail""",
            (feed_name, checked_at, ok_val, state, http_code, latency_ms, detail),
        )


def get_pull_path_status() -> dict:
    with conn() as c:
        _ensure_pull_path_status(c)
        rows = c.execute("SELECT * FROM pull_path_status").fetchall()
        return {r["feed_name"]: dict(r) for r in rows}


# ── Cowork<->Dispatch message board (added 2026-08-07) ───────────────────────
# Append-only, Tier-0/tunnel-reachable coordination log between the off-box
# Cowork session and the Pi dispatch side. Writes are gated by X-Board-Key at
# the web layer (NOT the Bearer/tier system -- the Cloudflare tunnel strips
# Authorization). `seq` is the monotonic cursor. See web/main.py board endpoints
# and the build contract. NEVER store CUI/credentialed/movement data here --
# the scrub gate runs on every POST at the web layer.

def _ensure_board(c) -> None:
    c.execute(
        """CREATE TABLE IF NOT EXISTS board_messages (
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
        )"""
    )
    c.execute("CREATE INDEX IF NOT EXISTS idx_board_thread_seq ON board_messages(thread, seq)")


def _board_row_to_msg(r) -> dict:
    try:
        refs = json.loads(r["refs"]) if r["refs"] else []
    except Exception:
        refs = []
    return {
        "id": r["id"], "ts": r["ts"], "seq": r["seq"],
        "from": r["from_side"], "to": r["to_side"], "thread": r["thread"],
        "subject": r["subject"], "body": r["body"],
        "refs": refs, "in_reply_to": r["in_reply_to"],
    }


def board_insert(from_side: str, to_side: str, thread: str, subject: str,
                 body: str, refs: list | None = None, in_reply_to: str | None = None,
                 remote_addr: str | None = None) -> dict:
    import uuid as _uuid
    mid = "brd-" + _uuid.uuid4().hex[:12]
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with conn() as c:
        _ensure_board(c)
        cur = c.execute(
            """INSERT INTO board_messages
                 (id, ts, from_side, to_side, thread, subject, body, refs, in_reply_to, remote_addr)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (mid, ts, from_side, to_side, thread, subject, body,
             json.dumps(refs or []), in_reply_to, remote_addr),
        )
        return {"id": mid, "ts": ts, "seq": cur.lastrowid}


def board_query(thread: str = "coord", since: str | None = None, limit: int = 50) -> tuple[list, str]:
    """Return (messages, cursor). Messages are seq-ordered ascending, only those
    newer than `since` (a numeric seq cursor, or an ISO ts). cursor is the max
    seq returned (opaque string) for the caller to pass back as `since`."""
    limit = max(1, min(int(limit or 50), 200))
    clauses = ["thread = ?"]
    params: list = [thread]
    if since:
        s = str(since).strip()
        if s.isdigit():
            clauses.append("seq > ?"); params.append(int(s))
        else:
            clauses.append("ts > ?"); params.append(s)
    with conn() as c:
        _ensure_board(c)
        rows = c.execute(
            f"SELECT * FROM board_messages WHERE {' AND '.join(clauses)} ORDER BY seq ASC LIMIT ?",
            (*params, limit),
        ).fetchall()
    msgs = [_board_row_to_msg(r) for r in rows]
    cursor = str(msgs[-1]["seq"]) if msgs else (str(since) if since else "0")
    return msgs, cursor


def board_threads() -> list[dict]:
    with conn() as c:
        _ensure_board(c)
        rows = c.execute(
            "SELECT thread, MAX(ts) AS last_ts, COUNT(*) AS n FROM board_messages GROUP BY thread ORDER BY last_ts DESC"
        ).fetchall()
        return [{"thread": r["thread"], "last_activity": r["last_ts"], "count": r["n"]} for r in rows]


# ── Board write-auth: one-time enrollment nonce -> short-lived board-write token
# (added 2026-08-07). A session obtains board-write access by consuming a
# single-use nonce (handed out-of-band as an enroll URL) exactly once; that
# mints a short-lived token scoped to board-write only. Both nonce and token are
# stored HASHED at rest (sha256) -- a DB read never yields a usable secret. The
# minted token is preferred over handing out the long-lived BOARD_KEY: smaller
# blast radius, and a leak self-heals when it expires.
_BOARD_TOKEN_TTL_S = 7 * 86400   # minted board-write tokens live 7 days


def _board_sha(s: str) -> str:
    import hashlib
    return hashlib.sha256((s or "").encode()).hexdigest()


def _ensure_board_auth(c) -> None:
    c.execute(
        """CREATE TABLE IF NOT EXISTS board_enroll_nonces (
            nonce_hash        TEXT PRIMARY KEY,
            created_at        REAL,
            expires_at        REAL,
            consumed_at       REAL,
            minted_token_hash TEXT,
            label             TEXT
        )"""
    )
    c.execute(
        """CREATE TABLE IF NOT EXISTS board_tokens (
            token_hash  TEXT PRIMARY KEY,
            created_at  REAL,
            expires_at  REAL,
            scope       TEXT,
            label       TEXT,
            via_nonce   TEXT
        )"""
    )


def board_mint_nonce(ttl_s: int = 600, label: str | None = None) -> dict:
    """Create a single-use enrollment nonce (default 10min TTL). Returns the
    PLAINTEXT nonce (only stored hashed) for embedding in the enroll URL."""
    import secrets as _s
    nonce = "bnc_" + _s.token_urlsafe(24)
    now = time.time()
    with conn() as c:
        _ensure_board_auth(c)
        c.execute(
            "INSERT INTO board_enroll_nonces (nonce_hash, created_at, expires_at, label) VALUES (?, ?, ?, ?)",
            (_board_sha(nonce), now, now + ttl_s, label),
        )
    return {"nonce": nonce, "expires_at": now + ttl_s, "ttl_s": ttl_s}


def board_consume_nonce(nonce: str) -> dict:
    """Consume a nonce exactly once. On success mints a short-lived board-write
    token and returns it PLAINTEXT (stored hashed). status is one of:
    ok | invalid | consumed | expired."""
    import secrets as _s
    now = time.time()
    nh = _board_sha(nonce)
    with conn() as c:
        _ensure_board_auth(c)
        row = c.execute("SELECT * FROM board_enroll_nonces WHERE nonce_hash=?", (nh,)).fetchone()
        if row is None:
            return {"status": "invalid"}
        if row["consumed_at"] is not None:
            return {"status": "consumed"}
        if now > row["expires_at"]:
            return {"status": "expired"}
        token = "btk_" + _s.token_urlsafe(30)
        texp = now + _BOARD_TOKEN_TTL_S
        c.execute(
            "INSERT INTO board_tokens (token_hash, created_at, expires_at, scope, label, via_nonce) VALUES (?, ?, ?, ?, ?, ?)",
            (_board_sha(token), now, texp, "board-write", row["label"], nh[:12]),
        )
        c.execute(
            "UPDATE board_enroll_nonces SET consumed_at=?, minted_token_hash=? WHERE nonce_hash=?",
            (now, _board_sha(token), nh),
        )
    return {"status": "ok", "token": token, "expires_at": texp, "scope": "board-write"}


def board_token_valid(presented: str) -> bool:
    """True if `presented` is a minted board-write token that hasn't expired.

    SCOPE-BLIND BY DESIGN (2026-08-07): every minted token today is
    scope="board-write" (see board_consume_nonce), so this deliberately does
    NOT filter on scope -- existence + expiry is sufficient.

    !! FOOTGUN GUARD: if you ever add a SECOND token scope (board-read,
    board-admin, a different resource, etc.), you MUST make this check
    scope-aware AT THE SAME TIME -- e.g. board_token_valid(presented,
    required_scope) filtering on `scope` -- and update the POST /api/v1/board
    caller to pass the scope it requires. As written, a token minted under ANY
    scope string still passes this check, so a second scope added alone would
    silently grant board-write to tokens that were meant to be restricted.
    Adding a second scope without fixing this is a privilege-escalation bug.
    """
    if not presented:
        return False
    now = time.time()
    with conn() as c:
        _ensure_board_auth(c)
        # NOTE: no `AND scope=?` here on purpose -- see the scope-blind guard in
        # this function's docstring before adding one / adding a second scope.
        row = c.execute("SELECT expires_at FROM board_tokens WHERE token_hash=?", (_board_sha(presented),)).fetchone()
        return bool(row and row["expires_at"] and row["expires_at"] > now)


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


def expire_tfrs(active_ids: list[str]) -> None:
    """Delete TFRs no longer present in the current FAA feed.
    The getTfrList endpoint is authoritative — any ID absent from the current
    response has been cancelled or expired upstream."""
    if not active_ids:
        return  # Safety: never wipe the table on an empty feed response
    placeholders = ",".join("?" * len(active_ids))
    with conn() as c:
        c.execute(
            f"DELETE FROM tfrs WHERE tfr_id NOT IN ({placeholders})",
            active_ids,
        )


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
                           vip_flags: list[str],
                           source: str = "route") -> None:
    """
    Write a narrative row to hot_alerts.

    source values:
      'route'   — ground-route impact (read by ops_brief ROUTE NARRATIVE section)
      'flight'  — flight-impact skill output (ops_brief ignores these)
      'train'   — train-impact skill output  (ops_brief ignores these)
    """
    with conn() as c:
        c.execute("""
            INSERT INTO hot_alerts (route_narrative, active_tfrs, vip_flags, source)
            VALUES (?, ?, ?, ?)
        """, (narrative, json.dumps(active_tfrs), json.dumps(vip_flags), source))


def get_latest_route_narrative(source: str = "route") -> dict | None:
    """Return the most recent hot_alerts row matching source (default 'route')."""
    with conn() as c:
        row = c.execute(
            "SELECT * FROM hot_alerts WHERE source = ? ORDER BY computed_at DESC LIMIT 1",
            (source,),
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


# ── Compliance egress (outbound audit push) ──────────────────────────────────
# 2026-08-03: corrects docs/COMPLIANCE_SECURITY.md, which previously (and
# inaccurately) stated no outbound audit-push mechanism existed in this
# codebase. It does now -- see common/compliance_egress.py for the actual
# push logic; these are just the DB-side tracking helpers. Ships disabled
# (COMPLIANCE_HOOK_ENABLED=false by default) -- see that module's docstring.

def get_unshipped_audit_events(limit: int = 50, retry_limit: int = 5) -> list[dict]:
    """Rows not yet successfully shipped and not yet permanently failed
    (egress_attempts < retry_limit), oldest first so a long backlog drains
    in order rather than the newest events starving older ones."""
    with conn() as c:
        rows = c.execute("""
            SELECT * FROM audit_log
            WHERE egress_status = 'pending' AND egress_attempts < ?
            ORDER BY event_time ASC LIMIT ?
        """, (retry_limit, limit)).fetchall()
        return [dict(r) for r in rows]


def mark_audit_shipped(audit_id: int) -> None:
    with conn() as c:
        c.execute(
            "UPDATE audit_log SET egress_status = 'shipped' WHERE id = ?",
            (audit_id,),
        )


def mark_audit_egress_failed(audit_id: int, error: str, retry_limit: int = 5) -> None:
    """Increment the attempt counter and record the error. Once attempts
    reaches retry_limit, marks the row failed_permanent so
    get_unshipped_audit_events() stops returning it -- a persistently
    unreachable target degrades to "stop trying, this record is stuck"
    rather than retrying forever every poll cycle."""
    with conn() as c:
        row = c.execute(
            "SELECT egress_attempts FROM audit_log WHERE id = ?", (audit_id,)
        ).fetchone()
        attempts = (row[0] if row else 0) + 1
        status = "failed_permanent" if attempts >= retry_limit else "pending"
        c.execute("""
            UPDATE audit_log
            SET egress_attempts = ?, egress_last_error = ?, egress_status = ?
            WHERE id = ?
        """, (attempts, error[:500], status, audit_id))


# ── Auth token helpers ────────────────────────────────────────────────────────

def insert_token(token_hash: str, token_prefix: str, user_label: str,
                 tier: str, device_label: str | None,
                 expires_at: float | None, department: str | None = None) -> None:
    with conn() as c:
        c.execute("""
            INSERT INTO auth_tokens
                (token_hash, token_prefix, user_label, tier, device_label, expires_at, department)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (token_hash, token_prefix, user_label, tier, device_label, expires_at, department))


def set_token_department(token_prefix: str, department: str | None) -> int:
    """Set (or clear, with department=None) the department for all active
    tokens matching this prefix. Returns count updated. Added 2026-08-02
    for the department/multi-operator feed visibility model -- see
    shared/rss_catalog.py."""
    with conn() as c:
        c.execute("""
            UPDATE auth_tokens SET department=?
            WHERE token_prefix LIKE ? AND revoked_at IS NULL
        """, (department, token_prefix + "%"))
        return c.execute("SELECT changes()").fetchone()[0]


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
                 effective_start, effective_end, text_body, last_seen_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, unixepoch())
            ON CONFLICT(notam_id) DO UPDATE SET
                raw_json=excluded.raw_json,
                facility=excluded.facility,
                classification=excluded.classification,
                effective_start=excluded.effective_start,
                effective_end=excluded.effective_end,
                text_body=excluded.text_body,
                last_seen_at=unixepoch()
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


def cleanup_expired_notams() -> int:
    """Delete NOTAMs whose effective_end has passed (1-hour grace period).
    Also prunes NULL-end NOTAMs not re-seen on the wire in 30 days (stale
    permanent-duration entries). last_seen_at refreshes on every upsert, so a
    NOTAM still being rebroadcast never ages out -- only ones that have
    actually dropped off the feed do. Falls back to inserted_at for rows
    written before the last_seen_at column existed.
    Returns the number of rows removed."""
    now = time.time()
    cutoff = now - 3600           # 1-hour grace window
    stale  = now - (30 * 86400)  # 30-day stale window for NULL-end entries
    with conn() as c:
        r1 = c.execute(
            "DELETE FROM notams WHERE effective_end IS NOT NULL AND effective_end < ?",
            (cutoff,),
        )
        r2 = c.execute(
            "DELETE FROM notams WHERE effective_end IS NULL "
            "AND COALESCE(last_seen_at, inserted_at) < ?",
            (stale,),
        )
        return (r1.rowcount or 0) + (r2.rowcount or 0)


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
        # 2026-07-28 (train_events parity with flight_events): also unpack
        # this same payload into structured rows so it becomes mineable the
        # same way flight_events is, instead of sitting as an opaque JSON
        # blob only ever read back whole. Best-effort -- a parse issue here
        # must never break the primary blob insert above, which every
        # existing caller (poller fetcher, ingest push path, amtrak_tracker)
        # depends on unconditionally succeeding.
        try:
            for row in _parse_trains_json_to_rows(trains_json):
                c.execute("""
                    INSERT INTO train_events
                    (train_number, train_name, route_name, direction,
                     origin, destination, station_code, station_name,
                     scheduled_time, estimated_time, status, delay_minutes, platform)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, row)
        except Exception:
            pass


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


SCHEMA_USAGE = """
CREATE TABLE IF NOT EXISTS feed_data_usage (
    feed_name        TEXT PRIMARY KEY,
    bytes_in         INTEGER DEFAULT 0,   -- raw bytes from source (pre-filter)
    records_in       INTEGER DEFAULT 0,   -- messages/records received
    records_accepted INTEGER DEFAULT 0,   -- records that passed filter and were stored
    window_start     REAL,               -- unix epoch when window opened (reset on restart)
    updated_at       REAL DEFAULT (unixepoch())
);
"""


def get_protected_flight_ids() -> set[str]:
    """flight_events.flight_id (GUFI) values that must be retained regardless
    of age because they belong to a flight currently on the active watchlist.

    2026-07-27: the original purge_old_flight_events compared flight_id (a
    GUFI/UUID) directly against watchlist identifiers (ICAO callsigns like
    "UAL2670") in a NOT IN clause -- the same class of mismatch as the
    original _check_flight_fdps_cache bug fixed earlier the same day. A GUFI
    is never equal to a callsign, so that exclusion was always a silent
    no-op. This version matches correctly by splitting each watched
    identifier into airline+flight_num (same split as
    get_flight_plan_by_callsign) and looking up the matching flight_events
    rows by those columns instead.
    """
    import re
    with conn() as c:
        idents: set[str] = {
            row[0] for row in c.execute(
                "SELECT identifier FROM watchlist_entries WHERE entry_type='flight'"
            ).fetchall()
        }
        idents |= {
            row[0] for row in c.execute(
                "SELECT subject FROM watchlist_sessions WHERE status='active'"
            ).fetchall()
        }
        protected: set[str] = set()
        for ident in idents:
            m = re.match(r"^([A-Za-z]{2,3})(\d+[A-Za-z]?)$", (ident or "").strip())
            if not m:
                continue
            airline, flight_num = m.group(1).upper(), m.group(2)
            rows = c.execute(
                "SELECT flight_id FROM flight_events WHERE airline=? AND flight_num=?",
                (airline, flight_num),
            ).fetchall()
            protected |= {r[0] for r in rows}
        return protected


def export_old_flight_events(cutoff_days: int = 30, limit: int = 1000) -> list[dict]:
    """Return up to `limit` flight_events rows older than cutoff_days that
    are NOT protected by an active watchlist entry (see
    get_protected_flight_ids), oldest first. Read-only -- does not delete
    anything. Pair with delete_flight_events_by_id() using this exact same
    row set, only after the export has been successfully archived
    elsewhere (see poller/skills/flight_events_cleanup.py).

    2026-07-27: bounded by `limit` after a live test with cutoff_days=0
    (deliberately matching the whole table) OOM-killed the poller
    container -- SELECT * over 220k+ rows of raw_json, fully materialized
    into Python dicts, blew past the container's 448m memory cap. The
    caller (flight_events_cleanup.run()) loops in batches of `limit`
    instead of pulling everything in one shot; a batch smaller than
    `limit` means there's nothing left to archive. The exclusion is done
    in SQL now (flight_id NOT IN protected-GUFIs) rather than as a
    Python-side post-filter, so LIMIT and the protected-set exclusion
    don't fight each other -- a short batch is always genuinely the end.
    """
    cutoff = time.time() - cutoff_days * 86400
    protected = get_protected_flight_ids()
    with conn() as c:
        if protected:
            placeholders = ",".join("?" * len(protected))
            rows = c.execute(
                f"SELECT * FROM flight_events WHERE updated_at < ? "
                f"AND flight_id NOT IN ({placeholders}) "
                f"ORDER BY updated_at ASC LIMIT ?",
                [cutoff] + list(protected) + [limit],
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM flight_events WHERE updated_at < ? "
                "ORDER BY updated_at ASC LIMIT ?",
                (cutoff, limit),
            ).fetchall()
        return [dict(r) for r in rows]


def delete_flight_events_by_id(flight_ids: list[str]) -> int:
    """Delete specific flight_events rows by flight_id (GUFI). Deletes
    exactly the row set passed in -- callers should pass the same list
    returned by export_old_flight_events(), not a freshly re-run query, so
    there's no gap between "what we archived" and "what we deleted".
    Returns the number of rows actually deleted."""
    if not flight_ids:
        return 0
    with conn() as c:
        placeholders = ",".join("?" * len(flight_ids))
        result = c.execute(
            f"DELETE FROM flight_events WHERE flight_id IN ({placeholders})",
            flight_ids,
        )
        return result.rowcount


def init_feed_usage(feed_name: str) -> None:
    """Open a fresh usage window for a feed (called at ingest/poller startup)."""
    import time as _time
    with conn() as c:
        c.execute("""
            INSERT INTO feed_data_usage
                (feed_name, bytes_in, records_in, records_accepted, window_start, updated_at)
            VALUES (?, 0, 0, 0, ?, unixepoch())
            ON CONFLICT(feed_name) DO UPDATE SET
                bytes_in=0, records_in=0, records_accepted=0,
                window_start=excluded.window_start, updated_at=unixepoch()
        """, (feed_name, _time.time()))


def record_feed_bytes(feed_name: str, bytes_in: int,
                      records_in: int = 1, records_accepted: int = 0) -> None:
    """Increment usage counters for a feed. Thread-safe via SQLite WAL."""
    with conn() as c:
        c.execute("""
            INSERT INTO feed_data_usage
                (feed_name, bytes_in, records_in, records_accepted,
                 window_start, updated_at)
            VALUES (?, ?, ?, ?, unixepoch(), unixepoch())
            ON CONFLICT(feed_name) DO UPDATE SET
                bytes_in = bytes_in + excluded.bytes_in,
                records_in = records_in + excluded.records_in,
                records_accepted = records_accepted + excluded.records_accepted,
                updated_at = unixepoch()
        """, (feed_name, bytes_in, records_in, records_accepted))


def get_feed_data_usage() -> list[dict]:
    """Return per-feed data usage snapshot."""
    with conn() as c:
        rows = c.execute("""
            SELECT feed_name, bytes_in, records_in, records_accepted,
                   window_start, updated_at
            FROM feed_data_usage
            ORDER BY bytes_in DESC
        """).fetchall()
    return [
        {
            "feed_name": r[0],
            "bytes_in": r[1],
            "records_in": r[2],
            "records_accepted": r[3],
            "records_dropped": max(0, r[2] - r[3]),
            "filter_pass_pct": round(100 * r[3] / r[2], 1) if r[2] else None,
            "window_start": r[4],
            "updated_at": r[5],
        }
        for r in rows
    ]


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
    # days_active arrives as a python list (or None) from callers -- encode
    # to JSON text since SQLite has no native array column type. Decoded
    # back to a list in get_watchlist_entries() below.
    days_active = entry.get("days_active")
    days_active_json = json.dumps(days_active) if days_active is not None else None

    show_national = entry.get("show_national")
    show_regional = entry.get("show_regional")

    with conn() as c:
        c.execute("""
            INSERT INTO watchlist_entries
                (id, entry_type, tier, identifier, origin, destination,
                 route_name, scheduled_departure, scheduled_arrival,
                 auto_remove_at, added_at, added_by, notes,
                 last_event_at, last_event_summary, hex_id, registration,
                 subsection, show_national, show_regional, days_active,
                 sister_flight)
            VALUES (:id, :entry_type, :tier, :identifier, :origin,
                    :destination, :route_name, :scheduled_departure,
                    :scheduled_arrival, :auto_remove_at, :added_at,
                    :added_by, :notes, :last_event_at, :last_event_summary,
                    :hex_id, :registration, :subsection, :show_national,
                    :show_regional, :days_active, :sister_flight)
            ON CONFLICT(id) DO UPDATE SET
                identifier=excluded.identifier,
                origin=excluded.origin,
                destination=excluded.destination,
                route_name=excluded.route_name,
                scheduled_departure=excluded.scheduled_departure,
                scheduled_arrival=excluded.scheduled_arrival,
                auto_remove_at=excluded.auto_remove_at,
                notes=excluded.notes,
                hex_id=COALESCE(excluded.hex_id, watchlist_entries.hex_id),
                registration=COALESCE(excluded.registration, watchlist_entries.registration),
                subsection=excluded.subsection,
                show_national=excluded.show_national,
                show_regional=excluded.show_regional,
                days_active=excluded.days_active,
                sister_flight=excluded.sister_flight
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
            "hex_id": (entry.get("hex_id") or "").lower().strip() or None,
            "registration": entry.get("registration"),
            "subsection": entry.get("subsection"),
            "show_national": None if show_national is None else int(bool(show_national)),
            "show_regional": None if show_regional is None else int(bool(show_regional)),
            "days_active": days_active_json,
            "sister_flight": entry.get("sister_flight"),
        })


def set_watchlist_identity(entry_id: str, hex_id: str | None = None,
                           registration: str | None = None) -> None:
    """Directly set/backfill hex_id and/or registration on an existing
    entry without touching any other field. Used by the one-time notes-hex
    backfill and by any future admin "confirm identity" action."""
    sets, params = [], {}
    if hex_id is not None:
        sets.append("hex_id=:hex_id")
        params["hex_id"] = hex_id.lower().strip() or None
    if registration is not None:
        sets.append("registration=:registration")
        params["registration"] = registration.upper().strip() or None
    if not sets:
        return
    params["id"] = entry_id
    with conn() as c:
        c.execute(f"UPDATE watchlist_entries SET {', '.join(sets)} WHERE id=:id", params)


def get_watchlist_entries(entry_type: str | None = None,
                          tier: str | None = None) -> list[dict]:
    """Return active watchlist entries (not yet auto_remove_at expired)."""
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with conn() as c:
        # FIX (2026-07-28): same datetime()-normalization fix as
        # sweep_expired_watchlist_entries() -- see that function's
        # docstring for the full root cause (raw TEXT comparison between
        # two different timestamp formats, space-vs-'T' separator sorting
        # incorrectly in ASCII).
        base = """
            SELECT * FROM watchlist_entries
            WHERE (auto_remove_at IS NULL OR datetime(auto_remove_at) > datetime(?))
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
        entries = [dict(r) for r in rows]
        for e in entries:
            # days_active is stored as JSON text (see upsert_watchlist_entry) --
            # decode back to a list for consumers (frontend roster panel,
            # day-pattern flight logic).
            raw_days = e.get("days_active")
            if raw_days:
                try:
                    e["days_active"] = json.loads(raw_days)
                except (TypeError, ValueError):
                    e["days_active"] = None
            if "show_national" in e and e["show_national"] is not None:
                e["show_national"] = bool(e["show_national"])
            if "show_regional" in e and e["show_regional"] is not None:
                e["show_regional"] = bool(e["show_regional"])
        return entries


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


def update_watchlist_fdps_confirmation(entry_id: str, status: str | None,
                                       updated_at: str) -> None:
    """Persist FDPS flight-plan status onto a watchlist entry at add-time.

    2026-07-28: added_at-time FDPS resolution was previously response-only
    (see web/routes/watchlist.py add_flight_watchlist's transient
    "fdps_confirmed" response block) -- it was never written to the
    last_fdps_status/last_fdps_updated_at columns, so a fresh GET on the
    same entry a minute later showed no trace of what FDPS said at add
    time. Operator request: "for FDPS departures with tail numbers let's
    always resolve it and then have a FDPS yes/no confirmation in the
    watchlist when it's added" -- this makes that confirmation durable,
    not just a one-shot response field.
    """
    with conn() as c:
        c.execute("""
            UPDATE watchlist_entries
            SET last_fdps_status=?, last_fdps_updated_at=?
            WHERE id=?
        """, (status, updated_at, entry_id))


def sweep_expired_watchlist_entries() -> list[dict]:
    """Remove transient entries past auto_remove_at. Returns removed entries.

    FIX (2026-07-28): auto_remove_at is stored in whatever format the caller
    supplied ("YYYY-MM-DD HH:MM:SS", space-separated, no zone) while now_iso
    here is ISO8601 ("YYYY-MM-DDTHH:MM:SSZ"). SQLite has no native datetime
    type, so this was a plain TEXT comparison -- and since ' ' (0x20) sorts
    before 'T' (0x54) in ASCII, any auto_remove_at on the *same calendar
    date* as now always compared as "less than" now_iso, REGARDLESS of the
    actual hour. Result: every same-day transient entry expired on the very
    next sweep tick after insertion (observed: two live client flights
    wiped within seconds/hours of being added, nowhere near their real
    auto_remove_at). Root-caused via direct DB inspection + operator
    noticing a "4 hours off" pattern in the push text (a real, separate
    UTC/local labeling issue -- see the watchlist add-flight route -- but
    NOT what was actually deleting these rows; that was this format
    mismatch, which fired within seconds regardless of hour).

    Fix: wrap both sides in SQLite's datetime() so the comparison happens
    on normalized values instead of raw text -- datetime() accepts both
    "YYYY-MM-DD HH:MM:SS" and "YYYY-MM-DDTHH:MM:SSZ" and returns the same
    canonical form for both, so a real chronological comparison happens
    instead of an accidental ASCII one.
    """
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with conn() as c:
        rows = c.execute("""
            SELECT * FROM watchlist_entries
            WHERE tier='transient' AND auto_remove_at IS NOT NULL
              AND datetime(auto_remove_at) <= datetime(?)
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


def enrich_flight_arrival_times(updates: list[tuple[float, str]]) -> int:
    """Bulk-set flight_events.arrival_time for existing rows, by flight_id
    (GUFI) primary key. `updates` is a list of (arrival_time, flight_id)
    tuples (executemany parameter order). Deliberately does NOT touch
    updated_at -- that column is what feed_db_integrity_check.py reads as
    "FDPS is actively writing"; bumping it here would make a silently-dead
    FDPS feed look alive just because enrichment is still running against
    old rows. Returns the number of rows actually updated (excludes
    flight_ids that no longer exist, e.g. aged out of the retention
    window between the enrichment query and this write)."""
    if not updates:
        return 0
    with conn() as c:
        cur = c.executemany(
            "UPDATE flight_events SET arrival_time = ? WHERE flight_id = ?",
            updates,
        )
        return cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0


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


def get_brief_history(limit: int = 7, brief_type: str | None = None) -> list[dict]:
    """Return the last `limit` briefs, newest first. Optional brief_type filter ('ops'|'weekly')."""
    with conn() as c:
        if brief_type:
            rows = c.execute(
                "SELECT id, generated_at, brief_type, source FROM brief_archive "
                "WHERE brief_type=? ORDER BY generated_at DESC LIMIT ?",
                (brief_type, limit)
            ).fetchall()
        else:
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
                    feed_urls: str = "", push_threshold: str = "HIGH",
                    event_name: str = "", audience: str = "", genre: str = "") -> int:
    """Create a new OSINT scope. Returns the new id.

    event_name/audience/genre (2026-08-12, SCHEMA_V32): optional structured
    metadata for scope_type="event" -- the specific named occurrence, who
    attends it, and what kind of event it is. Empty string (not NULL) when
    unused so existing callers/scope types are unaffected."""
    import time as _time
    with conn() as c:
        cur = c.execute(
            """INSERT INTO osint_scopes
               (label, scope_type, query_terms, feed_urls, push_threshold,
                event_name, audience, genre, enabled, created_at)
               VALUES (?,?,?,?,?,?,?,?,1,?)""",
            (label, scope_type, query_terms, feed_urls, push_threshold,
             event_name, audience, genre, _time.time()),
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
    feed_urls, push_threshold, enabled, event_name, audience, genre."""
    allowed = {"label", "scope_type", "query_terms", "feed_urls", "push_threshold",
               "enabled", "event_name", "audience", "genre"}
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
                    narrative: str | None, content_hash: str,
                    headline: str | None = None, outlet: str | None = None,
                    story_key: str | None = None) -> bool:
    """
    Persist one scored OSINT item. Returns True if new, False if already exists.
    Uses INSERT OR IGNORE so duplicate content_hash is a silent no-op.

    headline/outlet/story_key (2026-08-12, SCHEMA_V33): optional cross-outlet
    story-clustering fields -- see osint_monitor._split_headline_outlet /
    _story_key. Callers not aware of clustering can omit them.
    """
    import time as _time
    with conn() as c:
        cur = c.execute(
            """INSERT OR IGNORE INTO osint_items
               (scope_id, title, url, source_name, published_at,
                ingested_at, score, score_label, narrative, content_hash,
                headline, outlet, story_key)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (scope_id, title, url, source_name, published_at,
             _time.time(), score, score_label, narrative, content_hash,
             headline, outlet, story_key),
        )
        return cur.rowcount > 0


def osint_get_feed(scope_id: int | None = None, min_score: int = 0,
                   limit: int = 50) -> list[dict]:
    """Return recent OSINT items, newest first. Optionally filtered by scope."""
    with conn() as c:
        c.row_factory = sqlite3.Row
        if scope_id is not None:
            rows = c.execute(
                """SELECT i.*, s.label AS scope_label, s.scope_type AS scope_type
                   FROM osint_items i JOIN osint_scopes s ON s.id=i.scope_id
                   WHERE i.scope_id=? AND i.score>=?
                   ORDER BY i.ingested_at DESC LIMIT ?""",
                (scope_id, min_score, limit),
            ).fetchall()
        else:
            rows = c.execute(
                """SELECT i.*, s.label AS scope_label, s.scope_type AS scope_type
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


# ── FAA Aircraft Registry (V11) ────────────────────────────────────────────────

SCHEMA_V11 = """
CREATE TABLE IF NOT EXISTS faa_aircraft_registry (
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
CREATE INDEX IF NOT EXISTS idx_faa_reg_hex
    ON faa_aircraft_registry(mode_s_hex);
CREATE INDEX IF NOT EXISTS idx_faa_reg_status
    ON faa_aircraft_registry(status_code);

CREATE TABLE IF NOT EXISTS faa_ladd_aircraft (
    n_number        TEXT    PRIMARY KEY,
    updated_at      REAL    NOT NULL
);

CREATE TABLE IF NOT EXISTS faa_registry_meta (
    key             TEXT    PRIMARY KEY,
    value           TEXT    NOT NULL
);
"""


def init_db_v11() -> None:
    """Apply v11 schema — FAA aircraft registry + LADD list."""
    with conn() as c:
        c.executescript(SCHEMA_V11)


def faa_upsert_aircraft(records: list[dict]) -> int:
    """Bulk upsert FAA registry records. Returns count upserted."""
    import time as _time
    now = _time.time()
    sql = """
        INSERT INTO faa_aircraft_registry
            (n_number, mode_s_hex, serial_number, mfr_mdl_code, year_mfr,
             registrant_name, city, state, status_code, type_aircraft,
             type_engine, expiration_date, last_action_date, cert_issue_date,
             updated_at)
        VALUES
            (:n_number, :mode_s_hex, :serial_number, :mfr_mdl_code, :year_mfr,
             :registrant_name, :city, :state, :status_code, :type_aircraft,
             :type_engine, :expiration_date, :last_action_date, :cert_issue_date,
             :updated_at)
        ON CONFLICT(n_number) DO UPDATE SET
            mode_s_hex       = excluded.mode_s_hex,
            serial_number    = excluded.serial_number,
            mfr_mdl_code     = excluded.mfr_mdl_code,
            year_mfr         = excluded.year_mfr,
            registrant_name  = excluded.registrant_name,
            city             = excluded.city,
            state            = excluded.state,
            status_code      = excluded.status_code,
            type_aircraft    = excluded.type_aircraft,
            type_engine      = excluded.type_engine,
            expiration_date  = excluded.expiration_date,
            last_action_date = excluded.last_action_date,
            cert_issue_date  = excluded.cert_issue_date,
            updated_at       = excluded.updated_at
    """
    for r in records:
        r["updated_at"] = now
    with conn() as c:
        c.executemany(sql, records)
    return len(records)


def faa_upsert_ladd(n_numbers: list[str]) -> int:
    """Replace LADD list entirely. Returns final count."""
    import time as _time
    now = _time.time()
    with conn() as c:
        c.execute("DELETE FROM faa_ladd_aircraft")
        c.executemany(
            "INSERT OR REPLACE INTO faa_ladd_aircraft (n_number, updated_at) VALUES (?, ?)",
            [(n.strip().upper(), now) for n in n_numbers if n.strip()],
        )
    return len(n_numbers)


def faa_registry_sweep_removed(cutoff_epoch: float) -> int:
    """Delete rows not touched since cutoff_epoch -- i.e. aircraft that no
    longer appear in the source file (deregistered/removed). cutoff_epoch
    should be captured BEFORE the import run starts, so every row upserted
    during the run has updated_at >= cutoff_epoch and survives; anything
    older is from a prior run and genuinely missing from this one.
    Added 2026-07-21 -- the upsert-only import never pruned stale records
    before this."""
    with conn() as c:
        cur = c.execute(
            "DELETE FROM faa_aircraft_registry WHERE updated_at < ?", (cutoff_epoch,)
        )
        return cur.rowcount


def faa_lookup_by_n_number(n_number: str) -> dict | None:
    """Look up a single aircraft by N-number (with or without leading N)."""
    key = n_number.upper().lstrip("N") if n_number.upper().startswith("N") else n_number.upper()
    with conn() as c:
        c.row_factory = sqlite3.Row
        row = c.execute(
            "SELECT * FROM faa_aircraft_registry WHERE n_number=?", (key,)
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["ladd"] = faa_is_ladd(key)
    return d


def faa_lookup_by_hex(hex_code: str) -> dict | None:
    """Look up a single aircraft by ICAO mode-S hex."""
    key = hex_code.lower()
    with conn() as c:
        c.row_factory = sqlite3.Row
        row = c.execute(
            "SELECT * FROM faa_aircraft_registry WHERE LOWER(mode_s_hex)=?", (key,)
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["ladd"] = faa_is_ladd(d["n_number"])
    return d


def faa_is_ladd(n_number: str) -> bool:
    """Return True if N-number is on the LADD privacy list."""
    key = n_number.upper().lstrip("N") if n_number.upper().startswith("N") else n_number.upper()
    with conn() as c:
        row = c.execute(
            "SELECT 1 FROM faa_ladd_aircraft WHERE n_number=?", (key,)
        ).fetchone()
    return row is not None


def faa_registry_meta_set(key: str, value: str) -> None:
    with conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO faa_registry_meta (key, value) VALUES (?, ?)",
            (key, value),
        )


def faa_registry_meta_get(key: str) -> str | None:
    with conn() as c:
        row = c.execute(
            "SELECT value FROM faa_registry_meta WHERE key=?", (key,)
        ).fetchone()
    return row[0] if row else None


def faa_registry_count() -> dict:
    with conn() as c:
        total = c.execute("SELECT COUNT(*) FROM faa_aircraft_registry").fetchone()[0]
        valid = c.execute(
            "SELECT COUNT(*) FROM faa_aircraft_registry WHERE status_code='V'"
        ).fetchone()[0]
        ladd  = c.execute("SELECT COUNT(*) FROM faa_ladd_aircraft").fetchone()[0]
        last_updated = faa_registry_meta_get("last_full_import")
    return {"total": total, "valid": valid, "ladd": ladd, "last_updated": last_updated}


# -- Schema V12 -- WPC national forecast discussions --------------------------

SCHEMA_V12 = """
CREATE TABLE IF NOT EXISTS wpc_discussions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    awips_id        TEXT NOT NULL,
    product_label   TEXT NOT NULL,
    issued_at       REAL NOT NULL,
    fetched_at      REAL DEFAULT (unixepoch()),
    body            TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_wpc_discussions_awips
    ON wpc_discussions(awips_id, issued_at DESC);
"""


def init_db_v12() -> None:
    """Apply v12 schema -- WPC national forecast discussions."""
    with conn() as c:
        c.executescript(SCHEMA_V12)
        c.executescript(SCHEMA_USAGE)


SCHEMA_V13 = """
ALTER TABLE notams ADD COLUMN last_seen_at REAL DEFAULT NULL;
"""


def init_db_v13() -> None:
    """Apply v13 schema -- notams.last_seen_at, refreshed on every upsert so
    NULL-effective_end staleness can be judged by 'last seen on the wire'
    rather than 'first ever inserted'."""
    with conn() as c:
        for stmt in SCHEMA_V13.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                try:
                    c.execute(stmt)
                except Exception:
                    pass  # column already exists on subsequent startups


def upsert_wpc_discussion(awips_id: str, product_label: str,
                           issued_at: float, body: str) -> None:
    with conn() as c:
        c.execute("""
            INSERT INTO wpc_discussions (awips_id, product_label, issued_at, body)
            VALUES (?, ?, ?, ?)
        """, (awips_id, product_label, issued_at, body))


def get_latest_wpc_discussion(awips_id: str = "FXUS02") -> dict | None:
    with conn() as c:
        row = c.execute("""
            SELECT * FROM wpc_discussions
            WHERE awips_id = ?
            ORDER BY issued_at DESC LIMIT 1
        """, (awips_id,)).fetchone()
        return dict(row) if row else None


def get_latest_wpc_discussions() -> list[dict]:
    with conn() as c:
        rows = c.execute("""
            SELECT w.*
            FROM wpc_discussions w
            INNER JOIN (
                SELECT awips_id, MAX(issued_at) AS max_issued
                FROM wpc_discussions
                GROUP BY awips_id
            ) latest ON w.awips_id = latest.awips_id
                     AND w.issued_at = latest.max_issued
            ORDER BY w.awips_id
        """).fetchall()
        return [dict(r) for r in rows]


def prune_wpc_discussions(keep_per_product: int = 10) -> int:
    with conn() as c:
        rows = c.execute(
            "SELECT DISTINCT awips_id FROM wpc_discussions"
        ).fetchall()
        deleted = 0
        for row in rows:
            awips = row[0]
            cur = c.execute("""
                DELETE FROM wpc_discussions
                WHERE awips_id = ?
                  AND id NOT IN (
                      SELECT id FROM wpc_discussions
                      WHERE awips_id = ?
                      ORDER BY issued_at DESC
                      LIMIT ?
                  )
            """, (awips, awips, keep_per_product))
            deleted += cur.rowcount
        return deleted


SCHEMA_V14 = """
CREATE TABLE IF NOT EXISTS webhook_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source          TEXT    NOT NULL,   -- 'limoanywhere' | 'ringcentral' | '3cx'
    event_type      TEXT    NOT NULL,
    external_ref    TEXT,               -- source's own event/reservation/call id
    payload         TEXT    NOT NULL,   -- raw JSON as received
    received_at     REAL    NOT NULL DEFAULT (unixepoch())
);
CREATE INDEX IF NOT EXISTS idx_webhook_events_source
    ON webhook_events(source, received_at);
CREATE INDEX IF NOT EXISTS idx_webhook_events_ref
    ON webhook_events(external_ref);
"""


def init_db_v14() -> None:
    """Apply v14 schema — inbound external webhook events (LimoAnywhere/RingCentral/3CX)."""
    with conn() as c:
        c.executescript(SCHEMA_V14)


def insert_webhook_event(source: str, event_type: str, external_ref: str, payload: str) -> int:
    """Store a raw inbound webhook delivery. Returns the new row id."""
    with conn() as c:
        cur = c.execute(
            """INSERT INTO webhook_events (source, event_type, external_ref, payload)
                   VALUES (?, ?, ?, ?)""",
            (source, event_type, external_ref, payload),
        )
        return cur.lastrowid


def get_webhook_events(source: str | None = None, limit: int = 50) -> list[dict]:
    """Return recent webhook events, optionally filtered by source."""
    with conn() as c:
        if source:
            rows = c.execute(
                """SELECT * FROM webhook_events WHERE source = ?
                       ORDER BY received_at DESC LIMIT ?""",
                (source, limit),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM webhook_events ORDER BY received_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]


SCHEMA_V15 = """
CREATE TABLE IF NOT EXISTS international_aviation_feed (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source          TEXT    NOT NULL,   -- 'eurocontrol' | 'jasdat'
    record_type     TEXT    NOT NULL,   -- 'notam' | 'sigmet' | 'flow_measure' | etc.
    external_ref    TEXT,
    raw_json        TEXT    NOT NULL,
    fetched_at      REAL    NOT NULL DEFAULT (unixepoch())
);
CREATE INDEX IF NOT EXISTS idx_intl_aviation_source
    ON international_aviation_feed(source, fetched_at);
"""


def init_db_v15() -> None:
    """Apply v15 schema -- international aviation feeds (EUROCONTROL/JASDAT)."""
    with conn() as c:
        c.executescript(SCHEMA_V15)


# ── Schema V16 — dedicated OOOI phase field on watchlist_entries ──────────────

SCHEMA_V16 = """
ALTER TABLE watchlist_entries ADD COLUMN oooi_phase TEXT;
ALTER TABLE watchlist_entries ADD COLUMN oooi_phase_updated_at TEXT;
"""


def init_db_v16() -> None:
    """Apply v16 schema — adds a dedicated oooi_phase field to watchlist_entries.

    Fixes a live bug (found 2026-07-21): OOOI phase was previously re-derived
    by parsing the free-text last_event_summary field, which every other
    alert type for the same entry (TMI assignment, flight-plan amendment,
    approach-proximity ping, FDPS filed/cancelled) also overwrites. Any of
    those unrelated alerts firing in between OOOI checks clobbered the
    parser's input, causing it to fall back to "pre_departure" and re-fire
    the same OOOI transition repeatedly (confirmed live: one takeoff fired
    "OFF — airborne" 14 times over 90 minutes). This field is written only
    by the OOOI phase-detection code paths in poller/main.py.
    """
    with conn() as c:
        for stmt in SCHEMA_V16.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                try:
                    c.execute(stmt)
                except Exception:
                    pass  # column already exists on subsequent startups


# ── OpenSky Aircraft Metadata Registry (V17) ────────────────────────────────
# International supplementary registry -- see poller/fetchers/opensky_registry.py.
# OpenSky's bulk aircraftDatabase.csv is a frozen snapshot (confirmed
# 2026-07-21: Last-Modified Nov 2024, site itself says updates are "on hold"),
# so this is a one-time/occasional import, not a recurring full pull like FAA's
# daily one -- see fetcher module docstring for the freshness-check approach.

SCHEMA_V17 = """
CREATE TABLE IF NOT EXISTS opensky_aircraft_registry (
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
CREATE INDEX IF NOT EXISTS idx_opensky_reg_registration
    ON opensky_aircraft_registry(registration);

CREATE TABLE IF NOT EXISTS opensky_registry_meta (
    key             TEXT    PRIMARY KEY,
    value           TEXT    NOT NULL
);
"""


def init_db_v17() -> None:
    """Apply v17 schema — OpenSky aircraft metadata registry (supplementary,
    international coverage the FAA registry doesn't have)."""
    with conn() as c:
        c.executescript(SCHEMA_V17)


# ── Schema V18 — hex/registration identity fields on watchlist_entries ───────
# 2026-07-21. Closes a real gap: check_fdps_watchlist() and the TFMS matcher
# both match a live event to a watchlist entry on callsign string alone.
# entry.get("hex_id") was already being read in fdps_parser.py's
# _fire_fdps_nas_alert() call site -- it just always returned None, because
# no such column existed. The only place a hex ever lived was hand-typed
# into the free-text notes field ("Hex: af83e8"), regex-scraped at poll time
# by poller/main.py's _check_flight_airplanes_live(). Real structured
# columns let that same function do an actual identity cross-check instead
# of just resolving a lookup key: compare the ADS-B-reported hex/registration
# against the entry's *expected* hex/registration and flag a mismatch --
# the literal "follows the metal, not the schedule" claim, now enforced
# rather than just descriptive of the underlying registry data existing.

SCHEMA_V18 = """
ALTER TABLE watchlist_entries ADD COLUMN hex_id TEXT;
ALTER TABLE watchlist_entries ADD COLUMN registration TEXT;
"""


def init_db_v18() -> None:
    """Apply v18 schema — adds hex_id/registration identity columns to
    watchlist_entries. See SCHEMA_V18 comment above for why."""
    with conn() as c:
        for stmt in SCHEMA_V18.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                try:
                    c.execute(stmt)
                except Exception:
                    pass  # column already exists on subsequent startups
        c.execute("""
            CREATE INDEX IF NOT EXISTS idx_watchlist_entries_hex
                ON watchlist_entries(hex_id)
        """)


# ── Schema V19 — train subsection/day-pattern + flight sister-flight fields ──
# 2026-07-21. Closes a real gap found while rebuilding the train page: the
# permanent_trains.json/permanent_flights.json files already carry
# subsection ("amtrak"/"vre"/"marc"), show_national, show_regional,
# days_active, and (for flights) sister_flight -- added earlier in this same
# session's train-roster rebuild -- but shared.watchlist.WatchlistFileWatcher
# never read them into the DB, and no columns existed to hold them anyway.
# Every permanent train entry has silently had subsection=NULL since that
# rebuild, which is why the VRE/MARC panel filters (subsection='vre'/'marc')
# always matched zero rows despite the roster file itself being correct.
# days_active is stored as a JSON-encoded array (SQLite has no native array
# type) and decoded back to a list in get_watchlist_entries() below.

SCHEMA_V19 = """
ALTER TABLE watchlist_entries ADD COLUMN subsection TEXT;
ALTER TABLE watchlist_entries ADD COLUMN show_national INTEGER;
ALTER TABLE watchlist_entries ADD COLUMN show_regional INTEGER;
ALTER TABLE watchlist_entries ADD COLUMN days_active TEXT;
ALTER TABLE watchlist_entries ADD COLUMN sister_flight TEXT;
"""


def init_db_v19() -> None:
    """Apply v19 schema — adds subsection/show_national/show_regional/
    days_active/sister_flight columns to watchlist_entries. See SCHEMA_V19
    comment above for why."""
    with conn() as c:
        for stmt in SCHEMA_V19.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                try:
                    c.execute(stmt)
                except Exception:
                    pass  # column already exists on subsequent startups


# ── Schema V20 — bandwidth priority override (SWIM vs NEXRAD) ───────────────
# 2026-07-21. the operator asked for a bidirectional operator toggle: when bandwidth
# is tight, let a human (or, later, an automated contention detector) declare
# which side matters more right now -- 'swim' (SWIM/NMS ingest stays at full
# rate, anything else should back off) or 'nexrad' (a NEXRAD Level II puller
# gets priority, SWIM's heaviest feed -- fdps, the unscoped nationwide flight
# feed, see ingest_swim_firehose_bandwidth memory -- pauses until this flips
# back). 'auto' is the default/no-override state.
#
# IMPORTANT — half of this is currently a no-op: there is no NEXRAD Level II
# puller built yet (only an ingest.swim_client fallback exists so far). The
# 'nexrad' priority direction is real today (ingest/swim_client.py checks it
# and pauses fdps). The 'swim' priority direction has nothing on the other
# side to defer YET -- it's a documented contract for whenever a Level II
# puller is built, not functional today. Don't represent it as already doing
# something on the NEXRAD side until that puller exists and checks this flag.
#
# Singleton row (id=1, enforced via CHECK) rather than a new one per change --
# callers only ever care about "what's the current state", not history.
# expires_at is optional; NULL means "stays until explicitly changed back."

SCHEMA_V20 = """
CREATE TABLE IF NOT EXISTS bandwidth_priority_state (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    priority    TEXT NOT NULL DEFAULT 'auto',
    reason      TEXT,
    set_by      TEXT,
    set_at      REAL,
    expires_at  REAL
);
"""


def init_db_v20() -> None:
    """Apply v20 schema — creates bandwidth_priority_state (singleton row).
    See SCHEMA_V20 comment above for why."""
    with conn() as c:
        for stmt in SCHEMA_V20.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                c.execute(stmt)
        c.execute("""
            INSERT OR IGNORE INTO bandwidth_priority_state (id, priority, set_at)
            VALUES (1, 'auto', :now)
        """, {"now": time.time()})


def get_bandwidth_priority() -> dict:
    """Current bandwidth-priority override state.

    Returns dict: priority ('auto'|'swim'|'nexrad'|'weather'), reason, set_by,
    set_at, expires_at, and active (bool -- True only when priority != 'auto'
    AND not expired). Expiry is computed on read, not written back -- a stale
    row just reports active=False and priority normalized to 'auto' until
    someone explicitly calls set_bandwidth_priority() again.

    'weather' added 2026-07-26: an ingest-side backpressure valve, distinct
    from 'nexrad' (which only pauses fdps for an unrelated future NEXRAD
    puller). 'weather' pauses the SWIM feeds that are lowest-value during an
    active severe/extreme weather event (STDDS surface tracks, TBFM arrival
    sequencing, ITWS raw terminal weather codes, AIM/NOTAM) so FDPS (flight
    events) and TFMS (NAS ground programs -- GDP/GS, exactly what matters
    during weather-driven ground stops) keep their full share of this Pi's
    CPU/bandwidth instead of competing with six equally-weighted sessions.
    See ingest/swim_client.py's _bandwidth_priority_says_pause().

    'ollama' added 2026-08-11: same low-priority feed set as 'weather', but
    auto-triggered from common/llm.py around an in-flight Ollama call rather
    than an NWS alert -- the active complement to that module's passive
    load-gate wait (see OLLAMA_BACKPRESSURE_ENABLED there). Built after
    confirming a cold model load can lose the CPU race entirely on this
    4-core Pi when ingest is running unshed (docs/benchmarks/
    OLLAMA_BACKPRESSURE_AB_2026-08-11.md).
    """
    with conn() as c:
        row = c.execute("SELECT * FROM bandwidth_priority_state WHERE id = 1").fetchone()
    if row is None:
        return {"priority": "auto", "reason": None, "set_by": None,
                 "set_at": None, "expires_at": None, "active": False}
    d = dict(row)
    now = time.time()
    expired = d["expires_at"] is not None and now >= d["expires_at"]
    d["active"] = d["priority"] != "auto" and not expired
    if expired:
        d["priority"] = "auto"
    return d


def set_bandwidth_priority(priority: str, set_by: str = "", reason: str = "",
                           ttl_seconds: float | None = None) -> dict:
    """Set the bandwidth-priority override. priority must be 'auto', 'swim',
    'nexrad', 'weather', or 'ollama'. ttl_seconds is optional -- omit for
    "stays until explicitly changed back."."""
    if priority not in ("auto", "swim", "nexrad", "weather", "ollama"):
        raise ValueError(f"invalid priority: {priority!r} (must be auto/swim/nexrad/weather/ollama)")
    now = time.time()
    expires_at = (now + ttl_seconds) if ttl_seconds else None
    with conn() as c:
        c.execute("""
            INSERT INTO bandwidth_priority_state (id, priority, reason, set_by, set_at, expires_at)
            VALUES (1, :priority, :reason, :set_by, :set_at, :expires_at)
            ON CONFLICT(id) DO UPDATE SET
                priority=excluded.priority, reason=excluded.reason,
                set_by=excluded.set_by, set_at=excluded.set_at, expires_at=excluded.expires_at
        """, {"priority": priority, "reason": reason or None, "set_by": set_by or None,
              "set_at": now, "expires_at": expires_at})
    return get_bandwidth_priority()


def opensky_upsert_aircraft(records: list[dict]) -> int:
    """Bulk upsert OpenSky registry records. Returns count upserted."""
    import time as _time
    now = _time.time()
    sql = """
        INSERT INTO opensky_aircraft_registry
            (icao24, registration, manufacturer_icao, manufacturer_name, model,
             typecode, serial_number, icao_aircraft_type, operator, operator_icao,
             operator_iata, owner, registered, reg_until, status, built, updated_at)
        VALUES
            (:icao24, :registration, :manufacturer_icao, :manufacturer_name, :model,
             :typecode, :serial_number, :icao_aircraft_type, :operator, :operator_icao,
             :operator_iata, :owner, :registered, :reg_until, :status, :built, :updated_at)
        ON CONFLICT(icao24) DO UPDATE SET
            registration       = excluded.registration,
            manufacturer_icao  = excluded.manufacturer_icao,
            manufacturer_name  = excluded.manufacturer_name,
            model              = excluded.model,
            typecode           = excluded.typecode,
            serial_number      = excluded.serial_number,
            icao_aircraft_type = excluded.icao_aircraft_type,
            operator           = excluded.operator,
            operator_icao      = excluded.operator_icao,
            operator_iata      = excluded.operator_iata,
            owner              = excluded.owner,
            registered         = excluded.registered,
            reg_until          = excluded.reg_until,
            status             = excluded.status,
            built              = excluded.built,
            updated_at         = excluded.updated_at
    """
    for r in records:
        r["updated_at"] = now
    with conn() as c:
        c.executemany(sql, records)
    return len(records)


def opensky_lookup_by_hex(icao24: str) -> dict | None:
    """Look up a single aircraft by ICAO24 hex (supplementary to FAA lookup)."""
    key = icao24.lower()
    with conn() as c:
        c.row_factory = sqlite3.Row
        row = c.execute(
            "SELECT * FROM opensky_aircraft_registry WHERE LOWER(icao24)=?", (key,)
        ).fetchone()
    return dict(row) if row else None


def opensky_lookup_by_registration(registration: str) -> dict | None:
    """
    Look up a single aircraft by registration/tail number. Added 2026-07-23
    to support the tail-number -> hex resolution directive: FAA covers US
    N-numbers only, OpenSky's registry (idx_opensky_reg_registration) covers
    foreign registrations too -- this is the fallback/cross-check for
    non-US tails FAA will never have, and a second independent source for
    US tails FAA does have.
    """
    key = registration.upper().strip().replace("-", "")
    with conn() as c:
        c.row_factory = sqlite3.Row
        row = c.execute(
            "SELECT * FROM opensky_aircraft_registry "
            "WHERE UPPER(REPLACE(registration, '-', ''))=?", (key,)
        ).fetchone()
    return dict(row) if row else None


def opensky_registry_meta_set(key: str, value: str) -> None:
    with conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO opensky_registry_meta (key, value) VALUES (?, ?)",
            (key, value),
        )


def opensky_registry_meta_get(key: str) -> str | None:
    with conn() as c:
        row = c.execute(
            "SELECT value FROM opensky_registry_meta WHERE key=?", (key,)
        ).fetchone()
    return row[0] if row else None


def opensky_registry_count() -> int:
    with conn() as c:
        return c.execute("SELECT COUNT(*) FROM opensky_aircraft_registry").fetchone()[0]


def opensky_registry_sweep_removed(cutoff_epoch: float) -> int:
    """Delete OpenSky rows not touched since cutoff_epoch -- same mark-and-sweep
    pattern as faa_registry_sweep_removed(), same reasoning."""
    with conn() as c:
        cur = c.execute(
            "DELETE FROM opensky_aircraft_registry WHERE updated_at < ?", (cutoff_epoch,)
        )
        return cur.rowcount


def update_watchlist_oooi_phase(entry_id: str, phase: str, updated_at: str) -> None:
    """Persist the OOOI phase tracker's own state for entry_id.

    Deliberately separate from update_watchlist_last_event()'s
    last_event_summary column — see init_db_v16() docstring for why sharing
    that field with every other alert type caused repeated re-firing.
    """
    with conn() as c:
        c.execute("""
            UPDATE watchlist_entries
            SET oooi_phase=?, oooi_phase_updated_at=?
            WHERE id=?
        """, (phase, updated_at, entry_id))


def upsert_international_aviation_records(source: str, records: list[dict]) -> int:
    """Bulk-insert normalized international aviation records. Returns count inserted."""
    with conn() as c:
        n = 0
        for r in records:
            c.execute(
                """INSERT INTO international_aviation_feed
                       (source, record_type, external_ref, raw_json)
                       VALUES (?, ?, ?, ?)""",
                (source, r.get("record_type", "unknown"),
                 str(r.get("external_ref", "")), json.dumps(r)),
            )
            n += 1
        return n


# ── v21: sudo approval-gate ─────────────────────────────────────────────────
#
# Backing store for the human-in-the-loop approval gate on passwordless sudo
# grants (ollama.service start/stop/restart, dnf remove/autoremove -- see
# SUDO_JUSTIFICATION_PROPOSAL.md, decided 2026-07-27). Claude creates a
# pending row + fires an ntfy push with Allow/Deny action buttons; the phone
# tap hits POST /admin/approval-requests/{id}/resolve (Tier 0, no bearer auth
# -- secured purely by the unguessable id, same trust model as a magic link)
# which flips status. expires_at is enforced on READ (get_approval_request),
# not via a background sweep -- a request nobody ever taps just reads back as
# "expired" the next time anything checks it, which is exactly the fail-
# closed behavior wanted: silence is never consent.
#
# command_pattern is separate from command: pattern is the normalized/generic
# form used to count repeat approvals for the frequency-promotion proposal
# (e.g. "dnf remove <docs-cleanup-set>" -> pattern "dnf-remove", but the full
# command with the real package list is still stored and shown in the push).

SCHEMA_V21 = """
CREATE TABLE IF NOT EXISTS approval_requests (
    id               TEXT PRIMARY KEY,
    command_pattern  TEXT NOT NULL,
    command          TEXT NOT NULL,
    reasoning        TEXT,
    status           TEXT NOT NULL DEFAULT 'pending',
    created_at       REAL NOT NULL,
    resolved_at      REAL,
    expires_at       REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_approval_requests_pattern
    ON approval_requests(command_pattern, status, created_at);
"""


def init_db_v21() -> None:
    """Apply v21 schema -- creates approval_requests. See SCHEMA_V21 comment
    above for the fail-closed-on-expiry design."""
    with conn() as c:
        for stmt in SCHEMA_V21.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                c.execute(stmt)


def create_approval_request(request_id: str, command_pattern: str, command: str,
                             reasoning: str = "", ttl_seconds: float = 600.0) -> dict:
    now = time.time()
    expires_at = now + ttl_seconds
    with conn() as c:
        c.execute(
            """INSERT INTO approval_requests
                   (id, command_pattern, command, reasoning, status, created_at, expires_at)
               VALUES (?, ?, ?, ?, 'pending', ?, ?)""",
            (request_id, command_pattern, command, reasoning, now, expires_at),
        )
    return {"id": request_id, "status": "pending", "expires_at": expires_at}


def get_approval_request(request_id: str) -> dict | None:
    """Read a request, applying expiry-on-read: a still-pending row past its
    expires_at reads back (and is persisted) as 'expired', never as 'pending'
    or implicitly allowed. Silence is never consent."""
    with conn() as c:
        row = c.execute(
            "SELECT * FROM approval_requests WHERE id = ?", (request_id,)
        ).fetchone()
        if row is None:
            return None
        d = dict(row)
        if d["status"] == "pending" and time.time() >= d["expires_at"]:
            c.execute(
                "UPDATE approval_requests SET status='expired', resolved_at=? WHERE id=?",
                (time.time(), request_id),
            )
            d["status"] = "expired"
            d["resolved_at"] = time.time()
        return d


def resolve_approval_request(request_id: str, action: str) -> dict | None:
    """action must be 'allow' or 'deny'. Only resolves a request still in
    'pending' state -- a request already allowed/denied/expired can't be
    flipped again (no double-tap races, no resolving a stale/expired link)."""
    if action not in ("allow", "deny"):
        raise ValueError(f"invalid action: {action!r} (must be allow/deny)")
    new_status = "allowed" if action == "allow" else "denied"
    now = time.time()
    with conn() as c:
        cur = c.execute(
            """UPDATE approval_requests
               SET status=?, resolved_at=?
               WHERE id=? AND status='pending' AND expires_at > ?""",
            (new_status, now, request_id, now),
        )
        if cur.rowcount == 0:
            # either not found, already resolved, or expired-on-arrival
            row = c.execute(
                "SELECT * FROM approval_requests WHERE id = ?", (request_id,)
            ).fetchone()
            return dict(row) if row else None
        row = c.execute(
            "SELECT * FROM approval_requests WHERE id = ?", (request_id,)
        ).fetchone()
        return dict(row)


def count_recent_approvals(command_pattern: str, since_epoch: float) -> int:
    """Count of 'allowed' requests for this pattern since since_epoch --
    backs the frequency-promotion proposal (>2 in a rolling 7 days -> Claude
    proposes folding the pattern into a standing NOPASSWD grant with no gate)."""
    with conn() as c:
        row = c.execute(
            """SELECT COUNT(*) AS n FROM approval_requests
               WHERE command_pattern = ? AND status = 'allowed' AND created_at >= ?""",
            (command_pattern, since_epoch),
        ).fetchone()
        return row["n"] if row else 0


# ── FDPS confirmed flight-plan lookup ─────────────────────────────────────────
# 2026-07-27: flight_events (populated by ingest/parsers/fdps_parser.py, see
# its module docstring for the FIXM 3.0/4.2 parser history) had no index on
# (airline, flight_num) -- a callsign-based lookup against 219k+ rows under
# concurrent ingest write load took >30s and timed out. This index makes the
# equality lookup instant; the ORDER BY updated_at DESC only has to sort the
# small per-callsign result set (a callsign is reused across different real
# flights over time, so more than one flight_id/GUFI can match -- most recent
# wins).

SCHEMA_V22 = """
CREATE INDEX IF NOT EXISTS idx_flight_events_callsign
    ON flight_events(airline, flight_num);
"""


def init_db_v22() -> None:
    """Apply v22 schema -- index for FDPS callsign lookups. See SCHEMA_V22
    comment above."""
    with conn() as c:
        for stmt in SCHEMA_V22.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                c.execute(stmt)


def _extract_aircraft_hex_registration(raw_xml: str | None) -> tuple[str | None, str | None]:
    """flight_events.raw_json actually holds the raw FDPS FIXM XML text
    (not JSON, despite the column name) -- see ingest/parsers/fdps_parser.py
    for the full namespace-aware parse this mirrors a narrow slice of.
    The <aircraftDescription> element carries aircraftAddress (ICAO 24-bit
    hex) and registration (tail number) as plain XML attributes, e.g.:
        <aircraftDescription ... aircraftAddress="AB2C8E" ... registration="N819UA" ...>

    A small targeted regex is used here rather than importing
    fdps_parser.py's namespace-aware ElementTree helpers (those are
    module-private and this only needs two flat attributes, not a full
    tree walk) or a fresh XML parse in common/db.py (keeps this module's
    dependency footprint as-is). Returns (None, None) if either attribute
    is absent -- normal for a flight plan that hasn't had an aircraft
    assigned yet, not an error."""
    if not raw_xml:
        return None, None
    import re
    hex_m = re.search(r'aircraftAddress="([0-9A-Fa-f]{6})"', raw_xml)
    reg_m = re.search(r'\bregistration="([^"]+)"', raw_xml)
    return (
        hex_m.group(1).upper() if hex_m else None,
        reg_m.group(1) if reg_m else None,
    )


def get_flight_plan_by_callsign(callsign: str) -> dict | None:
    """Confirmed flight-plan details from FAA FDPS (SWIM/SFDPS FIXM feed),
    keyed by ICAO callsign (e.g. 'UAL2185' -> airline='UAL', flight_num='2185').

    Returns the most-recently-updated flight_events row matching that
    callsign split, or None if FDPS has never carried a flight plan for it
    (feed coverage gap, callsign not yet filed, or genuinely no match).
    Prefers a non-cancelled row when both a stale cancelled entry and a
    fresher active/proposed one exist for the same reused callsign.

    2026-08-10: the returned dict now also carries "hex" and
    "registration" keys, parsed from raw_json via
    _extract_aircraft_hex_registration() -- added so callers (see
    _check_flight_fdps_cache in poller/main.py) can detect a tail/airframe
    reassignment on an already-watchlisted flight, not just a destination
    or cancellation change. Both are None if the flight plan doesn't
    carry an aircraft assignment yet (e.g. still "proposed" upstream of
    equipment assignment) -- a normal state, not a parse failure."""
    import re
    m = re.match(r"^([A-Za-z]{2,3})(\d+[A-Za-z]?)$", callsign.strip())
    if not m:
        return None
    airline, flight_num = m.group(1).upper(), m.group(2)

    with conn() as c:
        row = c.execute("""
            SELECT * FROM flight_events
            WHERE airline = ? AND flight_num = ?
            ORDER BY (status != 'cancelled') DESC, updated_at DESC
            LIMIT 1
        """, (airline, flight_num)).fetchone()
        if not row:
            return None
        plan = dict(row)
        plan["hex"], plan["registration"] = _extract_aircraft_hex_registration(plan.get("raw_json"))
        return plan


def get_flight_plan_by_flight_num(callsign_or_number: str,
                                  origin: str | None = None) -> dict | None:
    """Fallback FDPS match by bare flight number (+ optional origin),
    ignoring the airline code entirely.

    2026-07-28: get_flight_plan_by_callsign() only matches the airline code
    embedded in the given identifier. That breaks for codeshare/regional-
    operated flights -- a flight marketed as "UAL4044" (United) is actually
    flown and filed with FAA as "ASH4044" (Mesa Airlines, operating as
    United Express). The marketing carrier's code never appears in FDPS at
    all for these, so the direct lookup always returns None even when FAA
    has a completely live flight plan on file. Confirmed this the hard way
    2026-07-28 checking UAL4044/UAL4056 by hand (both filed under "ASH").

    Strips any leading letters from `callsign_or_number` to get the bare
    number, then matches flight_num alone. `origin` narrows the match when
    given (recommended -- flight numbers get reused across carriers and
    routes on the same day, e.g. 4044 was seen as SWA/ENY/ASH on 2026-07-28
    alone), but is optional since callers may not have it yet.
    """
    import re
    m = re.match(r"^[A-Za-z]*(\d+[A-Za-z]?)$", callsign_or_number.strip())
    if not m:
        return None
    flight_num = m.group(1)

    with conn() as c:
        if origin:
            row = c.execute("""
                SELECT * FROM flight_events
                WHERE flight_num = ? AND origin = ?
                ORDER BY (status != 'cancelled') DESC, updated_at DESC
                LIMIT 1
            """, (flight_num, origin.strip().upper())).fetchone()
        else:
            row = c.execute("""
                SELECT * FROM flight_events
                WHERE flight_num = ?
                ORDER BY (status != 'cancelled') DESC, updated_at DESC
                LIMIT 1
            """, (flight_num,)).fetchone()
        return dict(row) if row else None


# ── v23: dedicated FDPS/FIDS state tracking (fixes 2026-07-27 alert-spam bug) ─
#
# last_event_summary is a single shared field written by EVERY watchlist
# check type (OOOI phase transitions, fdps_status/fdps_cancelled/
# fdps_destination_change, fids_update, proximity, schedule inference) via
# update_watchlist_last_event(). _check_flight_fdps_cache and
# _check_flight_fids (both added earlier the same day as this fix) each
# compared their OWN new summary against that ONE shared field to decide
# whether to fire -- so a fire from a DIFFERENT check type in between ticks
# overwrote the field and made the NEXT check see a false "changed" positive,
# even though its own actual signal hadn't moved. This is confirmed as the
# proximate cause of three duplicate "UAL2670 FIDS DCA: gate B14 baggage 5
# Landed" pushes on 2026-07-27, each ~5 minutes apart, interleaved with
# unrelated fdps_status fires that kept clobbering last_event_summary.
#
# The OOOI-phase chain already solved this exact problem via its own
# dedicated oooi_phase/oooi_phase_updated_at columns (see init_db_v16() and
# update_watchlist_oooi_phase() -- same lesson, just not yet applied to FDPS
# or FIDS when they were wired in). This migration gives each of those two
# checks the same treatment: last_fdps_status/last_fdps_updated_at and
# last_fids_status/last_fids_updated_at, both independent of
# last_event_summary and of each other.
SCHEMA_V23 = """
ALTER TABLE watchlist_entries ADD COLUMN last_fdps_status TEXT;
ALTER TABLE watchlist_entries ADD COLUMN last_fdps_updated_at TEXT;
ALTER TABLE watchlist_entries ADD COLUMN last_fids_status TEXT;
ALTER TABLE watchlist_entries ADD COLUMN last_fids_updated_at TEXT;
"""


def init_db_v23() -> None:
    """Apply v23 schema -- dedicated FDPS/FIDS state columns. See SCHEMA_V23
    comment above. ALTER TABLE ADD COLUMN has no IF NOT EXISTS in SQLite, so
    each statement is tried independently and a 'duplicate column' error
    (already applied) is swallowed -- safe to call on every startup."""
    with conn() as c:
        for stmt in SCHEMA_V23.strip().split(";"):
            stmt = stmt.strip()
            if not stmt:
                continue
            try:
                c.execute(stmt)
            except sqlite3.OperationalError as e:
                if "duplicate column" not in str(e).lower():
                    raise


def update_watchlist_fdps_status(entry_id: str, status: str, updated_at: str) -> None:
    """Persist _check_flight_fdps_cache's own state for entry_id. Deliberately
    separate from update_watchlist_last_event()'s last_event_summary column
    and from update_watchlist_fids_status() -- see SCHEMA_V23 comment for why
    sharing a field across check types caused repeated re-firing."""
    with conn() as c:
        c.execute("""
            UPDATE watchlist_entries
            SET last_fdps_status=?, last_fdps_updated_at=?
            WHERE id=?
        """, (status, updated_at, entry_id))


def update_watchlist_fids_status(entry_id: str, status: str, updated_at: str) -> None:
    """Persist _check_flight_fids's own state for entry_id. Deliberately
    separate from update_watchlist_last_event()'s last_event_summary column
    and from update_watchlist_fdps_status() -- see SCHEMA_V23 comment for why
    sharing a field across check types caused repeated re-firing."""
    with conn() as c:
        c.execute("""
            UPDATE watchlist_entries
            SET last_fids_status=?, last_fids_updated_at=?
            WHERE id=?
        """, (status, updated_at, entry_id))


# ── v24: index for the 30-day flight_events retention scan ─────────────────
#
# export_old_flight_events() filters on updated_at with no supporting
# index -- confirmed via direct testing that a bare `WHERE updated_at < ?`
# scan against this table times out past 15s+ under concurrent write load
# at 220k+ rows (same class of problem SCHEMA_V22 solved for the
# airline+flight_num lookup). Without this, the new daily archival skill
# would hit the same kind of slow-DDL/slow-scan issue that caused the
# SCHEMA_V22 rollout's brief web outage, every single day instead of once.
SCHEMA_V24 = """
CREATE INDEX IF NOT EXISTS idx_flight_events_updated_at
    ON flight_events(updated_at);
"""


def init_db_v24() -> None:
    """Apply v24 schema -- index for the flight_events retention scan. See
    SCHEMA_V24 comment above."""
    with conn() as c:
        for stmt in SCHEMA_V24.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                c.execute(stmt)


SCHEMA_V25 = """
-- 2026-07-28: codeshare_map -- marketing<->operating carrier/flight-number
-- pairings. Operator request: infer airline flight-numbering blocks and
-- route-locks the same way we already infer aircraft identity (FAA +
-- OpenSky), starting from real confirmed pairs rather than a hand-authored
-- table someone has to keep current by hand. Seeded opportunistically by
-- add_flight_watchlist's FDPS flight_num-fallback match (see
-- web/routes/watchlist.py) -- every time that fallback fires, it has just
-- proven a marketing identifier and an FAA-filed operating identifier are
-- the same physical flight, live, in production. NULL marketing_flight_num
-- is a valid, meaningful state (carrier-level-only signal, e.g. a future
-- AeroAPI codeshares_iata capture) -- lookup/upsert logic must use IS,
-- not =, when matching on it.
CREATE TABLE IF NOT EXISTS codeshare_map (
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
CREATE INDEX IF NOT EXISTS idx_codeshare_marketing
    ON codeshare_map(marketing_carrier, marketing_flight_num);
CREATE INDEX IF NOT EXISTS idx_codeshare_operating
    ON codeshare_map(operating_carrier, operating_flight_num);
"""


def init_db_v25() -> None:
    """Apply v25 schema -- codeshare_map. See SCHEMA_V25 comment above."""
    with conn() as c:
        for stmt in SCHEMA_V25.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                c.execute(stmt)


def upsert_codeshare_mapping(marketing_carrier: str, marketing_flight_num: str | None,
                              operating_carrier: str | None, operating_flight_num: str | None,
                              origin: str | None, destination: str | None,
                              source: str) -> None:
    """Record or reinforce a marketing<->operating pairing. Matches on
    IS (not =) throughout since marketing_flight_num/operating_carrier are
    legitimately NULL for coarse, carrier-level-only signals -- SQLite\'s
    UNIQUE index treats every NULL as distinct, so a plain INSERT ... ON
    CONFLICT would silently accumulate duplicate rows for those; this
    explicit SELECT-then-INSERT/UPDATE avoids that."""
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    marketing_carrier = (marketing_carrier or "").upper() or None
    operating_carrier = (operating_carrier or "").upper() or None
    with conn() as c:
        existing = c.execute("""
            SELECT id FROM codeshare_map
            WHERE marketing_carrier IS ? AND marketing_flight_num IS ?
              AND operating_carrier IS ?
        """, (marketing_carrier, marketing_flight_num, operating_carrier)).fetchone()
        if existing:
            c.execute("""
                UPDATE codeshare_map
                SET confidence = confidence + 1,
                    last_confirmed_at = ?,
                    origin = COALESCE(?, origin),
                    destination = COALESCE(?, destination),
                    operating_flight_num = COALESCE(?, operating_flight_num)
                WHERE id = ?
            """, (now, origin, destination, operating_flight_num, existing["id"]))
        else:
            c.execute("""
                INSERT INTO codeshare_map
                (marketing_carrier, marketing_flight_num, operating_carrier,
                 operating_flight_num, origin, destination, confidence,
                 source, first_seen_at, last_confirmed_at)
                VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
            """, (marketing_carrier, marketing_flight_num, operating_carrier,
                  operating_flight_num, origin, destination, source, now, now))


def get_codeshare_mapping_by_marketing(marketing_carrier: str, marketing_flight_num: str,
                                        include_zero_confidence: bool = False) -> list[dict]:
    with conn() as c:
        q = """
            SELECT * FROM codeshare_map
            WHERE marketing_carrier = ? AND marketing_flight_num = ?
        """ + ("" if include_zero_confidence else " AND confidence > 0") + """
            ORDER BY confidence DESC
        """
        rows = c.execute(q, ((marketing_carrier or "").upper(), marketing_flight_num)).fetchall()
        return [dict(r) for r in rows]


def get_codeshare_mapping_by_operating(operating_carrier: str, operating_flight_num: str,
                                        include_zero_confidence: bool = False) -> list[dict]:
    with conn() as c:
        q = """
            SELECT * FROM codeshare_map
            WHERE operating_carrier = ? AND operating_flight_num = ?
        """ + ("" if include_zero_confidence else " AND confidence > 0") + """
            ORDER BY confidence DESC
        """
        rows = c.execute(q, ((operating_carrier or "").upper(), operating_flight_num)).fetchall()
        return [dict(r) for r in rows]


def decay_stale_codeshare_mappings(stale_after_days: int = 90, decay_amount: int = 1) -> dict:
    """Phase 3: periodic maintenance sweep for codeshare_map. Any entry not
    reconfirmed (see upsert_codeshare_mapping) in `stale_after_days` days
    loses `decay_amount` confidence, floored at 0. A mapping that reaches 0
    is not deleted -- the historical record stays -- but
    get_codeshare_mapping_by_marketing/by_operating exclude it by default
    (include_zero_confidence=True overrides), so a stale/likely-wrong
    pairing stops being trusted automatically without erasing the fact it
    was once observed. Intended to run on a schedule (12-24h, alongside
    whatever second-brain ingestion cadence ends up calling the analyze_*
    mining functions) -- not wired to a timer yet as of 2026-07-28, this is
    the callable primitive."""
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    cutoff = time.strftime("%Y-%m-%dT%H:%M:%SZ",
                           time.gmtime(time.time() - stale_after_days * 86400))
    with conn() as c:
        stale = c.execute("""
            SELECT id, marketing_carrier, marketing_flight_num, operating_carrier,
                   operating_flight_num, confidence, last_confirmed_at
            FROM codeshare_map
            WHERE datetime(last_confirmed_at) < datetime(?) AND confidence > 0
        """, (cutoff,)).fetchall()

        decayed = []
        zeroed = []
        for row in stale:
            new_conf = max(0, row["confidence"] - decay_amount)
            c.execute("UPDATE codeshare_map SET confidence = ? WHERE id = ?",
                     (new_conf, row["id"]))
            entry = {
                "marketing_carrier": row["marketing_carrier"],
                "marketing_flight_num": row["marketing_flight_num"],
                "operating_carrier": row["operating_carrier"],
                "operating_flight_num": row["operating_flight_num"],
                "old_confidence": row["confidence"],
                "new_confidence": new_conf,
                "last_confirmed_at": row["last_confirmed_at"],
            }
            decayed.append(entry)
            if new_conf == 0:
                zeroed.append(entry)

    return {
        "stale_after_days": stale_after_days,
        "checked": len(stale),
        "decayed": decayed,
        "zeroed_out": zeroed,
        "generated_at": now,
    }


def analyze_flight_number_patterns(min_samples: int = 5, dominance_threshold: float = 0.85) -> dict:
    """Phase 2 mining over flight_events (30-day live window, operating-
    carrier data only -- flight_events is FDPS/FIXM, it has never carried a
    marketing code, see get_flight_plan_by_flight_num\'s docstring). Produces:
    (a) route_locks -- (airline, flight_num) pairs where one origin/dest
        pair dominates at or above `dominance_threshold` across at least
        `min_samples` observations;
    (b) block_histogram -- per-airline flight-number-range (bucketed by
        1000) observation counts, for spotting mainline-vs-regional-block
        numbering conventions.
    Computed on demand -- flight_events is retention-capped at 30 days live
    (see flight_events_retention_archival_20260727), so a full scan stays
    cheap. No cron/materialization needed yet; revisit if this gets slow.
    """
    from collections import defaultdict, Counter

    with conn() as c:
        rows = c.execute("""
            SELECT airline, flight_num, origin, destination
            FROM flight_events
            WHERE airline IS NOT NULL AND flight_num IS NOT NULL
        """).fetchall()

    by_key = defaultdict(Counter)
    for r in rows:
        by_key[(r["airline"], r["flight_num"])][(r["origin"], r["destination"])] += 1

    route_locks = []
    for (airline, flight_num), counter in by_key.items():
        total = sum(counter.values())
        if total < min_samples:
            continue
        top_route, top_count = counter.most_common(1)[0]
        dominance = top_count / total
        if dominance >= dominance_threshold:
            route_locks.append({
                "airline": airline, "flight_num": flight_num,
                "origin": top_route[0], "destination": top_route[1],
                "dominance": round(dominance, 3), "samples": total,
            })
    route_locks.sort(key=lambda x: -x["samples"])

    block_hist: dict = {}
    for (airline, flight_num), counter in by_key.items():
        digits = "".join(ch for ch in flight_num if ch.isdigit())
        if not digits:
            continue
        num = int(digits)
        bucket = f"{(num // 1000) * 1000}-{(num // 1000) * 1000 + 999}"
        block_hist.setdefault(airline, {}).setdefault(bucket, 0)
        block_hist[airline][bucket] += sum(counter.values())

    return {
        "route_locks": route_locks,
        "block_histogram": block_hist,
        "sample_window": "flight_events live retention (30d)",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


SCHEMA_V26 = """
-- 2026-07-28: train_events + vessel_events -- structured historical
-- accumulation tables, same role for trains/vessels that flight_events
-- plays for flights. Operator request: bring these two verticals up to
-- parity with the flight codeshare/route-lock work (same route-lock +
-- schedule-drift analysis, applied to Amtrak trains and DC-area AIS
-- vessel traffic -- water taxis, cruise ships).
CREATE TABLE IF NOT EXISTS train_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    train_number    TEXT,
    train_name      TEXT,
    route_name      TEXT,
    direction       TEXT,
    origin          TEXT,
    destination     TEXT,
    station_code    TEXT,
    station_name    TEXT,
    scheduled_time  TEXT,
    estimated_time  TEXT,
    status          TEXT,
    delay_minutes   INTEGER,
    platform        TEXT,
    fetched_at      REAL DEFAULT (unixepoch())
);
CREATE INDEX IF NOT EXISTS idx_train_events_number
    ON train_events(train_number, fetched_at);
CREATE INDEX IF NOT EXISTS idx_train_events_number_station
    ON train_events(train_number, station_code, fetched_at);

CREATE TABLE IF NOT EXISTS vessel_events (
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
);
CREATE INDEX IF NOT EXISTS idx_vessel_events_mmsi
    ON vessel_events(mmsi, fetched_at);
"""


def init_db_v26() -> None:
    """Apply v26 schema -- train_events + vessel_events. See SCHEMA_V26
    comment above.

    2026-07-28 fix: order matters here. SCHEMA_V26's CREATE INDEX on
    station_code was failing with "no such column: station_code" on any
    deployment that had already run the original (pre-fix) v26 -- CREATE
    TABLE IF NOT EXISTS is a no-op against an existing table, so the new
    columns never appeared, and the CREATE INDEX statement (which DOES run
    unconditionally) then referenced a column that didn't exist yet. Fixed
    by running CREATE TABLE statements first, then the ALTER TABLE guard,
    then CREATE INDEX statements last -- so the columns always exist by
    the time anything indexes them, whether this is a fresh install or a
    pre-fix deployment catching up."""
    with conn() as c:
        for stmt in SCHEMA_V26.strip().split(";"):
            stmt = stmt.strip()
            if stmt and stmt.upper().startswith("CREATE TABLE"):
                c.execute(stmt)
        # origin/destination/station_code/station_name were added to
        # SCHEMA_V26 after some deployments already ran the original
        # version -- ALTER TABLE ... ADD COLUMN for anyone whose
        # train_events predates this fix. Guarded so re-running is a no-op.
        existing_cols = {row["name"] for row in
                         c.execute("PRAGMA table_info(train_events)").fetchall()}
        for col in ("origin", "destination", "station_code", "station_name"):
            if col not in existing_cols:
                c.execute(f"ALTER TABLE train_events ADD COLUMN {col} TEXT")
        for stmt in SCHEMA_V26.strip().split(";"):
            stmt = stmt.strip()
            if stmt and stmt.upper().startswith("CREATE INDEX"):
                c.execute(stmt)


# ── Schema V27 — FAA ACFTREF.txt reference table (mfr_mdl_code decode) ──────
# 2026-08-02. the operator's directive: get FAA's own manufacturer/model decode
# locally too, not just OpenSky's -- redundant on purpose, so the two
# sources can be cross-checked against each other the same way FAA+OpenSky
# hex is already cross-checked in get_aircraft(). Real gap this closes:
# faa_aircraft_registry.mfr_mdl_code (e.g. "1390044") has been stored raw
# since v11 with no local way to decode it -- every prior lookup either left
# it opaque or required an external WebFetch to registry.faa.gov (done by
# hand for N39FE earlier today, the exact case that prompted this).
#
# ACFTREF.txt ships inside the SAME ReleasableAircraft.zip MASTER.txt
# already comes from (confirmed 2026-08-02 via `unzip -l`) -- no new
# download, no new schedule, just parse one more file out of the zip
# fetch_faa_registry() already opens weekly.
#
# Column layout (confirmed against a live download, comma-delimited, one
# header row, trailing comma on every row):
#   0 CODE  1 MFR  2 MODEL  3 TYPE-ACFT  4 TYPE-ENG  5 AC-CAT
#   6 BUILD-CERT-IND  7 NO-ENG  8 NO-SEATS  9 AC-WEIGHT  10 SPEED
#   11 TC-DATA-SHEET  12 TC-DATA-HOLDER
# CODE matches faa_aircraft_registry.mfr_mdl_code exactly (verified:
# code 1390044 -> "BOMBARDIER INC" / "BD-100-1A10", matching both the
# registry.faa.gov web lookup and OpenSky's independent typecode CL30
# for N39FE, hex a480f2).

SCHEMA_V27 = """
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
    updated_at      REAL    NOT NULL
);
"""


def init_db_v27() -> None:
    """Apply v27 schema -- FAA ACFTREF.txt reference table. See SCHEMA_V27
    comment above for why."""
    with conn() as c:
        c.executescript(SCHEMA_V27)


def faa_acftref_upsert(records: list[dict]) -> int:
    """Bulk upsert FAA ACFTREF.txt reference records. Returns count upserted."""
    import time as _time
    now = _time.time()
    sql = """
        INSERT INTO faa_aircraft_reference
            (code, manufacturer, model, type_acft, type_engine, ac_category,
             no_engines, no_seats, ac_weight, speed, updated_at)
        VALUES
            (:code, :manufacturer, :model, :type_acft, :type_engine, :ac_category,
             :no_engines, :no_seats, :ac_weight, :speed, :updated_at)
        ON CONFLICT(code) DO UPDATE SET
            manufacturer = excluded.manufacturer,
            model        = excluded.model,
            type_acft    = excluded.type_acft,
            type_engine  = excluded.type_engine,
            ac_category  = excluded.ac_category,
            no_engines   = excluded.no_engines,
            no_seats     = excluded.no_seats,
            ac_weight    = excluded.ac_weight,
            speed        = excluded.speed,
            updated_at   = excluded.updated_at
    """
    for r in records:
        r["updated_at"] = now
    with conn() as c:
        c.executemany(sql, records)
    return len(records)


def faa_acftref_lookup(code: str) -> dict | None:
    """Decode a faa_aircraft_registry.mfr_mdl_code into manufacturer/model
    via the local ACFTREF reference table. Returns None if the code isn't
    in the table (e.g. registry updated before a reference re-import)."""
    if not code:
        return None
    with conn() as c:
        c.row_factory = sqlite3.Row
        row = c.execute(
            "SELECT * FROM faa_aircraft_reference WHERE code=?", (code.strip(),)
        ).fetchone()
    return dict(row) if row else None


# ── Schema V28 — STDDS safety-logic hold bar + surface movement events ──────
#
# Added 2026-08-03. Two more STDDS message shapes discovered on the same
# SWIM subscription that already carries SMES/TAIS (see smes_parser.py):
#
# SafetyLogicHoldBar -- ASDE-X runway-safety-logic status per airport.
# Fields confirmed from real samples (KCLT, KCVG): <airport>, <control>
# (seen as "1" in every live sample so far -- likely a reporting-enabled
# flag, not itself the alert signal), <status> (a long digit string, ~68-70
# chars, mostly zeros with occasional non-zero digits at scattered
# positions). No FAA ICD/interface document is available to this project
# confirming what each digit position means -- treat status as an opaque
# per-airport bitmask whose CHANGE is the reliable signal, not a decoded
# "which light/runway" classification. Do not claim more precision than
# that in any alert text.
#
# SurfaceMovementEventMessage -- discrete per-aircraft ground-movement
# events (confirmed live: "spotout" [pushback], "runwayin"/"runwayout",
# "on" [touchdown], carried in both a current <event> field and a rolling
# <events><eventRecord> history). status field seen as "onrunway" /
# "onsurface" (taxiing). This is what actually lets us count "how many
# aircraft are in the taxi phase right now" per airport, as opposed to
# just raw ASDE-X track density.
SCHEMA_V28 = """
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
"""


def init_db_v28() -> None:
    """Apply v28 schema -- STDDS safety-logic status + surface movement
    (taxi) events. See SCHEMA_V28 comment above."""
    with conn() as c:
        c.executescript(SCHEMA_V28)

# 2026-08-03: adds outbound-egress tracking columns to the existing
# audit_log table (no new table -- this is state ON the audit_log rows
# themselves, not a separate log). ALTER TABLE ADD COLUMN has no IF NOT
# EXISTS in SQLite, so each statement is tried independently and a
# "duplicate column" error (already applied) is swallowed -- same pattern
# as SCHEMA_V23, safe to call on every startup.
SCHEMA_V29 = """
ALTER TABLE audit_log ADD COLUMN egress_status TEXT NOT NULL DEFAULT 'pending';
ALTER TABLE audit_log ADD COLUMN egress_attempts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE audit_log ADD COLUMN egress_last_error TEXT;
CREATE INDEX IF NOT EXISTS idx_audit_log_egress_status ON audit_log(egress_status);
"""


def init_db_v29() -> None:
    """Apply v29 schema -- compliance-egress tracking columns on
    audit_log. See SCHEMA_V29 comment above and common/compliance_egress.py
    for the actual push logic this enables."""
    with conn() as c:
        for stmt in SCHEMA_V29.strip().split(";"):
            stmt = stmt.strip()
            if not stmt:
                continue
            try:
                c.execute(stmt)
            except sqlite3.OperationalError as e:
                if "duplicate column" not in str(e).lower():
                    raise


# 2026-08-03: append-only history of every SafetyLogicHoldBar bitmask
# CHANGE (not every raw message -- STDDS re-sends the current value on a
# short cycle regardless of whether it changed, logging every one of
# those would be pure noise for this purpose) for DCA/IAD/BWI only, per
# operator direction for local reverse-engineering of the bit-position
# mapping (no FAA ICD available -- see the SafetyLogicHoldBar comment
# above parse_safety_logic_message). Deliberately scoped to the three
# home-region airports rather than all ~37 currently observed nationwide
# -- operator's own call: the bit encoding is presumably an ASDE-X
# protocol-level constant, not airport-specific, so DC-only data should
# be enough to find real correlations without the volume of a nationwide
# log. Meant to be joined against surface_movement_events (airport +
# event_time) as the first correlation source -- same underlying ASDE-X
# sensor network as the safety-logic signal itself, already being logged
# with real runway/event detail. ADS-B/ACARS enrichment is a planned
# follow-on once there is enough history here to correlate against, not
# built yet.
SCHEMA_V30 = """
CREATE TABLE IF NOT EXISTS stdds_safety_status_history (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    airport           TEXT NOT NULL,
    control           TEXT,
    previous_bitmask  TEXT,
    new_bitmask       TEXT NOT NULL,
    changed_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_stdds_safety_status_history_airport_time
    ON stdds_safety_status_history(airport, changed_at);
"""


def init_db_v30() -> None:
    """Apply v30 schema -- stdds_safety_status_history (DCA/IAD/BWI bit-
    mapping reverse-engineering log). See SCHEMA_V30 comment above."""
    with conn() as c:
        c.executescript(SCHEMA_V30)


# Airports this history log covers -- kept as one constant so the ingest
# side (smes_parser.check_incursion_alert) and any future analysis/API
# code agree on scope without duplicating the literal set.
STDDS_SAFETY_HISTORY_AIRPORTS = frozenset({"KDCA", "KIAD", "KBWI"})


# 2026-08-09: additive columns for vessel physical dimensions, added ahead
# of the ingestion path that would populate them (nothing currently writes
# these -- see init_db_v31 docstring). Nullable REAL, safe on every
# existing row. ALTER TABLE ADD COLUMN has no IF NOT EXISTS in SQLite, so
# each statement is tried independently and a "duplicate column" error
# (already applied) is swallowed -- same pattern as SCHEMA_V23/V29.
SCHEMA_V31 = """
ALTER TABLE vessel_events ADD COLUMN loa_m     REAL;
ALTER TABLE vessel_events ADD COLUMN beam_m    REAL;
ALTER TABLE vessel_events ADD COLUMN draught_m REAL;
"""


def init_db_v31() -> None:
    """Apply v31 schema -- vessel_events.loa_m/beam_m/draught_m. See
    SCHEMA_V31 comment above.

    Honest scope note (2026-08-09, per operator request to break tanker/
    bulk-carrier traffic down by weight class -- Panamax, Neopanamax,
    Suezmax, Aframax, VLCC/ULCC, Handysize/Handymax/Capesize/VLOC, etc.):
    those classes are conventionally defined by vessel length/beam/draught
    (constrained by which canals/straits a hull can transit), NOT by the
    AIS ship_type code, which only has coarse 10-wide buckets (70-79
    "cargo", 80-89 "tanker") with no size information at all. AIS Static
    and Voyage Data messages (Type 5) DO carry dimension-to-bow/stern/
    port/starboard fields that let length-overall and beam be derived --
    but nothing in this codebase captures them yet: _norm_vessel() in
    runner/main.py (all three sources -- local, aishub.net, Kpler) reads
    only position/nav fields today, and AISHub's exact field names for
    those dimensions (commonly A/B/C/D/DRAUGHT in AIS parlance, but not
    confirmed against a real AISHub payload in this codebase) need
    verifying against a live response once AIS_AISHUB_ID is actually
    registered, rather than guessed and shipped unverified. This migration
    only adds the columns so _tanker_weight_class()/_bulk_carrier_
    weight_class() below have somewhere to read from the moment that
    ingestion wiring lands -- both currently return "insufficient_data"
    for every row, honestly, since loa_m is never populated yet."""
    with conn() as c:
        for stmt in SCHEMA_V31.strip().split(";"):
            stmt = stmt.strip()
            if not stmt:
                continue
            try:
                c.execute(stmt)
            except sqlite3.OperationalError as e:
                if "duplicate column" not in str(e).lower():
                    raise


# 2026-08-12: additive columns for a new osint_scopes scope_type="event" --
# a named, dated, venue-bound occurrence (conference, summit, forum) as
# opposed to the existing scope_types (keyword/ep_*/brand_monitor/
# market_intel/competitor/marketing), none of which carry any structure
# beyond a free-text query_terms/label pair. Added for COS26 (the Global
# Chief of Staff Dialogue, Oct 22-23 2026, Atlantic Council, Washington DC)
# -- a geopolitical conference relevant to [operator LLC] both as
# EP/logistics context (VIP ground transport demand around the venue) and
# as a marketing/positioning opportunity (see osint_monitor.py's
# _EVENT_SCOPE_TYPES narrative handling). Same idempotent ALTER-TABLE
# pattern as SCHEMA_V31 -- each statement tried independently, "duplicate
# column" swallowed so this is safe to re-run.
SCHEMA_V32 = """
ALTER TABLE osint_scopes ADD COLUMN event_name TEXT;
ALTER TABLE osint_scopes ADD COLUMN audience   TEXT;
ALTER TABLE osint_scopes ADD COLUMN genre      TEXT;
"""


def init_db_v32() -> None:
    """Apply v32 schema -- osint_scopes.event_name/audience/genre. See
    SCHEMA_V32 comment above."""
    with conn() as c:
        for stmt in SCHEMA_V32.strip().split(";"):
            stmt = stmt.strip()
            if not stmt:
                continue
            try:
                c.execute(stmt)
            except sqlite3.OperationalError as e:
                if "duplicate column" not in str(e).lower():
                    raise


# 2026-08-12: cross-outlet story clustering for osint_items. Operator
# observation: Google News RSS titles are formatted "Headline - Outlet
# Name", and the same real-world story (e.g. COS26 coverage) routinely
# shows up as several separate osint_items rows -- one per outlet -- with
# an identical headline. osint_monitor.py now splits that at ingest time
# into headline/outlet, and fingerprints the normalized headline into
# story_key so items covering the same story (same headline, different
# outlet) can be clustered client-side instead of appearing as unrelated
# duplicate-looking rows. Deliberately exact-normalized-match, not fuzzy
# similarity -- see osint_monitor._story_key docstring for why.
SCHEMA_V33 = """
ALTER TABLE osint_items ADD COLUMN headline  TEXT;
ALTER TABLE osint_items ADD COLUMN outlet    TEXT;
ALTER TABLE osint_items ADD COLUMN story_key TEXT;
CREATE INDEX IF NOT EXISTS idx_osint_items_story ON osint_items(story_key);
"""


def init_db_v33() -> None:
    """Apply v33 schema -- osint_items.headline/outlet/story_key. See
    SCHEMA_V33 comment above."""
    with conn() as c:
        for stmt in SCHEMA_V33.strip().split(";"):
            stmt = stmt.strip()
            if not stmt:
                continue
            try:
                c.execute(stmt)
            except sqlite3.OperationalError as e:
                if "duplicate column" not in str(e).lower():
                    raise


def insert_safety_status_history(airport: str, control: str | None,
                                  previous_bitmask: str | None,
                                  new_bitmask: str, changed_at: str) -> None:
    """Append one bitmask-change row. Caller (check_incursion_alert)
    already knows this is a real change and already scoped to
    STDDS_SAFETY_HISTORY_AIRPORTS -- this function does not re-check
    either, it just writes."""
    with conn() as c:
        c.execute("""
            INSERT INTO stdds_safety_status_history
                (airport, control, previous_bitmask, new_bitmask, changed_at)
            VALUES (?, ?, ?, ?, ?)
        """, (airport, control, previous_bitmask, new_bitmask, changed_at))


def get_safety_status_history(airport: str | None = None,
                               limit: int = 500) -> list[dict]:
    with conn() as c:
        if airport:
            rows = c.execute(
                "SELECT * FROM stdds_safety_status_history WHERE airport=? "
                "ORDER BY id DESC LIMIT ?", (airport, limit)
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM stdds_safety_status_history "
                "ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]


def upsert_safety_status(airport: str, control: str | None,
                          status_bitmask: str, last_seen: str) -> str | None:
    """Upsert the latest SafetyLogicHoldBar status for an airport. Returns
    the PREVIOUS status_bitmask value (None if this airport has never been
    seen before), so the caller can detect a change without a separate
    SELECT."""
    with conn() as c:
        row = c.execute(
            "SELECT status_bitmask FROM stdds_safety_status WHERE airport=?",
            (airport,)
        ).fetchone()
        previous = row[0] if row else None
        c.execute("""
            INSERT INTO stdds_safety_status (airport, control, status_bitmask, last_seen)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(airport) DO UPDATE SET
                control=excluded.control,
                status_bitmask=excluded.status_bitmask,
                last_seen=excluded.last_seen
        """, (airport, control, status_bitmask, last_seen))
    return previous


def get_safety_status(airport: str | None = None) -> list[dict]:
    with conn() as c:
        if airport:
            rows = c.execute(
                "SELECT * FROM stdds_safety_status WHERE airport=?", (airport,)
            ).fetchall()
        else:
            rows = c.execute("SELECT * FROM stdds_safety_status ORDER BY airport").fetchall()
        return [dict(r) for r in rows]


def upsert_surface_movement_event(track_id: str, airport: str, callsign: str | None,
                                   event: str | None, status: str | None,
                                   runway: str | None, latitude: float | None,
                                   longitude: float | None, altitude_ft: float | None,
                                   event_time: str | None, departure_airport: str | None,
                                   destination_airport: str | None, last_seen: str) -> None:
    with conn() as c:
        c.execute("""
            INSERT INTO surface_movement_events
                (track_id, airport, callsign, event, status, runway,
                 latitude, longitude, altitude_ft, event_time,
                 departure_airport, destination_airport, last_seen)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(airport, track_id) DO UPDATE SET
                callsign=excluded.callsign,
                event=excluded.event,
                status=excluded.status,
                runway=excluded.runway,
                latitude=excluded.latitude,
                longitude=excluded.longitude,
                altitude_ft=excluded.altitude_ft,
                event_time=excluded.event_time,
                departure_airport=excluded.departure_airport,
                destination_airport=excluded.destination_airport,
                last_seen=excluded.last_seen
        """, (track_id, airport, callsign, event, status, runway,
              latitude, longitude, altitude_ft, event_time,
              departure_airport, destination_airport, last_seen))


def count_onsurface(airport: str) -> int:
    """Count distinct aircraft whose LATEST known surface-movement status
    is 'onsurface' (taxiing, not yet on/off the runway) at this airport --
    the "how many are in the taxi phase right now" gauge."""
    with conn() as c:
        row = c.execute(
            "SELECT count(*) FROM surface_movement_events WHERE airport=? AND status='onsurface'",
            (airport,)
        ).fetchone()
        return row[0] if row else 0


def get_surface_movement_events(airport: str | None = None) -> list[dict]:
    with conn() as c:
        if airport:
            rows = c.execute(
                "SELECT * FROM surface_movement_events WHERE airport=? ORDER BY last_seen DESC",
                (airport,)
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM surface_movement_events ORDER BY airport, last_seen DESC"
            ).fetchall()
        return [dict(r) for r in rows]


def _parse_trains_json_to_rows(trains_json: str) -> list[tuple]:
    """Parse a trains_json blob (as already stored in amtrak_status) into
    train_events row tuples. Best-effort -- an Amtrak feed schema change
    should degrade to fewer populated columns, never raise.

    2026-07-28 fix: field names confirmed against a real stored blob, not
    guessed from the documented /api/v1/amtrak *response* contract (which
    turned out to describe a different, reshaped view -- the raw ingest
    blob uses train_num/route/origin/destination/station_code/station_name
    /scheduled_dep/estimated_dep, not train_number/route_name/direction/
    scheduled_time). Each array entry is this train's status AT ONE
    STATION at fetch time (a multi-stop train appears once per stop it's
    currently near), not one flat door-to-door record -- station_code
    identifies which stop this particular row is about."""
    import json as _json
    try:
        trains = _json.loads(trains_json)
    except Exception:
        return []
    if not isinstance(trains, list):
        return []
    rows = []
    for t in trains:
        if not isinstance(t, dict):
            continue
        rows.append((
            t.get("train_num"),
            None,  # train_name -- no distinct field in the raw blob
            t.get("route"),
            None,  # direction -- not present in the raw blob
            t.get("origin"),
            t.get("destination"),
            t.get("station_code"),
            t.get("station_name"),
            t.get("scheduled_dep") or t.get("scheduled_arr"),
            t.get("estimated_dep") or t.get("estimated_arr"),
            t.get("status"),
            t.get("delay_minutes"),
            None,  # platform -- not present in the raw blob
        ))
    return rows


def backfill_train_events_from_amtrak_status(force: bool = False) -> int:
    """One-time migration: unpack every existing amtrak_status.trains_json
    blob into train_events rows, so train_events starts with amtrak_status's
    full retained history instead of an empty table. Safe to call more than
    once -- no-ops if train_events already has rows, since amtrak_status is
    the entire source of truth here and re-running would just duplicate it.
    Returns the number of rows inserted (0 if skipped)."""
    with conn() as c:
        existing = c.execute("SELECT COUNT(*) AS n FROM train_events").fetchone()
        if existing and existing["n"] > 0 and not force:
            return 0
        if force:
            c.execute("DELETE FROM train_events")
        blobs = c.execute(
            "SELECT trains_json FROM amtrak_status ORDER BY fetched_at ASC"
        ).fetchall()
        inserted = 0
        for b in blobs:
            for row in _parse_trains_json_to_rows(b["trains_json"]):
                c.execute("""
                    INSERT INTO train_events
                    (train_number, train_name, route_name, direction,
                     origin, destination, station_code, station_name,
                     scheduled_time, estimated_time, status, delay_minutes, platform)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, row)
                inserted += 1
        return inserted


def insert_vessel_event(mmsi: str, name, lat, lon, sog, cog, hdg,
                         nav_status, ship_type, source: str) -> None:
    with conn() as c:
        c.execute("""
            INSERT INTO vessel_events
            (mmsi, name, lat, lon, sog, cog, hdg, nav_status, ship_type, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (mmsi, name, lat, lon, sog, cog, hdg, nav_status, ship_type, source))


def analyze_train_patterns(min_samples: int = 5, drift_minutes_flag: int = 10) -> dict:
    """train_events analog of analyze_flight_number_patterns. Per
    train_number: dominant route_name/direction (Amtrak numbers are already
    carrier-fixed, so this mostly confirms consistency rather than
    discovering it the way the flight side does), plus schedule-time drift
    -- mean scheduled time-of-day across the oldest third of samples vs the
    newest third, flagged when the shift exceeds drift_minutes_flag.

    Honest caveat: train_events only goes back as far as amtrak_status's
    retained history (backfilled once via backfill_train_events_from_
    amtrak_status). A real multi-year drift -- the 5:45->6:10 shuttle creep
    the operator described -- needs years of data this system has not been
    running long enough to have. This mechanism is real and starts
    compounding a genuine baseline from today forward; it is not a 3-year
    lookback on day one."""
    import re as _re
    from collections import defaultdict

    def _time_of_day_minutes(ts):
        if not ts:
            return None
        m = _re.search(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", str(ts))
        if not m:
            return None
        return int(m.group(1)) * 60 + int(m.group(2))

    with conn() as c:
        rows = c.execute("""
            SELECT train_number, route_name, origin, destination,
                   station_code, scheduled_time, fetched_at
            FROM train_events
            WHERE train_number IS NOT NULL
            ORDER BY train_number, fetched_at ASC
        """).fetchall()

    by_train = defaultdict(list)
    by_train_station = defaultdict(list)
    for r in rows:
        by_train[r["train_number"]].append(r)
        by_train_station[(r["train_number"], r["station_code"])].append(r)

    route_locks = []
    for train_number, samples in by_train.items():
        total = len(samples)
        if total < min_samples:
            continue
        route_counts: dict = {}
        for s in samples:
            key = (s["route_name"], s["origin"], s["destination"])
            route_counts[key] = route_counts.get(key, 0) + 1
        top_route, top_count = max(route_counts.items(), key=lambda kv: kv[1])
        dominance = top_count / total
        route_locks.append({
            "train_number": train_number, "route_name": top_route[0],
            "origin": top_route[1], "destination": top_route[2],
            "dominance": round(dominance, 3), "samples": total,
        })

    drift_flags = []
    for (train_number, station_code), samples in by_train_station.items():
        if not station_code:
            continue
        tod = [_time_of_day_minutes(s["scheduled_time"]) for s in samples]
        tod = [t for t in tod if t is not None]
        if len(tod) < min_samples:
            continue
        third = max(1, len(tod) // 3)
        old_avg = sum(tod[:third]) / third
        new_avg = sum(tod[-third:]) / third
        shift = new_avg - old_avg
        if abs(shift) >= drift_minutes_flag:
            drift_flags.append({
                "train_number": train_number,
                "station_code": station_code,
                "shift_minutes": round(shift, 1),
                "old_avg_time_of_day_min": round(old_avg, 1),
                "new_avg_time_of_day_min": round(new_avg, 1),
                "samples": len(tod),
            })

    return {
        "route_locks": route_locks,
        "schedule_drift_flags": drift_flags,
        "sample_window": "train_events (backfilled from amtrak_status history)",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def _vessel_class_for_ship_type(ship_type: str | None) -> str:
    """Classify an AIS ship-type code (ITU-R M.1371 numeric code, stored
    as-is from AISHub's shiptype/TYPE/shipType fields -- see runner/main.py)
    into the buckets ITU-R M.1371 actually distinguishes. Expanded
    2026-08-09 per operator request to cover the full multi-domain set
    (motor vessels, motor/sailing/pleasure yachts and vessels, passenger/
    cruise, patrol, cargo, tanker) rather than only passenger/cargo/tanker.

    36 ("Sailing") and 37 ("Pleasure Craft") are dedicated AIS codes --
    independent confirmation for the SV/SY and MY/PY name-prefix signal
    below, not a guess. 55 ("Law Enforcement") is the closest AIS code to
    "patrol vessel" (PV); there is no dedicated AIS code for "motor
    vessel" specifically since that describes propulsion, not vessel
    class -- MV is only derivable from the name prefix.

    60-69 ("Passenger") remains the closest available signal for cruise
    ships specifically -- AIS has no dedicated cruise code, so a cruise
    ship and a harbor water-taxi both report somewhere in 60-69.

    70-79 ("Cargo") and 80-89 ("Tanker") are as granular as AIS ship_type
    gets: container ship vs. bulk/general cargo, and crude vs. product vs.
    chemical tanker, are NOT distinguishable from this code alone -- that
    would need an IMO-number cross-reference against a vessel-type
    registry (e.g. IHS Markit/Lloyd's or a free IMO dataset), a separate
    future data source, not something AIS itself carries. Weight-class
    breakdown for cargo/tanker (Panamax, Neopanamax, Suezmax, Aframax,
    VLCC/ULCC, Handysize/Handymax/Capesize/VLOC) is handled separately by
    _tanker_weight_class()/_bulk_carrier_weight_class() below, gated on
    loa_m (see SCHEMA_V31) -- not by this function, since size and cargo
    type are independent axes."""
    try:
        code = int(ship_type)
    except (TypeError, ValueError):
        return "unknown"
    if code == 36:
        return "sailing"
    if code == 37:
        return "pleasure_craft"
    if code == 50:
        return "pilot_vessel"
    if code == 51:
        return "search_and_rescue"
    if code == 55:
        return "law_enforcement/patrol"
    if 60 <= code <= 69:
        return "passenger/cruise-class"
    if 70 <= code <= 79:
        return "cargo"
    if 80 <= code <= 89:
        return "tanker"
    return "other"


# Tanker weight classes, by length-overall (LOA, meters) -- the
# conventional definition (constrained by which canals/straits a hull can
# transit) uses deadweight tonnage (DWT), which AIS does not carry at all;
# LOA is the closest proxy derivable from AIS dimension fields (once
# ingested -- see SCHEMA_V31/init_db_v31), so these bands are approximate
# and meant to be replaced with real DWT thresholds if a vessel-registry
# lookup is ever added. Bands drawn from standard tanker-class LOA ranges:
# Panamax ~228m, Aframax ~245m, Suezmax ~275m, VLCC ~330m, ULCC ~350m+.
_TANKER_WEIGHT_CLASSES = (
    (228, "Panamax"),
    (245, "Aframax"),
    (275, "Suezmax"),
    (330, "VLCC"),
    (float("inf"), "ULCC"),
)

# Bulk-carrier weight classes, by LOA -- same DWT-vs-LOA caveat as tankers
# above. Bands: Handysize ~190m, Handymax/Supramax ~200m, Panamax ~225m,
# Capesize ~300m, VLOC (Very Large Ore Carrier) 300m+.
_BULK_CARRIER_WEIGHT_CLASSES = (
    (190, "Handysize"),
    (200, "Handymax/Supramax"),
    (225, "Panamax"),
    (300, "Capesize"),
    (float("inf"), "VLOC"),
)

# Neopanamax (New Panamax, post-2016 expanded locks): ~366m LOA / 49m beam
# ceiling, spans the top of several of the classes above -- checked first
# since it's a beam-gated class the LOA-only bands can't express alone.
_NEOPANAMAX_MAX_LOA_M = 366.0
_NEOPANAMAX_MAX_BEAM_M = 49.0


def _weight_class_from_loa(loa_m: float | None, beam_m: float | None,
                            bands: tuple) -> str:
    """Shared lookup for _tanker_weight_class/_bulk_carrier_weight_class.
    Returns "insufficient_data" when loa_m is missing -- honest today for
    every row, since nothing populates loa_m yet (see SCHEMA_V31).

    Neopanamax is beam-gated (~49m ceiling), not just length-gated, so it
    can't be expressed as one more LOA-only band in the tuple below without
    also silently reclassifying real VLCC/Capesize/VLOC/ULCC hulls that
    happen to share a similar length but are actually too beamy for the
    expanded locks. It's checked as a special case instead, and only when
    beam_m is actually known -- without it, a wide hull can't be told apart
    from a Neopanamax one on length alone, so this deliberately falls
    through to the plain LOA bands rather than guessing."""
    if loa_m is None:
        return "insufficient_data"
    second_largest_max = bands[-2][0]
    if (beam_m is not None and loa_m > second_largest_max
            and loa_m <= _NEOPANAMAX_MAX_LOA_M and beam_m <= _NEOPANAMAX_MAX_BEAM_M):
        return "Neopanamax"
    for max_loa, label in bands:
        if loa_m <= max_loa:
            return label
    return bands[-1][1]


def _tanker_weight_class(loa_m: float | None, beam_m: float | None = None) -> str:
    """See _TANKER_WEIGHT_CLASSES/_weight_class_from_loa docstrings.
    Not yet wired to real data -- see init_db_v31()."""
    return _weight_class_from_loa(loa_m, beam_m, _TANKER_WEIGHT_CLASSES)


def _bulk_carrier_weight_class(loa_m: float | None, beam_m: float | None = None) -> str:
    """See _BULK_CARRIER_WEIGHT_CLASSES/_weight_class_from_loa docstrings.
    Not yet wired to real data -- see init_db_v31()."""
    return _weight_class_from_loa(loa_m, beam_m, _BULK_CARRIER_WEIGHT_CLASSES)


# Standard maritime vessel-name prefixes -- independent signal from the AIS
# ship_type code, and often more reliable in practice since ship_type is
# self-reported and frequently left at a generic/default value, while the
# name prefix is baked into the vessel's registered name itself. Ordered
# longest-first so a two-letter prefix is checked before any shorter
# collision (none currently, kept for safety if more are added later).
_VESSEL_NAME_PREFIXES = ("MV", "MY", "SV", "SY", "PV", "PY")


def _vessel_name_prefix(name: str | None) -> str | None:
    """Extract a standard vessel-name prefix if the vessel's AIS-reported
    name starts with one, space- or period-separated (e.g. "MV FREEDOM OF
    THE SEAS", "M/V FREEDOM..."). Added 2026-08-09 per operator request --
    each of MV (motor vessel), MY (motor yacht), SV (sailing vessel), SY
    (sailing yacht), PV (patrol vessel), PY (pleasure yacht) needs to be
    independently queryable in analyze_vessel_patterns()'s output, not
    folded into a single class. Cruise ships most commonly carry MV (large
    passenger/cargo motor vessels) or occasionally MY: this prefix and the
    ship_type-derived vessel_class are deliberately kept as two separate
    fields since either alone can miss what the other catches (ship_type
    is self-reported and often stale/generic; not every vessel's name
    carries a prefix at all)."""
    if not name:
        return None
    cleaned = name.strip().upper().replace("/", "").replace(".", "")
    first_token = cleaned.split(" ", 1)[0] if cleaned else ""
    if first_token in _VESSEL_NAME_PREFIXES:
        return first_token
    return None


def analyze_vessel_patterns(min_samples: int = 5) -> dict:
    """vessel_events analog of analyze_flight_number_patterns. Vessels have
    no flight-number equivalent, so "route-lock" here means: per-MMSI, does
    one coarse position cluster (rounded to ~0.01 deg, ~1km) dominate --
    e.g. the National Harbor<->Alexandria water taxi run should show up as
    a small, repeated set of clusters rather than scattered positions.
    Only populated for watchlisted vessels seen via the AISHub sweep so
    far (see _check_vessel_aishub in poller/main.py) -- local AIS-catcher
    hardware and the Kpler/MarineTraffic path are not wired into this
    capture yet."""
    from collections import defaultdict

    with conn() as c:
        rows = c.execute("""
            SELECT mmsi, name, lat, lon, ship_type, loa_m, beam_m FROM vessel_events
            WHERE mmsi IS NOT NULL AND lat IS NOT NULL AND lon IS NOT NULL
        """).fetchall()

    by_mmsi = defaultdict(list)
    for r in rows:
        by_mmsi[r["mmsi"]].append(r)

    route_locks = []
    for mmsi, samples in by_mmsi.items():
        total = len(samples)
        if total < min_samples:
            continue
        cluster_counts: dict = {}
        for s in samples:
            try:
                key = (round(float(s["lat"]), 2), round(float(s["lon"]), 2))
            except (TypeError, ValueError):
                continue
            cluster_counts[key] = cluster_counts.get(key, 0) + 1
        if not cluster_counts:
            continue
        top_cluster, top_count = max(cluster_counts.items(), key=lambda kv: kv[1])
        dominance = top_count / total
        vclass = _vessel_class_for_ship_type(samples[0]["ship_type"])
        loa_m = samples[0]["loa_m"]
        beam_m = samples[0]["beam_m"]
        # Weight class is only meaningful for the two bulk-cargo-carrying
        # classes -- a passenger/sailing/pleasure/patrol vessel doesn't
        # have a Panamax/Capesize equivalent, so this stays None for those
        # rather than reporting a misleading "insufficient_data" on
        # classes it was never meant to apply to.
        if vclass == "tanker":
            weight_class = _tanker_weight_class(loa_m, beam_m)
        elif vclass == "cargo":
            weight_class = _bulk_carrier_weight_class(loa_m, beam_m)
        else:
            weight_class = None
        route_locks.append({
            "mmsi": mmsi, "name": samples[0]["name"],
            "vessel_class": vclass,
            "name_prefix": _vessel_name_prefix(samples[0]["name"]),
            "weight_class": weight_class,
            "dominant_cluster_lat": top_cluster[0], "dominant_cluster_lon": top_cluster[1],
            "dominance": round(dominance, 3), "samples": total,
            "distinct_clusters": len(cluster_counts),
        })
    route_locks.sort(key=lambda x: -x["samples"])

    name_prefix_counts = {p: 0 for p in _VESSEL_NAME_PREFIXES}
    for x in route_locks:
        if x["name_prefix"]:
            name_prefix_counts[x["name_prefix"]] += 1

    return {
        "route_locks": route_locks,
        "passenger_cruise_class_count": sum(
            1 for x in route_locks if x["vessel_class"] == "passenger/cruise-class"
        ),
        # Independently queryable per-prefix counts (MV/MY/SV/SY/PV/PY) --
        # see _vessel_name_prefix docstring for why this is kept separate
        # from vessel_class/passenger_cruise_class_count rather than merged.
        "name_prefix_counts": name_prefix_counts,
        "sample_window": "vessel_events (accumulating from today forward, AISHub-watchlisted sweep only)",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def analyze_disruption_weather_split(days: int = 30, facilities: list[str] | None = None) -> dict:
    """Generalizes the 2026-08-09 ad hoc nas_programs weather-vs-facility
    analysis (see that session's vault note,
    01-Sources/transport-patterns/2026-08-09-30day-facility-weather-
    analysis.md) into reusable code for the new recurring disruption-
    weather digest.

    nas_programs is the only transport-modality source in this platform
    with a genuinely reliable, FAA-sourced, per-event REASON string (e.g.
    "WX:Thunderstorms", "VOL:Compacted Demand", "RWY:Construction").
    Deliberately NOT based on flight_events.status='cancelled' -- that
    field was confirmed contaminated by FDPS flight-PLAN-cancellation
    noise (refiled/amended plans, not real operational cancellations),
    see the same vault note for the full root-cause trace.

    facilities: defaults to the DC-area + major connecting-hub list used
    in the 2026-08-09 session; pass an explicit list to scope elsewhere.
    """
    if facilities is None:
        facilities = ["DCA", "IAD", "BWI", "ORD", "ATL", "EWR", "LGA", "JFK",
                      "MCO", "DFW", "MIA", "FLL", "DEN", "BOS", "MDW", "SFO",
                      "CLT", "PHL", "IAH", "LAS", "LAX", "MSP"]

    placeholders = ",".join("?" for _ in facilities)
    with conn() as c:
        rows = c.execute(f"""
            SELECT facility, json_extract(raw_json,'$.reason') as reason
            FROM nas_programs
            WHERE fetched_at >= strftime('%s','now',?)
              AND facility IN ({placeholders})
        """, (f"-{days} days", *facilities)).fetchall()

    from collections import defaultdict
    stats: dict = defaultdict(lambda: {"total": 0, "weather": 0})
    for r in rows:
        reason = r["reason"] or ""
        stats[r["facility"]]["total"] += 1
        if reason.startswith("WX:"):
            stats[r["facility"]]["weather"] += 1

    facility_breakdown = []
    for facility, s in stats.items():
        total = s["total"]
        weather = s["weather"]
        facility_breakdown.append({
            "facility": facility, "total_programs": total,
            "weather_driven": weather, "facility_or_other": total - weather,
            "pct_weather": round(100.0 * weather / total, 1) if total else None,
        })
    facility_breakdown.sort(key=lambda x: -x["total_programs"])

    return {
        "facility_breakdown": facility_breakdown,
        "lookback_days": days,
        "sample_window": f"nas_programs (real {days}-day FAA/SWIM TFMS history)",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def analyze_train_disruption_summary(days: int = 30, delay_threshold_minutes: int = 15,
                                      nec_facilities: list[str] | None = None) -> dict:
    """Train-side counterpart to analyze_disruption_weather_split(). Real
    delay-rate/severity metrics come straight from train_events.delay_minutes
    (genuinely reported by Amtrak's own amtraker v3 feed).

    A true per-delay weather-vs-non-weather split -- the way
    nas_programs.reason gives flights -- is NOT possible from data this
    platform ingests: confirmed 2026-08-09 that no "cause"/"reason"/
    "delayReason" field exists anywhere in ingest/amtrak.py,
    poller/fetchers/amtrak.py, or train_events' own schema, and
    nws_alerts holds no retained history (current-snapshot-only, actively
    expiring inactive alerts -- see feed_db_integrity_check.py's
    docstring). Fabricating a per-train weather attribution from data
    that doesn't carry one would repeat the exact "cancelled means
    cancelled" mistake already caught and discarded for flight_events
    tonight.

    What this DOES provide instead, explicitly labeled as a REGIONAL
    PROXY rather than a per-train attribution: how many distinct days in
    the lookback window had a confirmed WX:-tagged nas_programs entry at
    a Northeast-Corridor-relevant airport (real FAA-sourced ground truth,
    just for aviation, not rail) -- context for whether the window was
    regionally weather-heavy, not a claim about any specific train
    delay's actual cause."""
    if nec_facilities is None:
        nec_facilities = ["DCA", "BWI", "PHL", "EWR", "JFK", "LGA", "BOS"]

    with conn() as c:
        train_rows = c.execute("""
            SELECT train_number, route_name, delay_minutes
            FROM train_events
            WHERE fetched_at >= strftime('%s','now',?)
              AND delay_minutes IS NOT NULL
        """, (f"-{days} days",)).fetchall()

    from collections import defaultdict
    by_train: dict = defaultdict(list)
    for r in train_rows:
        by_train[(r["train_number"], r["route_name"])].append(r["delay_minutes"])

    delay_summary = []
    for (train_number, route_name), delays in by_train.items():
        total = len(delays)
        if total < 3:
            continue
        avg_delay = sum(delays) / total
        pct_over_threshold = 100.0 * sum(1 for d in delays if d >= delay_threshold_minutes) / total
        delay_summary.append({
            "train_number": train_number, "route_name": route_name,
            "samples": total, "avg_delay_minutes": round(avg_delay, 1),
            "pct_over_threshold": round(pct_over_threshold, 1),
        })
    delay_summary.sort(key=lambda x: -x["pct_over_threshold"])

    placeholders = ",".join("?" for _ in nec_facilities)
    with conn() as c:
        wx_row = c.execute(f"""
            SELECT COUNT(DISTINCT date(fetched_at, 'unixepoch')) as n
            FROM nas_programs
            WHERE fetched_at >= strftime('%s','now',?)
              AND facility IN ({placeholders})
              AND json_extract(raw_json,'$.reason') LIKE 'WX:%'
        """, (f"-{days} days", *nec_facilities)).fetchone()
        wx_days = wx_row["n"] if wx_row else 0

    return {
        "delay_summary": delay_summary,
        "regional_weather_context": {
            "wx_flagged_days": wx_days, "window_days": days,
            "facilities_checked": nec_facilities,
            "note": ("Regional proxy only (aviation WX ground-programs near the NEC), "
                     "NOT a per-train delay-cause attribution -- see docstring."),
        },
        "sample_window": f"train_events (real retained history since 2026-07-28 backfill, {days}-day lookback)",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def analyze_vessel_disruption_summary(days: int = 30) -> dict:
    """Maritime counterpart to analyze_disruption_weather_split()/
    analyze_train_disruption_summary(). Structurally ready, but
    vessel_events has zero rows in production as of 2026-08-09
    (AIS_AISHUB_ID not registered -- see analyze_vessel_patterns
    docstring), so this honestly reports insufficient_data rather than
    fabricating a result from an empty table.

    Once real position data flows, this would compute per-MMSI dwell/
    delay anomalies (e.g. unusually long time in a port-approach cluster
    vs. that vessel's own historical norm) the way train delay_minutes
    does today -- but there is no Amtrak-style pre-computed "delay" field
    for vessels; it would need deriving from position-cluster dwell time.
    That derivation is deliberately not built yet since there is no real
    data to validate it against -- writing untested inference logic
    against an empty table risks exactly the kind of unverified,
    plausible-looking-but-wrong signal this session has repeatedly
    caught and discarded elsewhere (flight_events cancellation noise,
    the operator/nextcloud silent-fallback bug)."""
    with conn() as c:
        row = c.execute("SELECT COUNT(*) as n FROM vessel_events").fetchone()
        total = row["n"] if row else 0

    if total == 0:
        return {
            "status": "insufficient_data",
            "reason": ("vessel_events has zero rows -- AIS_AISHUB_ID is not configured, "
                      "so no position data has ever been captured to analyze."),
            "lookback_days": days,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

    return {
        "status": "not_yet_implemented",
        "reason": (f"vessel_events has {total} rows but the dwell-time-anomaly "
                  "derivation logic itself hasn't been built/validated yet."),
        "lookback_days": days,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def update_watchlist_destination(entry_id: str, destination: str) -> None:
    """Persist a confirmed destination change (e.g. an FDPS-detected
    diversion) onto the watchlist entry itself. Without this,
    _check_flight_fdps_cache's destination-change comparison
    (plan.destination vs entry.destination) would never converge -- the
    entry's stored destination never moved, so the same "changed" event
    would re-fire on every tick forever after a genuine diversion."""
    with conn() as c:
        c.execute(
            "UPDATE watchlist_entries SET destination=? WHERE id=?",
            (destination, entry_id),
        )


def update_watchlist_hex_registration(entry_id: str, hex_id: str, registration: str | None) -> None:
    """Persist a confirmed tail/airframe reassignment (e.g. an FDPS-
    detected aircraftAddress/registration change on the same flight
    plan) onto the watchlist entry itself. Same convergence reasoning as
    update_watchlist_destination() -- without this, _check_flight_fdps_
    cache's hex/registration comparison would never converge and the
    same "tail change" event would re-fire on every tick forever after a
    genuine reassignment."""
    with conn() as c:
        c.execute(
            "UPDATE watchlist_entries SET hex_id=?, registration=? WHERE id=?",
            (hex_id, registration, entry_id),
        )
