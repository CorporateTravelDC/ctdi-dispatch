"""
common/gate_aeroapi.py -- FlightAware AeroAPI as the gate / baggage FALLBACK ONLY
(2026-10-18, operator, gate batch).

Called only when no higher source (SWIM, ACARS) has a value for a WATCHED flight's
arrival gate (or, outside DCA/IAD/BWI, its baggage claim), and then at most twice
per flight leg: once ~45 min before the ETA ('pre_eta') and once after ON
('post_on'). A daily cap (AEROAPI_GATE_DAILY_CAP, default 50) bounds the total.
Every call is ledgered in aeroapi_gate_calls BEFORE it is made; the table's
UNIQUE (leg_key, purpose) is what holds the per-leg limit, across restarts.
Baggage at DCA/IAD/BWI is never taken from here: FIDS is the only DC carousel
source (plus proven ACARS formats).

The key (FLIGHTAWARE_API_KEY, dispatch-secrets.env) is read from the
environment, sent only in the x-apikey header, and never logged or printed.
AeroAPI time fields are used only to pick the flight that matches the leg; no
AeroAPI time or status is stored.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone

import requests

from common import db
from common import flight_instance as fi
from common import gate_assignments as ga

log = logging.getLogger(__name__)

AEROAPI_BASE = "https://aeroapi.flightaware.com/aeroapi"
TIMEOUT_S = 15
PRE_ETA_LEAD_S = 45 * 60
POST_ON_MAX_S = 3 * 3600          # after ON, only while the arrival is recent
PER_LEG_MAX = 2


def _key() -> str | None:
    return (os.environ.get("FLIGHTAWARE_API_KEY") or "").strip() or None


def daily_cap() -> int:
    try:
        return max(0, int(os.environ.get("AEROAPI_GATE_DAILY_CAP", "50")))
    except ValueError:
        return 50


def _today(now: float) -> str:
    return datetime.fromtimestamp(now, tz=timezone.utc).strftime("%Y-%m-%d")


def calls_today(now: float | None = None) -> int:
    now = time.time() if now is None else now
    with db.conn() as c:
        ga.ensure_tables(c)
        r = c.execute("SELECT count(*) AS n FROM aeroapi_gate_calls WHERE call_day = ?", (_today(now),)).fetchone()
    return int(dict(r)["n"]) if r else 0


def calls_for_leg(leg_key: str) -> set[str]:
    with db.conn() as c:
        ga.ensure_tables(c)
        rows = c.execute("SELECT purpose FROM aeroapi_gate_calls WHERE leg_key = ?", (leg_key,)).fetchall()
    return {dict(r)["purpose"] for r in rows}


def _claim(leg_key: str, purpose: str, now: float) -> int | None:
    """Ledger the call before making it. None when the (leg, purpose) slot was already used."""
    with db.conn() as c:
        ga.ensure_tables(c)
        cur = c.execute("INSERT INTO aeroapi_gate_calls (called_at, call_day, leg_key, purpose, outcome) "
                        "VALUES (?, ?, ?, ?, 'pending') ON CONFLICT (leg_key, purpose) DO NOTHING",
                        (ga._iso(now), _today(now), leg_key, purpose))
        if not (cur.rowcount or 0):
            return None
        r = c.execute("SELECT id FROM aeroapi_gate_calls WHERE leg_key = ? AND purpose = ?",
                      (leg_key, purpose)).fetchone()
    return int(dict(r)["id"]) if r else None


def _finish(call_id: int, http_status: int | None, outcome: str) -> None:
    with db.conn() as c:
        c.execute("UPDATE aeroapi_gate_calls SET http_status = ?, outcome = ? WHERE id = ?",
                  (http_status, outcome[:60], call_id))


def _higher_value(key: fi.FlightInstanceKey, field: str, now: float) -> bool:
    """A SWIM or ACARS value exists for this arrival field (AeroAPI is not needed)."""
    with db.conn() as c:
        ga.ensure_tables(c)
        rows = [dict(r) for r in c.execute(
            "SELECT * FROM gate_baggage_observations WHERE leg_key = ? AND side = 'arrival' AND field = ?",
            (str(key), field)).fetchall()]
    higher = [r for r in rows if ga.SOURCE_TIERS.get(r["source"], 0) > ga.SOURCE_TIERS["aeroapi"]]
    return ga.resolve(higher, now).status in ("observed", "provisional", "conflicting")


def needed_fields(key: fi.FlightInstanceKey, now: float | None = None) -> list[str]:
    now = time.time() if now is None else now
    fields = ["gate"]
    if key.destination not in ga.DC_AIRPORTS:
        fields.append("baggage")
    return [f for f in fields if not _higher_value(key, f, now)]


def due_purpose(key: fi.FlightInstanceKey, *, eta: float | None, phase: str | None,
                phase_at: float | None, now: float | None = None) -> str | None:
    """Which of the two per-leg calls is due now, or None."""
    now = time.time() if now is None else now
    used = calls_for_leg(str(key))
    if len(used) >= PER_LEG_MAX:
        return None
    if (phase or "").lower() in ("on", "in"):
        if "post_on" not in used and (phase_at is None or now - phase_at <= POST_ON_MAX_S):
            return "post_on"
        return None
    if eta is not None and "pre_eta" not in used and eta - PRE_ETA_LEAD_S <= now < eta:
        return "pre_eta"
    return None


def _pick_flight(flights: list[dict], key: fi.FlightInstanceKey) -> dict | None:
    picked = []
    for f in flights:
        o = (f.get("origin") or {})
        d = (f.get("destination") or {})
        if fi.airports_agree(o.get("code_iata") or o.get("code"), key.origin) is not True:
            continue
        if fi.airports_agree(d.get("code_iata") or d.get("code"), key.destination) is not True:
            continue
        if fi.utc_date(f.get("scheduled_out")) != key.sched_date:
            continue
        picked.append(f)
    return picked[0] if len(picked) == 1 else None


def fetch(key: fi.FlightInstanceKey, purpose: str, fields: list[str], *, now: float | None = None,
          session=requests) -> dict:
    """One budgeted call. Returns {'called': bool, 'stored': n, 'reason': str}."""
    now = time.time() if now is None else now
    api_key = _key()
    if not api_key:
        return {"called": False, "stored": 0, "reason": "no_key"}
    if not key.sched_date or not key.origin or not key.destination:
        return {"called": False, "stored": 0, "reason": "undated_leg"}
    if ga.ladd_blocked([key.callsign]):
        return {"called": False, "stored": 0, "reason": "ladd"}
    if calls_today(now) >= daily_cap():
        log.info("gate aeroapi: daily cap %d reached -- not called", daily_cap())
        return {"called": False, "stored": 0, "reason": "daily_cap"}
    call_id = _claim(str(key), purpose, now)
    if call_id is None:
        return {"called": False, "stored": 0, "reason": "leg_slot_used"}
    start = datetime.fromisoformat(key.sched_date).replace(tzinfo=timezone.utc) - timedelta(hours=12)
    end = start + timedelta(hours=48)
    status = None
    try:
        resp = session.get(f"{AEROAPI_BASE}/flights/{key.callsign}",
                           headers={"x-apikey": api_key, "Accept": "application/json"},
                           params={"ident_type": "designator",
                                   "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                   "end": end.strftime("%Y-%m-%dT%H:%M:%SZ")},
                           timeout=TIMEOUT_S)
        status = getattr(resp, "status_code", None)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        _finish(call_id, status, f"error:{type(e).__name__}")
        log.warning("gate aeroapi: call failed (%s)", type(e).__name__)
        return {"called": True, "stored": 0, "reason": "error"}
    f = _pick_flight((data or {}).get("flights") or [], key)
    if f is None:
        _finish(call_id, status, "no_matching_flight")
        return {"called": True, "stored": 0, "reason": "no_matching_flight"}
    obs_kw = dict(key=key, side="arrival", source="aeroapi", observed_at=ga._iso(now),
                  leg_match="route_date", evidence_type="api", confidence=0.8,
                  parser_version="aeroapi-flights-1", airport=key.destination,
                  raw_table="aeroapi_gate_calls", raw_ref=str(call_id), event_key=f"call:{call_id}")
    obs = []
    if "gate" in fields and f.get("gate_destination"):
        obs.append(ga.Observation(field="gate", value_raw=f.get("gate_destination"), **obs_kw))
        if f.get("terminal_destination"):
            obs.append(ga.Observation(field="terminal", value_raw=f.get("terminal_destination"), **obs_kw))
    if "baggage" in fields and key.destination not in ga.DC_AIRPORTS and f.get("baggage_claim"):
        obs.append(ga.Observation(field="baggage", value_raw=f.get("baggage_claim"), **obs_kw))
    r = ga.record(obs) if obs else ga.RecordResult()
    _finish(call_id, status, f"stored:{r.stored}" if obs else "no_values")
    return {"called": True, "stored": r.stored, "reason": "ok"}


def maybe_fetch(key: fi.FlightInstanceKey, *, eta: float | None, phase: str | None,
                phase_at: float | None, now: float | None = None, session=requests) -> dict:
    """The poller's entry point: call only when a field needs it and a slot is due."""
    now = time.time() if now is None else now
    if not _key():
        return {"called": False, "stored": 0, "reason": "no_key"}
    fields = needed_fields(key, now)
    if not fields:
        return {"called": False, "stored": 0, "reason": "higher_source_has_value"}
    purpose = due_purpose(key, eta=eta, phase=phase, phase_at=phase_at, now=now)
    if purpose is None:
        return {"called": False, "stored": 0, "reason": "not_due"}
    return fetch(key, purpose, fields, now=now, session=session)
