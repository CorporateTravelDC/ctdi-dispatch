"""common.ground_stop_tripwire -- wide ground stops and watched-leg ground stops (2026-11-18).

Reads the stored flow programs (nas_programs: TFMS ground stops and GDPs with their own start and
end times, plus the NAS Status REST rows) every 5 minutes and fires on:

  wide        GS programs in effect at WIDE_N or more distinct airports at once (default 5)
  watched leg a GS or GDP in effect at a watched flight's origin or destination today

Pushes go through the parity gate once per state change (ops kind ground_stop_wide; aviation kind
ground_stop_watched_leg per watch + program), so a program that stays in effect does not re-alert.

`--replay START END` runs the same rule over the stored rows for a past window and reports when it
would have fired, with no push. The 2026-11-12T12:00Z..2026-11-14T12:00Z window (the 11-13 ground
stops, during the agent-side lapse) is the first replay.

Limits, stated: TFMS rows carry start_time/end_time, so their history replays faithfully. NAS Status
REST rows are upserted with only their latest sighting (fetched_at), so in a replay a REST row counts
for REST_SPAN_S after that sighting only; live, a REST row counts while active=1.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass

from common import platform_sentinels as ps

NAME = "ground-stop-tripwire"
WIDE_N = int(os.environ.get("GS_TRIPWIRE_WIDE_N", "5"))
# hysteresis (replay of 11-12..11-14 flapped 4<->5 every few minutes without it): wide turns on at
# WIDE_N airports and off only at WIDE_N - WIDE_HYST or fewer
WIDE_HYST = int(os.environ.get("GS_TRIPWIRE_WIDE_HYST", "2"))
STEP_S = 300
REST_SPAN_S = 30 * 60
WATCH_TYPES = ("GS", "GDP")


@dataclass(frozen=True)
class Program:
    program_id: str
    type: str
    facility: str
    source: str
    start: float | None
    end: float | None
    active: bool
    fetched_at: float

    def in_effect(self, t: float, live: bool) -> bool:
        if self.start is not None:
            end = self.end if self.end is not None else self.start + 6 * 3600
            return self.start <= t <= end
        if live:
            return self.active
        return self.fetched_at <= t <= self.fetched_at + REST_SPAN_S


def _airport(code: str | None) -> str:
    c = (code or "").strip().upper()
    return c[1:] if len(c) == 4 and c.startswith("K") else c


def parse_rows(rows: list[dict]) -> list[Program]:
    out = []
    for r in rows:
        if (r.get("type") or "").upper() not in WATCH_TYPES:
            continue
        try:
            raw = json.loads(r.get("raw_json") or "{}")
        except ValueError:
            raw = {}
        start = raw.get("start_time")
        end = raw.get("end_time")
        out.append(Program(str(r.get("program_id")), r["type"].upper(), _airport(r.get("facility")),
                           r.get("source") or "", float(start) if start else None,
                           float(end) if end else None, bool(r.get("active")), float(r.get("fetched_at") or 0)))
    return out


def evaluate(programs: list[Program], t: float, watches: list[dict], live: bool,
             prior_wide: bool = False) -> dict:
    """State at time t: wide flag (with hysteresis against prior_wide) + facilities, and watched legs."""
    gs = sorted({p.facility for p in programs if p.type == "GS" and p.in_effect(t, live) and p.facility
                 and p.facility != "ALL"})
    wide = len(gs) >= WIDE_N or (prior_wide and len(gs) > max(0, WIDE_N - WIDE_HYST))
    touched = []
    for w in watches:
        ends = {_airport(w.get("origin")): "origin", _airport(w.get("destination")): "destination"}
        for p in programs:
            if p.facility in ends and p.in_effect(t, live):
                touched.append({"watch_id": w.get("id"), "identifier": w.get("identifier"), "program_id": p.program_id,
                                "type": p.type, "facility": p.facility, "end": ends[p.facility]})
    return {"t": t, "wide": wide, "gs_facilities": gs, "watched": touched}


def replay(programs: list[Program], start: float, end: float, watches: list[dict] | None = None) -> list[dict]:
    """Transitions the live rule would have reported between start and end (no pushes)."""
    events, was_wide, seen_watch = [], False, set()
    t = start
    while t <= end:
        st = evaluate(programs, t, watches or [], live=False, prior_wide=was_wide)
        if st["wide"] != was_wide:
            events.append({"at": t, "at_fmt": ps.fmt(t), "event": "wide_on" if st["wide"] else "wide_off",
                           "facilities": st["gs_facilities"]})
            was_wide = st["wide"]
        for w in st["watched"]:
            k = (w["watch_id"], w["program_id"])
            if k not in seen_watch:
                seen_watch.add(k)
                events.append({"at": t, "at_fmt": ps.fmt(t), "event": "watched_leg", **w})
        t += STEP_S
    return events


def _fetch(since: float | None = None) -> list[Program]:
    from common import db
    with db.conn() as c:
        if since is None:
            rows = c.execute("SELECT program_id, type, facility, source, active, fetched_at, raw_json FROM nas_programs "
                             "WHERE type IN ('GS','GDP') AND (active = 1 OR fetched_at >= ?)",
                             (ps.now() - 12 * 3600,)).fetchall()
        else:
            rows = c.execute("SELECT program_id, type, facility, source, active, fetched_at, raw_json FROM nas_programs "
                             "WHERE type IN ('GS','GDP') AND fetched_at >= ?", (since,)).fetchall()
    return parse_rows([dict(r) for r in rows])


def _watches() -> list[dict]:
    from common import db
    try:
        return [w for w in db.get_watchlist_entries("flight") if w.get("origin") or w.get("destination")]
    except Exception as e:   # noqa: BLE001
        ps.log.warning("%s: watchlist unavailable (%s); wide rule only", NAME, type(e).__name__)
        return []


def run() -> dict:
    programs = _fetch()
    now = ps.now()
    try:
        prior_wide = bool(json.loads((ps.REPORTS_DIR / f"{NAME}.json").read_text()).get("wide"))
    except (OSError, ValueError):
        prior_wide = False
    st = evaluate(programs, now, _watches(), live=True, prior_wide=prior_wide)
    state = "wide" if st["wide"] else "clear"
    if st["wide"] or prior_wide:      # "cleared" only after a wide state; never on a quiet first run
        ps.push("ground_stop_wide",
                (f"Ground stops in effect at {len(st['gs_facilities'])} airports at once: "
                 f"{', '.join(st['gs_facilities'][:20])} (as of {ps.fmt(now)})." if st["wide"] else
                 f"Wide ground stop cleared ({len(st['gs_facilities'])} airports under a GS as of {ps.fmt(now)})."),
                title="Ground stops: wide" if st["wide"] else "Ground stops: cleared", priority=4,
                entity="nas", detail={"state": state})
    for w in st["watched"]:
        ps.push("ground_stop_watched_leg",
                f"{w['type']} in effect at {w['facility']} ({w['end']} of watched {w['identifier']}), "
                f"program {w['program_id']}, as of {ps.fmt(now)}.",
                title=f"{w['type']} at {w['facility']}: {w['identifier']}", priority=4,
                vertical="aviation", topic="flight-alerts", entity=str(w["watch_id"]),
                detail={"program_id": w["program_id"], "state": "in_effect"})
    report = {"generated": ps.fmt(now), "wide_n": WIDE_N, **st}
    ps.write_report(NAME, report)
    return report


def run_replay(start_iso: str, end_iso: str) -> dict:
    from common import flight_instance as fi
    start, end = fi.to_epoch(start_iso), fi.to_epoch(end_iso)
    if start is None or end is None or end <= start:
        raise ValueError("replay needs START < END, ISO-8601 UTC")
    programs = _fetch(since=start - 24 * 3600)
    events = replay(programs, start, end)
    report = {"replay": [ps.fmt(start), ps.fmt(end)], "wide_n": WIDE_N, "events": events,
              "limits": "TFMS rows replay from their own start/end; REST rows only near their last sighting"}
    ps.write_report(f"{NAME}-replay", report)
    return report
