#!/usr/bin/env python3
"""scripts/lib/tripwire_draw.py -- draw the NEXT tripwire run time.

Operator directive 2026-10-04: "within 7 days, minimum 36 hrs since last, and
at least once monthly use a new candidate window ... randomized without a set
clock an agent or human adversary could clock like an armored car route".

Rules (all local time America/New_York):
  * next_run in [last_run + 36h, last_run + 7d]  (first run: [now, now + 7d])
  * prefer a quiet-window candidate from
    /var/lib/corporatetraveldc/reports/quiet-windows.json (the rolling 30-day
    load profile): a concrete datetime inside the range whose weekday/hour is
    one of the ranked quiet windows, with a random offset inside the window
  * once per calendar month the pick MUST be a window label never used before
    (state keeps every label ever used); other picks are uniform over all
    candidates in range
  * no candidates in range (or no report yet) -> uniform random instant in
    the range
Nothing here is periodic: the hourly timer only asks "is it time yet?".

Usage:
  tripwire_draw.py draw   <state.json>      # writes next_run into state, prints it
  tripwire_draw.py due    <state.json>      # exit 0 if now >= next_run; a MISSING next_run
                                            # draws one now and is NOT due (exit 1)
  tripwire_draw.py mark   <state.json>      # record a run now (sets last_run, used label)
  tripwire_draw.py mark-and-draw <state.json>  # mark + draw, saved ONCE (preferred)
  tripwire_draw.py near-ceiling <state.json>   # exit 0 if now >= last_run + 7d - 1h
  tripwire_draw.py status <state.json>      # human-readable

2026-10-04 duel L1: `mark` used to pop next_run and the shell then called
`draw` separately -- if draw raised, the state had no next_run and `due`
returned 0 every hour, firing a full stack refresh hourly. Now mark never
removes next_run (draw overwrites it), mark-and-draw saves once, a failed
draw falls back to a uniform instant in [last+36h, last+7d], and a missing
next_run is "draw now", never "run now".
"""
from __future__ import annotations

import json
import os
import random
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

LOCAL = ZoneInfo("America/New_York")
QUIET_JSON = "/var/lib/corporatetraveldc/reports/quiet-windows.json"
MIN_GAP_S = 36 * 3600
MAX_GAP_S = 7 * 86400
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _load(path):
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _save(path, st):
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(st, fh, indent=1)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _candidates():
    q = _load(QUIET_JSON)
    wins = list(q.get("quietest_overall", []))
    for day_list in (q.get("quietest_per_day") or {}).values():
        wins.extend(day_list)
    seen, out = set(), []
    for w in wins:
        key = (w["weekday"], int(w["start_hour"]))
        if key in seen:
            continue
        seen.add(key)
        out.append({"weekday": w["weekday"], "start_hour": int(w["start_hour"]), "width_h": int(w.get("width_h", 2))})
    return out


def _concrete(cands, lo, hi):
    """Every concrete (datetime, label) for the candidate windows inside [lo, hi]."""
    out = []
    d = datetime.fromtimestamp(lo, LOCAL).replace(hour=0, minute=0, second=0, microsecond=0)
    end = datetime.fromtimestamp(hi, LOCAL)
    while d <= end:
        for c in cands:
            if DAYS[d.weekday()] != c["weekday"]:
                continue
            start = d.replace(hour=c["start_hour"])
            offset = random.randint(0, max(0, c["width_h"] * 3600 - 600))
            t = start + timedelta(seconds=offset)
            if lo <= t.timestamp() <= hi:
                out.append((int(t.timestamp()), f"{c['weekday']}-{c['start_hour']:02d}"))
        d += timedelta(days=1)
    return out


def _seed():
    """Secondary randomisation seed (operator directive 2026-10-04): mix the
    quiet-window profile's content hash into the RNG with os.urandom, so the
    draw depends on measured load history as well as entropy -- an observer
    would need both the box's entropy AND its 30-day load profile to
    reproduce a draw, and the profile changes every day."""
    import hashlib
    h = hashlib.sha256(os.urandom(32))
    try:
        with open(QUIET_JSON, "rb") as fh:
            h.update(fh.read())
    except OSError:
        pass
    h.update(str(time.time_ns()).encode())
    random.seed(h.digest())


def draw(st):
    try:
        return _draw(st)
    except Exception as e:  # never leave the state without a next_run
        now = int(time.time())
        last = int(st.get("last_run", 0))
        lo = max(now + 60, (last + MIN_GAP_S) if last else now + 60)
        hi = max(lo + 60, (last + MAX_GAP_S) if last else now + MAX_GAP_S)
        st.update({"next_run": random.randint(lo, hi), "next_label": "uniform",
                   "next_reason": f"draw failed ({type(e).__name__}) -> uniform fallback",
                   "drawn_at": now, "range": [lo, hi]})
        return st


def near_ceiling(st, now=None):
    """True when the 7-day ceiling is less than an hour away (or passed):
    the load deferral must not push the run past it (duel M3)."""
    now = int(time.time()) if now is None else now
    last = int(st.get("last_run", 0))
    return bool(last) and now >= last + MAX_GAP_S - 3600


def _draw(st):
    _seed()
    now = int(time.time())
    last = int(st.get("last_run", 0))
    lo, hi = (last + MIN_GAP_S, last + MAX_GAP_S) if last else (now + 60, now + MAX_GAP_S)
    if hi <= now:          # ceiling already passed (box was off?) -> ASAP, still random within the hour
        lo, hi = now + 60, now + 3600
    lo = max(lo, now + 60)
    used_all = set(st.get("used_labels", []))
    month = datetime.now(LOCAL).strftime("%Y-%m")
    new_this_month = st.get("new_window_month") == month
    concrete = _concrete(_candidates(), lo, hi)
    pool = concrete
    reason = "quiet-window candidate"
    if not new_this_month:
        fresh = [c for c in concrete if c[1] not in used_all]
        if fresh:
            pool, reason = fresh, "NEW window this month (never used before)"
    if pool:
        t, label = random.choice(pool)
    else:
        t, label, reason = random.randint(lo, hi), "uniform", "no candidate in range -> uniform random"
    st.update({"next_run": t, "next_label": label, "next_reason": reason, "drawn_at": now,
               "range": [lo, hi]})
    return st


def mark(st):
    now = int(time.time())
    label = st.get("next_label", "uniform")
    month = datetime.now(LOCAL).strftime("%Y-%m")
    used = list(st.get("used_labels", []))
    if label != "uniform" and label not in used:
        used.append(label)
        if st.get("new_window_month") != month:
            st["new_window_month"] = month
    hist = list(st.get("history", []))[-60:]
    hist.append({"ran_at": now, "label": label})
    st.update({"last_run": now, "used_labels": used, "history": hist})
    # next_run is NOT removed here: draw() overwrites it (duel L1)
    return st


def fmt(ts):
    return datetime.fromtimestamp(ts, LOCAL).strftime("%a %Y-%m-%d %H:%M %Z") if ts else "never"


def main(argv):
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 64
    cmd, path = argv[0], argv[1]
    st = _load(path)
    if cmd == "draw":
        st = draw(st)
        _save(path, st)
        print(f"next_run={st['next_run']} ({fmt(st['next_run'])}) label={st['next_label']} reason={st['next_reason']}")
        return 0
    if cmd == "due":
        nr = st.get("next_run")
        if nr is None:            # missing schedule = draw one now, not "run now"
            st = draw(st)
            _save(path, st)
            return 1
        return 0 if time.time() >= nr else 1
    if cmd == "near-ceiling":
        return 0 if near_ceiling(st) else 1
    if cmd == "mark":
        st = mark(st)
        _save(path, st)
        print(f"marked run at {fmt(st['last_run'])}; used labels: {len(st['used_labels'])}")
        return 0
    if cmd == "mark-and-draw":
        st = draw(mark(st))
        _save(path, st)
        print(f"marked run at {fmt(st['last_run'])}; next_run={st['next_run']} ({fmt(st['next_run'])}) label={st['next_label']} reason={st['next_reason']}")
        return 0
    if cmd == "status":
        print(f"last_run : {fmt(st.get('last_run', 0))}")
        print(f"next_run : {fmt(st.get('next_run'))}  label={st.get('next_label')}  ({st.get('next_reason')})")
        print(f"range    : {fmt((st.get('range') or [0, 0])[0])} -> {fmt((st.get('range') or [0, 0])[1])}")
        print(f"new-window month satisfied: {st.get('new_window_month')}  used labels: {st.get('used_labels', [])}")
        return 0
    print(f"unknown command {cmd}", file=sys.stderr)
    return 64


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
