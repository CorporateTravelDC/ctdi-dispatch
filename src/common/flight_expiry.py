"""common/flight_expiry.py -- close flight_events rows that will never get another message (2026-11-19).

Why: the FDPS parser only ever sets CANCELLED (on CL) or ACTIVE, and the SFDPS departure/arrival messages
(DH, HV) are not parsed yet, so nothing ever closed a row. A watched flight on 2026-11-18 was still 'active' 20 h
after it landed, and /api/v1/flightplan served it as current. This sweep marks such rows 'expired';
db.get_flight_plan_by_callsign never returns an expired row. A later message for the same GUFI writes
the row's status again, so an expired row that turns out to be alive comes back by itself.

A row with status 'active' is expired when its last update (updated_at) is older than:
  - 3 h and its arrival time/ETA is more than 3 h in the past, or
  - 6 h and it carried a position (it was flying, and the track went quiet), or
  - 24 h, whatever it carried.

Load: two bounded passes per run, each LIMIT batch rows, both on the existing updated_at index:
  1. recent window: updated_at between now-30 h and now-3 h (the three rules);
  2. backlog: updated_at older than now-30 h (always eligible under the 24 h rule), walked forward from a
     cursor kept in flight_expiry_state so the expired history is never rescanned.
"""
from __future__ import annotations

import logging
import time

log = logging.getLogger(__name__)

ARRIVAL_GRACE_S = 3 * 3600
POSITION_QUIET_S = 6 * 3600
ANY_QUIET_S = 24 * 3600
WINDOW_S = 30 * 3600
BATCH = 2000

_STATE_DDL = "CREATE TABLE IF NOT EXISTS flight_expiry_state (name TEXT PRIMARY KEY, value DOUBLE PRECISION)"


def _ids(rows) -> list:
    return [r["flight_id"] if isinstance(r, dict) or hasattr(r, "keys") else r[0] for r in rows]


def sweep(now: float | None = None, batch: int = BATCH, db=None) -> dict:
    """One run. Returns {'recent': n, 'backlog': n, 'cursor': t}."""
    from common import db as _db, db_backend
    db = db or _db
    t = time.time() if now is None else now
    out = {"recent": 0, "backlog": 0, "cursor": None}
    with db.conn() as c:
        if db_backend.backend() != "postgres":
            c.execute(_STATE_DDL)
        # pass 1: the recent window, all three rules
        rows = c.execute("""
            SELECT flight_id FROM flight_events
            WHERE updated_at >= ? AND updated_at < ? AND status = 'active'
              AND ( (arrival_time IS NOT NULL AND arrival_time < ?)
                 OR (position_lat IS NOT NULL AND updated_at < ?)
                 OR updated_at < ? )
            ORDER BY updated_at LIMIT ?
        """, (t - WINDOW_S, t - ARRIVAL_GRACE_S, t - ARRIVAL_GRACE_S,
              t - POSITION_QUIET_S, t - ANY_QUIET_S, batch)).fetchall()
        ids = _ids(rows)
        for fid in ids:
            c.execute("UPDATE flight_events SET status = 'expired' WHERE flight_id = ? AND status = 'active'", (fid,))
        out["recent"] = len(ids)

        # pass 2: the backlog older than the window, from the cursor forward
        cur = c.execute("SELECT value FROM flight_expiry_state WHERE name = 'backlog_cursor'").fetchone()
        cursor = float((cur["value"] if hasattr(cur, "keys") else cur[0]) if cur else 0.0)
        rows = c.execute("""
            SELECT flight_id, updated_at FROM flight_events
            WHERE updated_at >= ? AND updated_at < ? AND status = 'active'
            ORDER BY updated_at LIMIT ?
        """, (cursor, t - WINDOW_S, batch)).fetchall()
        last = cursor
        for r in rows:
            fid = r["flight_id"] if hasattr(r, "keys") else r[0]
            ts = float(r["updated_at"] if hasattr(r, "keys") else r[1])
            c.execute("UPDATE flight_events SET status = 'expired' WHERE flight_id = ? AND status = 'active'", (fid,))
            last = max(last, ts)
        if len(rows) < batch:
            last = max(last, t - WINDOW_S)  # caught up: everything before the window is done
        c.execute("""INSERT INTO flight_expiry_state (name, value) VALUES ('backlog_cursor', ?)
                     ON CONFLICT (name) DO UPDATE SET value = excluded.value""", (last,))
        out["backlog"] = len(rows)
        out["cursor"] = last
    if out["recent"] or out["backlog"]:
        log.info("flight expiry: closed %d recent + %d backlog active row(s) as 'expired' (cursor %s)",
                 out["recent"], out["backlog"], time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime(out["cursor"])))
    return out
