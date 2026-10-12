#!/usr/bin/env python3
"""scripts/lib/load_stamp.py -- host-load stamps for test runs (2026-10-12).

Operator: a failure blamed on load must carry a load VALUE, so a recurring
pattern becomes its own data-derived proof (move a job, add a guardrail,
let a run clear) instead of a shrug.

One JSON line per event, appended to a private ledger outside the repo:
  $CTDC_LOAD_STAMP_FILE, default ~/.local/state/ctdc-tests/load-stamps.jsonl

Recorded: UTC time, event (session-start / session-end / test-failed /
test-timeout / test-slow / manual), test id + duration when relevant, load
average 1/5/15 min, CPU count, load per CPU, PSI pressure (cpu / io /
memory avg10 + avg60) and the top CPU consumers by command NAME only (never
arguments: they can carry tokens). Never raises -- a stamp that fails must
never fail the run it describes. CTDC_LOAD_STAMP=0 disables it.

CLI:
  load_stamp.py stamp [--event NAME] [--note TEXT]   write one manual stamp
  load_stamp.py summary [--days N]                    failures/timeouts vs load
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

SLOW_TEST_S = 60.0


def ledger_path() -> Path:
    return Path(os.environ.get("CTDC_LOAD_STAMP_FILE")
                or Path.home() / ".local/state/ctdc-tests/load-stamps.jsonl")


def _pressure(kind: str) -> dict | None:
    try:
        line = Path(f"/proc/pressure/{kind}").read_text().splitlines()[0]   # the "some" line
        kv = dict(p.split("=") for p in line.split()[1:])
        return {"avg10": float(kv["avg10"]), "avg60": float(kv["avg60"])}
    except (OSError, KeyError, ValueError, IndexError):
        return None


def _top(n: int = 3) -> list[dict]:
    try:
        out = subprocess.run(["ps", "-eo", "comm=,pcpu=", "--sort=-pcpu"], capture_output=True,
                             text=True, timeout=5).stdout.splitlines()
    except (OSError, subprocess.SubprocessError):
        return []
    top = []
    for line in out[:n]:
        parts = line.rsplit(None, 1)
        if len(parts) == 2:
            try:
                top.append({"comm": parts[0].strip()[:32], "pcpu": float(parts[1])})
            except ValueError:
                pass
    return top


def snapshot() -> dict:
    try:
        l1, l5, l15 = os.getloadavg()
    except OSError:
        l1 = l5 = l15 = -1.0
    ncpu = os.cpu_count() or 1
    return {"load1": round(l1, 2), "load5": round(l5, 2), "load15": round(l15, 2), "ncpu": ncpu,
            "load_per_cpu": round(l1 / ncpu, 2),
            "psi": {k: _pressure(k) for k in ("cpu", "io", "memory")}, "top": _top()}


def stamp(event: str, path: Path | None = None, **fields) -> dict | None:
    if os.environ.get("CTDC_LOAD_STAMP") == "0":
        return None
    try:
        rec = {"ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "event": event,
               **fields, **snapshot()}
        p = Path(path) if path else ledger_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a") as f:
            f.write(json.dumps(rec, sort_keys=True) + "\n")
        return rec
    except Exception:
        return None


def read(days: float | None = None) -> list[dict]:
    p = ledger_path()
    if not p.exists():
        return []
    cut = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ") if days else ""
    rows = []
    for line in p.read_text().splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("ts", "") >= cut:
            rows.append(r)
    return rows


def summary(rows: list[dict]) -> str:
    """Per test: failures/timeouts and the load they happened at, against the
    load at session starts (the baseline). Plus who was on top of the CPU."""
    base = [r["load_per_cpu"] for r in rows if r.get("event") == "session-start"]
    bad = [r for r in rows if r.get("event") in ("test-failed", "test-timeout", "test-slow")]
    lines = [f"{len(rows)} stamps; {len(base)} sessions; baseline load/cpu at session start: "
             + (f"median {sorted(base)[len(base) // 2]:.2f}" if base else "n/a")]
    by: dict[str, list[dict]] = {}
    for r in bad:
        by.setdefault(r.get("test") or "?", []).append(r)
    for test, rs in sorted(by.items(), key=lambda kv: -len(kv[1])):
        lpc = sorted(r["load_per_cpu"] for r in rs)
        kinds = {}
        for r in rs:
            kinds[r["event"]] = kinds.get(r["event"], 0) + 1
        lines.append(f"  {test}: " + ", ".join(f"{k.split('-')[1]} x{v}" for k, v in sorted(kinds.items()))
                     + f"; load/cpu median {lpc[len(lpc) // 2]:.2f} (max {lpc[-1]:.2f})")
    tops: dict[str, int] = {}
    for r in bad:
        for t in r.get("top") or []:
            tops[t["comm"]] = tops.get(t["comm"], 0) + 1
    if tops:
        lines.append("  on top of the CPU at those moments: "
                     + ", ".join(f"{k} x{v}" for k, v in sorted(tops.items(), key=lambda kv: -kv[1])[:6]))
    if not bad:
        lines.append("  no failed, timed-out or slow tests recorded")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="load_stamp.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("stamp"); s.add_argument("--event", default="manual"); s.add_argument("--note", default="")
    m = sub.add_parser("summary"); m.add_argument("--days", type=float, default=None)
    a = ap.parse_args(argv)
    if a.cmd == "stamp":
        rec = stamp(a.event, **({"note": a.note} if a.note else {}))
        print(f"load stamp: {a.event} load1={rec['load1']} ({rec['load_per_cpu']}/cpu)" if rec else "load stamp: off")
    else:
        print(summary(read(a.days)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
