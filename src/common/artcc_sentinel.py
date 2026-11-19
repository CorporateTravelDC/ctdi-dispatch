"""common.artcc_sentinel -- per-centre FDPS first-message volume, daily (2026-11-18).

For one UTC day, from flight_events rows CREATED that day (first_seen_at, first_seen_centre and
first_seen_source are set on insert only, pg 0088), per source centre:

  firsts       flights whose first FDPS message reaching this box came from that centre
  firsts_fh    of those, how many arrived as a filed plan (source FH)
  predep       of those, how many arrived before the flight's TFMS OUT (else OFF)

Alert (ops-health, kind artcc_first_drop): a centre whose `firsts` fell more than DROP_PCT % below
its trailing 14-day median (with a median of at least MIN_BASE), or fell to zero.

The hypothesis test (2026-11-18 design note): if our SFDPS subscription is filtered by ARTCC, a
flight's first message only reaches us when ERAM hands it to an in-filter centre, i.e. at or after
departure. The report therefore lists the centres that NEVER deliver a pre-departure first message
over the trailing window, next to those that do. A national (unfiltered) subscription should show
first messages from the departure centre, hours before OUT, for every centre.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import statistics
from collections import defaultdict

from common import flight_instance as fi
from common import platform_sentinels as ps

NAME = "artcc-sentinel"
DROP_PCT = float(os.environ.get("ARTCC_SENTINEL_DROP_PCT", "60"))
MIN_BASE = int(os.environ.get("ARTCC_SENTINEL_MIN_BASE", "20"))
TRAIL_DAYS = 14


def compute(events: list[dict], legs: list[dict]) -> dict:
    """events: {callsign, origin, destination, first_seen_at, centre, source}; legs: TFMS OUT/OFF rows.
    Pure: no database."""
    ref: dict[tuple, list[float]] = defaultdict(list)
    for leg in legs:
        t = fi.to_epoch(leg.get("out")) or fi.to_epoch(leg.get("off"))
        if t is not None:
            ref[((leg.get("callsign") or "").upper(), leg.get("origin"), leg.get("destination"))].append(t)
    per: dict[str, dict] = {}
    for e in events:
        if e.get("first_seen_at") is None:
            continue
        c = e.get("centre") or "unknown"
        row = per.setdefault(c, {"firsts": 0, "firsts_fh": 0, "predep": 0, "matched": 0})
        row["firsts"] += 1
        if (e.get("source") or "").upper() == "FH":
            row["firsts_fh"] += 1
        fs = float(e["first_seen_at"])
        times = [t for t in ref.get((e["callsign"], e.get("origin"), e.get("destination")), ())
                 if fs - 6 * 3600 <= t <= fs + 20 * 3600]
        if times:
            row["matched"] += 1
            if fs < min(times):
                row["predep"] += 1
    return dict(sorted(per.items()))


def drops(today: dict, history: list[dict]) -> list[dict]:
    found = []
    centres = set(today) | {c for h in history[-TRAIL_DAYS:] for c in h}
    for c in sorted(centres):
        if c == "unknown":
            continue
        past = [h[c]["firsts"] for h in history[-TRAIL_DAYS:] if c in h]
        if len(past) < 3:
            continue
        base = statistics.median(past)
        if base < MIN_BASE:
            continue
        n = today.get(c, {}).get("firsts", 0)
        pct = (1 - n / base) * 100.0
        if n == 0 or pct > DROP_PCT:
            found.append({"centre": c, "firsts": n, "baseline": base, "drop_pct": round(pct, 1)})
    return found


def hypothesis(history: list[dict]) -> dict:
    """Centres that never delivered a pre-departure first message over the window, vs those that did."""
    tot: dict[str, dict] = defaultdict(lambda: {"matched": 0, "predep": 0})
    for h in history[-TRAIL_DAYS:]:
        for c, r in h.items():
            tot[c]["matched"] += r.get("matched", 0)
            tot[c]["predep"] += r.get("predep", 0)
    never = sorted(c for c, r in tot.items() if r["matched"] >= MIN_BASE and r["predep"] == 0)
    some = sorted(c for c, r in tot.items() if r["predep"] > 0)
    return {"never_predeparture": never, "some_predeparture": some, "window_days": min(len(history), TRAIL_DAYS),
            "reading": ("consistent with an ARTCC-filtered subscription" if never and some else
                        "no centre is consistently late" if some and not never else "not enough data yet")}


def _fetch(day0: float) -> tuple[list[dict], list[dict]]:
    from common import db
    day1 = day0 + 86400
    with db.conn() as c:
        ev = c.execute("SELECT airline, flight_num, origin, destination, first_seen_at, first_seen_centre, "
                       "first_seen_source FROM flight_events WHERE first_seen_at >= ? AND first_seen_at < ?",
                       (day0, day1)).fetchall()
        lg = c.execute("SELECT callsign, origin, destination, airline_out_time, airline_off_time "
                       "FROM flight_ooooi_times WHERE updated_at >= ? AND updated_at < ?",
                       (day0 - 6 * 3600, day1 + 20 * 3600)).fetchall()
    events = [{"callsign": f"{e['airline'] or ''}{e['flight_num'] or ''}".upper(), "origin": e["origin"],
               "destination": e["destination"], "first_seen_at": e["first_seen_at"],
               "centre": e["first_seen_centre"], "source": e["first_seen_source"]} for e in ev]
    legs = [{"callsign": r["callsign"], "origin": r["origin"], "destination": r["destination"],
             "out": r["airline_out_time"], "off": r["airline_off_time"]} for r in lg]
    return events, legs


def run(day: str | None = None) -> dict | None:
    if not ps.load_ok(NAME):
        return None
    if day:
        d = dt.datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc)
    else:
        d = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)).replace(hour=0, minute=0, second=0,
                                                                             microsecond=0)
    events, legs = _fetch(d.timestamp())
    today = compute(events, legs)
    hist_path = ps.REPORTS_DIR / f"{NAME}-history.json"
    try:
        history = json.loads(hist_path.read_text())
    except (OSError, ValueError):
        history = []
    past = [h["per_centre"] for h in history if h.get("day") != d.strftime("%Y-%m-%d")]
    found = drops(today, past)
    hyp = hypothesis(past + [today])
    report = {"day": d.strftime("%Y-%m-%d"), "generated": ps.fmt(ps.now()), "per_centre": today,
              "drops": found, "hypothesis": hyp}
    history = [h for h in history if h.get("day") != report["day"]] + [{"day": report["day"], "per_centre": today}]
    try:
        ps.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        hist_path.write_text(json.dumps(history[-60:]))
    except OSError as e:
        ps.log.warning("%s: history not saved (%s)", NAME, e)
    md = [f"# Per-centre FDPS first messages, {report['day']} (UTC)", "",
          f"Hypothesis reading: {hyp['reading']} (window {hyp['window_days']} days).",
          f"Never pre-departure: {', '.join(hyp['never_predeparture']) or 'none'}",
          f"Some pre-departure: {', '.join(hyp['some_predeparture']) or 'none'}", "",
          "| centre | firsts | as FH | before OUT | matched to TFMS |", "|---|---:|---:|---:|---:|"]
    for c, r in today.items():
        md.append(f"| {c} | {r['firsts']} | {r['firsts_fh']} | {r['predep']} | {r['matched']} |")
    ps.write_report(NAME, report, "\n".join(md) + "\n")
    for f in found:
        ps.push("artcc_first_drop",
                f"FDPS first messages from {f['centre']}: {f['firsts']} on {report['day']} UTC vs a "
                f"{f['baseline']:.0f}/day trailing median ({f['drop_pct']:.0f} % down). Report: {ps.REPORTS_DIR}/{NAME}.json",
                title=f"FDPS centre drop: {f['centre']}", priority=4, entity=f["centre"],
                detail={"state": "drop", "day": report["day"]})
    return report
