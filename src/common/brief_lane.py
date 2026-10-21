#!/usr/bin/env python3
"""common/brief_lane.py -- run an OPERATIONAL brief in the long lane, or promote
it to the hot lane on long-runner contention (operator 2026-10-20).

Why: ops-brief (:05) queued behind ep-advance (:35, ~1 h runs) on the box-wide
long-runner unit lock, waited out `flock -w 600` and skipped every hour from
14:15Z to 19:15Z on 2026-10-20. Operational briefs now:

  1. try the long-runner unit lock for LONG_FIRST_S (as before, just shorter);
  2. on contention, ALSO try a single-occupant hot-lane unit lock beside it --
     whichever frees first wins, the long lane preferred while both are free;
  3. holding the hot-lane lock, exec the brief with CTDC_LLM_LANE=hot, so
     common/llm.py posts straight to llama's second slot (no long_runner_lock,
     no pre-flight waits);
  4. if neither frees within BUDGET_S, exit 75 (EX_TEMPFAIL = clean skip,
     SuccessExitStatus=75 in the unit) exactly as `flock -E 75` did.

Standing rule kept: hot may take the second slot beside a running report,
never a third core, never a kill. This module never signals, kills or starts
anything; llama-server's -np 2 / -t 2 are untouched. One promoted brief at a
time (the hot-lane lock).

Every decision prints ONE line ("brief-lane: unit=... lane=long|hot|none
wait=Ns ...") so scripts/brief-fallback-monitor.sh can count promotions and
skips. Standard library only: it runs before the brief's own imports, inside
the image, after scripts/verified-exec.sh has checked src/.

Usage (quadlet Exec=):
  scripts/verified-exec.sh python3 src/common/brief_lane.py --long-first 60 --budget 600 -- src/poller/skills/ops_brief.py
"""
from __future__ import annotations

import argparse
import fcntl
import os
import sys
import time

LOCK_DIR = "/var/lib/corporatetraveldc/llama-pool"
LONG_LOCK = "long-runner-unit.lock"     # shared with every long-lane quadlet's flock(1)
HOT_LOCK = "hot-lane-unit.lock"          # new: single occupant, operational briefs only
LONG_FIRST_S = 60
BUDGET_S = 600                           # the old flock -w 600
POLL_S = 2.0
EX_TEMPFAIL = 75


def _try(fd: int) -> bool:
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except BlockingIOError:
        return False


def _open(path: str) -> int:
    return os.open(path, os.O_RDWR | os.O_CREAT, 0o644)


def acquire(lock_dir: str = LOCK_DIR, long_first_s: float = LONG_FIRST_S,
            budget_s: float = BUDGET_S, poll_s: float = POLL_S,
            now=time.monotonic, sleep=time.sleep) -> tuple[str | None, int | None, float]:
    """(lane, held fd, seconds waited). lane None = both locks busy for budget_s."""
    long_fd = _open(os.path.join(lock_dir, LONG_LOCK))
    hot_fd = None
    t0 = now()
    while True:
        waited = now() - t0
        if _try(long_fd):
            if hot_fd is not None:
                os.close(hot_fd)
            return "long", long_fd, waited
        if waited >= long_first_s:
            if hot_fd is None:
                hot_fd = _open(os.path.join(lock_dir, HOT_LOCK))
            if _try(hot_fd):
                os.close(long_fd)
                return "hot", hot_fd, waited
        if waited >= budget_s:
            os.close(long_fd)
            if hot_fd is not None:
                os.close(hot_fd)
            return None, None, waited
        sleep(poll_s)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="long lane, or hot lane on contention")
    ap.add_argument("--long-first", type=float, default=LONG_FIRST_S)
    ap.add_argument("--budget", type=float, default=BUDGET_S)
    ap.add_argument("--lock-dir", default=LOCK_DIR)
    ap.add_argument("script", nargs=argparse.REMAINDER)
    a = ap.parse_args(argv)
    script = [s for s in a.script if s != "--"] if a.script[:1] == ["--"] else a.script
    if not script:
        ap.error("no brief script given")
    unit = os.environ.get("CORPORATETRAVELDC_UNIT", "?")
    lane, fd, waited = acquire(a.lock_dir, a.long_first, a.budget)
    if lane is None:
        print(f"brief-lane: unit={unit} lane=none wait={waited:.0f}s -- long-runner and hot-lane "
              f"locks both busy; skipped (exit {EX_TEMPFAIL})", flush=True)
        return EX_TEMPFAIL
    os.set_inheritable(fd, True)   # the lock lives as long as the brief process holds this fd
    env = dict(os.environ)
    if lane == "hot":
        env["CTDC_LLM_LANE"] = "hot"
        print(f"brief-lane: unit={unit} lane=hot wait={waited:.0f}s -- promoted (long-runner lock "
              f"busy past {a.long_first:.0f}s)", flush=True)
    else:
        env.pop("CTDC_LLM_LANE", None)
        print(f"brief-lane: unit={unit} lane=long wait={waited:.0f}s", flush=True)
    os.execvpe(sys.executable, [sys.executable, *script], env)
    return 1  # not reached


if __name__ == "__main__":
    sys.exit(main())
