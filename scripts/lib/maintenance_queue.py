#!/usr/bin/env python3
"""scripts/lib/maintenance_queue.py -- state machine for maintenance-dispatch.sh.

2026-10-04 (duel M2/X3). The dispatcher used to `systemctl --user start` a
oneshot (blocking for the whole run) and record it as "started" whatever
happened: a failed job was never recorded and was retried first forever,
starving the queue; an ExecCondition skip or a lock give-up (exit 75) was
recorded as if it had run. Now:

  * the dispatcher starts with --no-block and records {status: dispatched,
    at: <epoch>, attempts: n};
  * on every later pass it RECONCILES dispatched jobs from the journal lines
    since `at` (systemd unit properties are useless here: a finished oneshot is
    garbage-collected and its Result/Condition fields reset):
        "Finished <unit>"                          -> done
        exit status 75 / "failed to get lock"      -> skipped (lock give-up)
        "Skipped due to" / "Condition check ... skipped" -> skipped (window closed)
        "Failed with result" / "Failed to start"   -> failed
        otherwise                                  -> still dispatched (running)
  * next job = first never-tried unit in queue order; then failed or skipped
    units with attempts < MAX_ATTEMPTS (one retry later in the day); a failed
    job never blocks the rest of the queue.

State file (JSON): {"day": "YYYY-MM-DD", "jobs": {unit: {...}}, "unrun_notified": bool}
"""
from __future__ import annotations

import json
import re
import sys
import time

MAX_ATTEMPTS = 2
TERMINAL = {"done", "failed-final"}

_SKIP_RX = re.compile(r"Skipped due to|Condition check resulted in .* being skipped|status=75\b|failed to get lock", re.I)
_FAIL_RX = re.compile(r"Failed with result|Failed to start", re.I)
_DONE_RX = re.compile(r"\bFinished\b")


def classify(lines: list[str]) -> str:
    """Outcome of ONE dispatch from the unit's journal lines since dispatch."""
    text = "\n".join(lines)
    if _SKIP_RX.search(text):
        return "skipped"
    if _FAIL_RX.search(text):
        return "failed"
    if _DONE_RX.search(text):
        return "done"
    return "dispatched"


def load(path: str, day: str) -> dict:
    try:
        st = json.load(open(path))
    except Exception:
        st = {}
    if st.get("day") != day:
        st = {"day": day, "jobs": {}, "unrun_notified": False}
    # migrate the pre-2026-10-04 shape {"started": [...]}
    for u in st.pop("started", []) or []:
        st.setdefault("jobs", {}).setdefault(u, {"status": "done", "at": 0, "attempts": 1, "note": "migrated"})
    st.setdefault("jobs", {})
    return st


def save(path: str, st: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(st, fh, indent=1)
    import os
    os.replace(tmp, path)


def reconcile(st: dict, unit: str, lines: list[str], active: bool) -> str:
    j = st["jobs"].get(unit)
    if not j or j.get("status") != "dispatched":
        return j.get("status", "pending") if j else "pending"
    # still running (or no journal evidence yet) -> leave it dispatched
    outcome = "dispatched" if active else classify(lines)
    if outcome in ("failed", "skipped") and j.get("attempts", 1) >= MAX_ATTEMPTS:
        outcome = "failed-final" if outcome == "failed" else "skipped-final"
    j["status"] = outcome
    j["settled_at"] = int(time.time()) if outcome != "dispatched" else None
    return outcome


def next_job(st: dict, queue: list[str]) -> str | None:
    jobs = st["jobs"]
    for u in queue:                                  # never tried, in priority order
        if u not in jobs:
            return u
    for u in queue:                                  # one retry for failed / skipped
        j = jobs[u]
        if j.get("status") in ("failed", "skipped") and j.get("attempts", 1) < MAX_ATTEMPTS:
            return u
    return None


def mark_dispatched(st: dict, unit: str) -> None:
    j = st["jobs"].setdefault(unit, {"attempts": 0})
    j.update({"status": "dispatched", "at": int(time.time()), "attempts": j.get("attempts", 0) + 1})


def position(st: dict, queue: list[str], unit: str) -> str:
    settled = sum(1 for u in queue if st["jobs"].get(u, {}).get("status") in ("done", "failed-final", "skipped-final"))
    return f"{settled + 1}/{len(queue)} (attempt {st['jobs'].get(unit, {}).get('attempts', 0)})"


def unrun(st: dict, queue: list[str]) -> list[str]:
    return [u for u in queue if st["jobs"].get(u, {}).get("status") not in ("done",)]


# --------------------------------------------------------------------- CLI --
# maintenance_queue.py <state> <day> <cmd> [args]   (all output on stdout)
#   reconcile <unit> <active:0|1> <journal-lines-file>  -> prints status
#   next <queue-file>                                  -> prints unit or nothing
#   dispatched <unit> <queue-file>                     -> records, prints position
#   dispatched-at <unit>                               -> prints epoch of last dispatch
#   status <queue-file>                                -> "unit status attempts" lines
#   unrun <queue-file>                                 -> space-separated units
#   flag <name>                                        -> sets st[name]=True
#   get-flag <name>                                    -> prints 1/0
def _queue(path: str) -> list[str]:
    return [l.strip() for l in open(path) if l.strip() and not l.lstrip().startswith("#")]


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__, file=sys.stderr)
        return 64
    path, day, cmd, *rest = argv
    st = load(path, day)
    if cmd == "reconcile":
        unit, active, lines_file = rest
        lines = open(lines_file).read().splitlines() if lines_file != "-" else []
        print(reconcile(st, unit, lines, active == "1")); save(path, st); return 0
    if cmd == "next":
        n = next_job(st, _queue(rest[0])); print(n or ""); return 0
    if cmd == "dispatched":
        unit, qf = rest
        mark_dispatched(st, unit); save(path, st); print(position(st, _queue(qf), unit)); return 0
    if cmd == "dispatched-at":
        print(st["jobs"].get(rest[0], {}).get("at", 0)); return 0
    if cmd == "status":
        for u in _queue(rest[0]):
            j = st["jobs"].get(u, {})
            print(f"{u} {j.get('status', 'pending')} {j.get('attempts', 0)}")
        return 0
    if cmd == "unrun":
        print(" ".join(unrun(st, _queue(rest[0])))); return 0
    if cmd == "flag":
        st[rest[0]] = True; save(path, st); return 0
    if cmd == "get-flag":
        print(1 if st.get(rest[0]) else 0); return 0
    print(f"unknown command {cmd}", file=sys.stderr)
    return 64


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
