"""common.plan_coverage -- fleet-wide pre-departure plan coverage, daily (2026-11-18).

Why: on 2026-11-18 UA4006 (ASH4006 KSAV-KIAD) was airborne before its FDPS plan was visible here,
and a fleet-wide check then showed 80-94 % of legs on EVERY carrier have no stored pre-departure
route version -- a standing gap, not a regression. fdps_route_versions only holds route-bearing
messages, so this report uses flight_events.first_seen_at (pg 0088, set on insert only): the moment
any FDPS message for that flight occurrence first reached this box.

For one UTC day: every TFMS leg with an OFF that day (flight_ooooi_times) is matched to the
same-occurrence flight_events row (same callsign, origin and destination, first seen within the
20 h before OFF). Per operator (the callsign's 3-letter prefix; the regional partner table and the
mainline carriers are grouped too):

  legs        TFMS legs with OFF that day
  measured    of those, legs whose matched row has first_seen_at (rows from before 0088 are unknown)
  visible     plan (any FDPS message) visible before OUT (else OFF)
  share       visible / measured
  median lead minutes before OUT/OFF, of the visible ones

Alert (ops-health, kind plan_coverage_step): an operator with at least MIN_LEGS measured legs whose
share moved by STEP_PP percentage points or more against its trailing 14-day median. A rise is
reported too (a national-subscription cut-over should show here as the proof).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import statistics
from collections import defaultdict

from common import flight_instance as fi
from common import platform_sentinels as ps
from common.fdps_predeparture import REGIONAL_PARTNERS

NAME = "plan-coverage"
WINDOW_S = 20 * 3600
MIN_LEGS = int(os.environ.get("PLAN_COVERAGE_MIN_LEGS", "50"))
STEP_PP = float(os.environ.get("PLAN_COVERAGE_STEP_PP", "20"))
TRAIL_DAYS = 14
MAINLINE = ("UAL", "AAL", "DAL", "SWA", "JBU", "ASA", "NKS", "FFT")


def group_of(op: str) -> str:
    if op in MAINLINE:
        return "mainline"
    for mk, ops in REGIONAL_PARTNERS.items():
        if op in ops:
            return "regional"
    return "other"


def compute(legs: list[dict], events: list[dict]) -> dict:
    """legs: {callsign, origin, destination, out, off, original_departure} (times any to_epoch form).
    events: {callsign, origin, destination, first_seen_at}. Pure: no database."""
    idx: dict[tuple, list[float]] = defaultdict(list)
    for e in events:
        if e.get("first_seen_at") is None:
            continue
        idx[(e["callsign"], e.get("origin"), e.get("destination"))].append(float(e["first_seen_at"]))
    unknown_idx = {(e["callsign"], e.get("origin"), e.get("destination"))
                   for e in events if e.get("first_seen_at") is None}
    per: dict[str, dict] = {}
    for leg in legs:
        cs = (leg.get("callsign") or "").upper()
        off = fi.to_epoch(leg.get("off"))
        if not cs or off is None:
            continue
        ref = fi.to_epoch(leg.get("out")) or off
        op = cs[:3]
        row = per.setdefault(op, {"legs": 0, "measured": 0, "visible": 0, "leads": []})
        row["legs"] += 1
        key = (cs, leg.get("origin"), leg.get("destination"))
        seen = [t for t in idx.get(key, ()) if off - WINDOW_S <= t <= off + 6 * 3600]
        if not seen:
            if key in unknown_idx:
                continue            # a row exists but predates first_seen_at: unknown, not counted
            row["measured"] += 1    # no row at all for this occurrence before landing-ish: not visible
            continue
        first = min(seen)
        row["measured"] += 1
        if first <= ref:
            row["visible"] += 1
            row["leads"].append((ref - first) / 60.0)
    out = {}
    for op, r in sorted(per.items()):
        share = (r["visible"] / r["measured"]) if r["measured"] else None
        out[op] = {"group": group_of(op), "legs": r["legs"], "measured": r["measured"], "visible": r["visible"],
                   "share": round(share, 4) if share is not None else None,
                   "median_lead_min": round(statistics.median(r["leads"]), 1) if r["leads"] else None}
    return out


def steps(today: dict, history: list[dict]) -> list[dict]:
    """Operators whose share moved >= STEP_PP points vs the trailing median of `history`
    (a list of earlier compute() outputs, newest last)."""
    found = []
    for op, r in today.items():
        if r["share"] is None or r["measured"] < MIN_LEGS:
            continue
        past = [h[op]["share"] for h in history[-TRAIL_DAYS:]
                if op in h and h[op].get("share") is not None and h[op].get("measured", 0) >= MIN_LEGS]
        if len(past) < 3:
            continue
        base = statistics.median(past)
        delta_pp = (r["share"] - base) * 100.0
        if abs(delta_pp) >= STEP_PP:
            found.append({"operator": op, "share": r["share"], "baseline": round(base, 4),
                          "delta_pp": round(delta_pp, 1), "direction": "drop" if delta_pp < 0 else "rise"})
    return found


def _fetch(day0: float) -> tuple[list[dict], list[dict]]:
    from common import db
    day1 = day0 + 86400
    with db.conn() as c:
        rows = c.execute(
            "SELECT callsign, origin, destination, airline_out_time, airline_off_time, original_departure "
            "FROM flight_ooooi_times WHERE updated_at >= ? AND updated_at < ?",
            (day0 - 3600, day1 + 12 * 3600)).fetchall()
        legs = []
        for r in rows:
            off = fi.to_epoch(r["airline_off_time"])
            if off is not None and day0 <= off < day1:
                legs.append({"callsign": r["callsign"], "origin": r["origin"], "destination": r["destination"],
                             "out": r["airline_out_time"], "off": r["airline_off_time"],
                             "original_departure": r["original_departure"]})
        ev = c.execute(
            "SELECT airline, flight_num, origin, destination, first_seen_at FROM flight_events "
            "WHERE (first_seen_at IS NULL AND updated_at >= ?) OR (first_seen_at >= ? AND first_seen_at < ?)",
            (day0 - WINDOW_S, day0 - WINDOW_S, day1 + 6 * 3600)).fetchall()
    events = [{"callsign": f"{e['airline'] or ''}{e['flight_num'] or ''}".upper(), "origin": e["origin"],
               "destination": e["destination"], "first_seen_at": e["first_seen_at"]} for e in ev]
    return legs, events


def run(day: str | None = None) -> dict | None:
    if not ps.load_ok(NAME):
        return None
    if day:
        d = dt.datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc)
    else:
        d = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)).replace(hour=0, minute=0, second=0,
                                                                             microsecond=0)
    day0 = d.timestamp()
    legs, events = _fetch(day0)
    today = compute(legs, events)
    hist_path = ps.REPORTS_DIR / f"{NAME}-history.json"
    try:
        history = json.loads(hist_path.read_text())
    except (OSError, ValueError):
        history = []
    found = steps(today, [h["per_operator"] for h in history])
    report = {"day": d.strftime("%Y-%m-%d"), "generated": ps.fmt(ps.now()), "legs": len(legs),
              "per_operator": today, "steps": found,
              "note": "first_seen_at exists only for rows inserted after pg 0088; older rows count as unknown"}
    history = [h for h in history if h.get("day") != report["day"]] + [{"day": report["day"], "per_operator": today}]
    try:
        ps.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        hist_path.write_text(json.dumps(history[-60:]))
    except OSError as e:
        ps.log.warning("%s: history not saved (%s)", NAME, e)
    md = [f"# Pre-departure plan coverage, {report['day']} (UTC)", "",
          "| operator | group | legs | measured | visible before OUT | share | median lead (min) |",
          "|---|---|---:|---:|---:|---:|---:|"]
    for op, r in sorted(today.items(), key=lambda kv: -kv[1]["legs"])[:40]:
        share = "" if r["share"] is None else f"{r['share'] * 100:.1f} %"
        lead = "" if r["median_lead_min"] is None else str(r["median_lead_min"])
        md.append(f"| {op} | {r['group']} | {r['legs']} | {r['measured']} | {r['visible']} | {share} | {lead} |")
    ps.write_report(NAME, report, "\n".join(md) + "\n")
    for s in found:
        ps.push("plan_coverage_step",
                f"Pre-departure plan coverage for {s['operator']} {s['direction']}: {s['share']*100:.1f} % "
                f"vs {s['baseline']*100:.1f} % trailing median ({s['delta_pp']:+.1f} points), day {report['day']} UTC. "
                f"Report: {ps.REPORTS_DIR}/{NAME}.json",
                title=f"Plan coverage {s['direction']}: {s['operator']}", priority=4, entity=s["operator"],
                detail={"state": s["direction"], "day": report["day"]})
    return report
