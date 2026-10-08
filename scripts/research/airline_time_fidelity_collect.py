#!/usr/bin/env python3
"""scripts/research/airline_time_fidelity_collect.py -- research collector
(2026-10-08, docs/research/AIRLINE_TIME_FIDELITY_SPEC.md).

READ-ONLY. Cross-references, per flight, the airline-reported OOOI times that
TFMS carries (flight_ooooi_times) against what FAA systems observed:
  * FDPS (ERAM) departure/arrival runway times (flight_events.raw_json),
  * ASDE-X surface events (surface_movement_events: spotout, off, on, spotin),
  * TDES clearance delivery and parking gate (tdes_departure_events),
  * TBFM meter-fix ETAs (tbfm_sequences),
  * our own watch outcome (watchlist_entries OOOI lock), when watched.
Prints one JSON line per flight, then one "airline_daily" line per airline
(nationwide OFF-fidelity aggregate). Never writes to the database, never
alerts. Runs inside the web container (it has the database connection):
    podman exec -i systemd-corporatetraveldc-web python3 - < this file
The host wrapper (airline-time-fidelity-collect.sh) appends the output to a
dated JSONL file. Surface data is kept only ~4,095 rows per airport, so the
collector snapshots every 6 hours with an 8-hour lookback; duplicates are
expected and removed at analysis time by (gufi, collected_at latest).
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

DC_AIRPORTS = ("KDCA", "KIAD", "KBWI")
# Operator benchmark flights: PRIVATE (watched flights are client travel patterns).
# The wrapper passes them from docs/research/private/bellwethers.txt (dropped from
# the public mirror) as ATF_BELLWETHERS="CS1,CS2". The public edition ships none.
BELLWETHERS = tuple(x.strip().upper() for x in os.environ.get("ATF_BELLWETHERS", "").split(",") if x.strip())
LOOKBACK_H = 8
WATCH_DAYS = 14                               # watched flights/aircraft and their routes
FIXED_ROUTES = ("KBOS>KDCA", "KDCA>KBOS", "KLGA>KDCA", "KDCA>KLGA")   # shuttle pairs, all carriers
_CALLSIGN = re.compile(r"^[A-Z]{3}[0-9]{1,4}[A-Z]?$")
_TAIL = re.compile(r"^(?:N[1-9][0-9A-Z]{0,4}|[A-Z]-[A-Z]{4}|[A-Z]{2}-[A-Z]{3}|C-[FGI][A-Z]{3})$")
SURFACE_EVENTS = ("spotout", "runwayin", "runwayout", "off", "on", "spotin")

_RWY = re.compile(r"<(departure|arrival)\b[^>]*>.*?<runwayTime>\s*<(actual|estimated)\s+time=\"([^\"]+)\"",
                  re.S)


def ts(v):
    """ISO string or epoch -> aware datetime, else None."""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return datetime.fromtimestamp(float(v), tz=timezone.utc)
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None


def minutes(a, b):
    """a - b in minutes (1 decimal), None if either is missing."""
    a, b = ts(a), ts(b)
    return None if a is None or b is None else round((a - b).total_seconds() / 60.0, 1)


def fdps_runway_times(raw: str | None) -> dict:
    """{'departure_actual'|'departure_estimated'|'arrival_actual'|...: iso} from a FIXM NasFlight."""
    out = {}
    for side, kind, t in _RWY.findall(raw or ""):
        out.setdefault(f"{side}_{kind}", t)
    return out


def nearest(events: list[dict], kind: str, around, before_h=4, after_h=4):
    """The surface event of `kind` nearest to `around` within the window."""
    c = ts(around)
    if c is None:
        return None
    best = None
    for e in events:
        if e["event"] != kind:
            continue
        t = ts(e["event_time"])
        if t is None or not (c - timedelta(hours=before_h) <= t <= c + timedelta(hours=after_h)):
            continue
        if best is None or abs((t - c).total_seconds()) < abs((ts(best["event_time"]) - c).total_seconds()):
            best = e
    return best


def derive(f: dict, fdps: dict, sfc_org: list, sfc_dst: list) -> dict:
    """All comparisons for one flight. Sign convention: reported - observed."""
    off_obs = nearest(sfc_org, "off", f["airline_off_time"] or f["original_departure"])
    out_obs = nearest(sfc_org, "spotout", f["airline_out_time"] or f["original_departure"])
    on_obs = nearest(sfc_dst, "on", f["airline_on_time"] or f["original_arrival"])
    in_obs = nearest(sfc_dst, "spotin", f["airline_in_time"] or f["original_arrival"])
    return {
        "frozen_off": bool(f["airline_off_time"]) and f["airline_off_time"] == f["original_departure"],
        "frozen_on": bool(f["airline_on_time"]) and f["airline_on_time"] == f["original_arrival"],
        "reported_taxi_out_min": minutes(f["airline_off_time"], f["airline_out_time"]),
        "reported_taxi_in_min": minutes(f["airline_in_time"], f["airline_on_time"]),
        "off_vs_filed_min": minutes(f["airline_off_time"], f["original_departure"]),
        "on_vs_filed_min": minutes(f["airline_on_time"], f["original_arrival"]),
        "off_vs_fdps_min": minutes(f["airline_off_time"], fdps.get("departure_actual")),
        "on_vs_fdps_min": minutes(f["airline_on_time"], fdps.get("arrival_actual")),
        "off_vs_surface_min": minutes(f["airline_off_time"], off_obs and off_obs["event_time"]),
        "on_vs_surface_min": minutes(f["airline_on_time"], on_obs and on_obs["event_time"]),
        "out_vs_ramp_exit_min": minutes(f["airline_out_time"], out_obs and out_obs["event_time"]),
        "in_vs_ramp_entry_min": minutes(f["airline_in_time"], in_obs and in_obs["event_time"]),
        "observed_ramp_exit_to_off_min": minutes(off_obs and off_obs["event_time"], out_obs and out_obs["event_time"]),
        "observed_on_to_ramp_entry_min": minutes(in_obs and in_obs["event_time"], on_obs and on_obs["event_time"]),
        "surface_coverage": {"origin": bool(sfc_org), "destination": bool(sfc_dst)},
    }


def watched_scope(c, now: float) -> tuple[set, set]:
    """2026-10-08 (operator): every flight watched in the last WATCH_DAYS days, by
    callsign or by aircraft (tail), and every route those flight numbers fly
    (all carriers on it). Returns (callsigns, 'ORIG>DEST' routes)."""
    since_iso = datetime.fromtimestamp(now - WATCH_DAYS * 86400, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    idents = {r["identifier"].strip().upper() for r in c.execute(
        "SELECT DISTINCT identifier FROM watchlist_history WHERE entry_type = 'flight' AND fired_at >= ?",
        (since_iso,)).fetchall() if r["identifier"]}
    callsigns = {i for i in idents if _CALLSIGN.match(i)} | set(BELLWETHERS)
    tails = [i for i in idents if not _CALLSIGN.match(i) and _TAIL.match(i)]
    if tails:
        for r in c.execute(
                f"SELECT DISTINCT airline || flight_num AS cs FROM flight_events WHERE upper(registration) IN "
                f"({','.join('?' * len(tails))}) AND updated_at >= ?", (*tails, now - WATCH_DAYS * 86400)).fetchall():
            if r["cs"] and _CALLSIGN.match(r["cs"]):
                callsigns.add(r["cs"])
    routes = set(FIXED_ROUTES)
    if callsigns:
        for r in c.execute(
                f"SELECT DISTINCT origin, destination FROM flight_ooooi_times WHERE callsign IN "
                f"({','.join('?' * len(callsigns))}) AND updated_at >= ?",
                (*sorted(callsigns), now - WATCH_DAYS * 86400)).fetchall():
            if r["origin"] and r["destination"]:
                routes.add(f"{r['origin']}>{r['destination']}")
    return callsigns, routes


def brands(c, airline: str, flight_num: str) -> list[str]:
    """Marketing carriers for this operating flight (codeshare_map). PRIVATE ledger
    only: the public aggregate never carries the marketing/operating breakdown."""
    return sorted({r["marketing_carrier"] for r in c.execute(
        "SELECT DISTINCT marketing_carrier FROM codeshare_map WHERE operating_carrier = ? AND operating_flight_num = ?",
        (airline, flight_num)).fetchall() if r["marketing_carrier"] and r["marketing_carrier"] != airline})


def collect(c, now: float) -> list[dict]:
    since = now - LOOKBACK_H * 3600
    ph = ",".join("?" * len(DC_AIRPORTS))
    w_calls, w_routes = watched_scope(c, now)
    calls = sorted(w_calls) or [""]               # an empty IN () is a syntax error
    flights = [dict(r) for r in c.execute(
        f"SELECT * FROM flight_ooooi_times WHERE updated_at >= ? AND "
        f"(origin IN ({ph}) OR destination IN ({ph}) OR callsign IN ({','.join('?' * len(calls))}) "
        f"OR (origin || '>' || destination) IN ({','.join('?' * len(w_routes))}))",
        (since, *DC_AIRPORTS, *DC_AIRPORTS, *calls, *sorted(w_routes))).fetchall()]
    out = []
    collected_at = datetime.fromtimestamp(now, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for f in flights:
        cs = f["callsign"]
        ev = [dict(r) for r in c.execute(
            "SELECT airport, event, event_time FROM surface_movement_events WHERE callsign = ? AND airport IN (?, ?)",
            (cs, f["origin"], f["destination"])).fetchall()]
        sfc_org = [e for e in ev if e["airport"] == f["origin"]]
        sfc_dst = [e for e in ev if e["airport"] == f["destination"]]
        fe = c.execute(
            "SELECT raw_json, updated_at FROM flight_events WHERE airline = ? AND flight_num = ? AND origin = ? "
            "AND destination = ? AND updated_at BETWEEN ? AND ? ORDER BY updated_at DESC LIMIT 1",
            (f["airline"], f["flight_num"], f["origin"], f["destination"], f["updated_at"] - 86400,
             f["updated_at"] + 86400)).fetchone()
        fdps = fdps_runway_times(fe["raw_json"] if fe else None)
        tdes = c.execute(
            "SELECT clearance_delivery_time, parking_gate, event_time FROM tdes_departure_events "
            "WHERE callsign = ? AND airport = ? ORDER BY event_time DESC LIMIT 1", (cs, f["origin"])).fetchone()
        tbfm = [dict(r) for r in c.execute(
            "SELECT meter_fix, facility, eta, eta_kind, last_seen FROM tbfm_sequences WHERE flight_id = ? "
            "ORDER BY last_seen DESC LIMIT 6", (cs,)).fetchall()]
        wl = c.execute(
            "SELECT id, oooi_phase, oooi_source, oooi_lock_source, oooi_lock_at, oooi_authority_note "
            "FROM watchlist_entries WHERE identifier = ? ORDER BY added_at DESC LIMIT 1", (cs,)).fetchone()
        out.append({
            "kind": "flight", "collected_at": collected_at, "gufi": f["gufi"], "callsign": cs,
            "airline": f["airline"], "flight_num": f["flight_num"], "origin": f["origin"],
            "destination": f["destination"], "status": f["flight_status"], "bellwether": cs in BELLWETHERS,
            "scope": sorted(filter(None, [
                "dc" if (f["origin"] in DC_AIRPORTS or f["destination"] in DC_AIRPORTS) else None,
                "watched" if cs in w_calls else None,
                "watched-route" if f"{f['origin']}>{f['destination']}" in w_routes else None])),
            "brands": brands(c, f["airline"], f["flight_num"]),
            "reported": {k: f[k] for k in ("airline_out_time", "airline_off_time", "airline_on_time",
                                           "airline_in_time", "original_departure", "original_arrival")},
            "fdps": fdps,
            "surface": sorted(({"airport": e["airport"], "event": e["event"], "t": e["event_time"]} for e in ev
                               if e["event"] in SURFACE_EVENTS), key=lambda e: e["t"]),
            "tdes": dict(tdes) if tdes else None,
            "tbfm": tbfm,
            "watch": dict(wl) if wl else None,
            "derived": derive(f, fdps, sfc_org, sfc_dst),
        })
    return out


def airline_daily(c, now: float) -> list[dict]:
    """Nationwide per-airline OFF fidelity over the lookback window."""
    since = now - LOOKBACK_H * 3600
    rows = c.execute("""
        WITH s AS (SELECT callsign, airport, event_time::timestamptz t FROM surface_movement_events WHERE event = 'off'),
        f AS (SELECT airline, callsign, origin, airline_off_time::timestamptz off_t,
                     (airline_off_time = original_departure) frozen
              FROM flight_ooooi_times WHERE updated_at >= ? AND airline_off_time IS NOT NULL
                AND original_departure IS NOT NULL),
        j AS (SELECT f.airline, f.frozen, extract(epoch FROM (f.off_t - s.t))/60.0 d FROM f LEFT JOIN s
                ON s.callsign = f.callsign AND s.airport = f.origin
               AND s.t BETWEEN f.off_t - interval '3 hours' AND f.off_t + interval '3 hours')
        SELECT airline, count(*) AS n, sum(frozen::int) AS frozen_n, count(d) AS observed_n,
               sum((abs(d) <= 1.5)::int) AS within_1_5_n,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY d) AS median_off_minus_observed
        FROM j GROUP BY airline HAVING count(*) >= 20""", (since,)).fetchall()
    at = datetime.fromtimestamp(now, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return [{"kind": "airline_window", "collected_at": at, "window_h": LOOKBACK_H, **{k: (round(float(v), 2)
             if isinstance(v, float) or (v is not None and k.startswith("median")) else v) for k, v in dict(r).items()}}
            for r in rows]


def main() -> int:
    sys.path.insert(0, "/app/src")
    from common import db
    now = time.time()
    with db.conn() as c:
        lines = collect(c, now) + airline_daily(c, now)
    for ln in lines:
        print(json.dumps(ln, sort_keys=True, default=str))
    print(json.dumps({"kind": "run", "collected_at": datetime.fromtimestamp(now, tz=timezone.utc)
                      .strftime("%Y-%m-%dT%H:%M:%SZ"), "flights": sum(1 for x in lines if x["kind"] == "flight")}),
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
