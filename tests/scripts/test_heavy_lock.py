"""tests/scripts/test_heavy_lock.py -- 2026-10-12: backup, restore test, rollout and stack refresh take
turns (one heavy job at a time); a waiter names the holder; a child of the holder does not deadlock."""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

LIB = Path(__file__).resolve().parents[2] / "scripts/lib/heavy_lock.sh"


def sh(script, env, **kw):
    return subprocess.run(["bash", "-c", f"set -euo pipefail; source {LIB}; {script}"], env=env,
                          capture_output=True, text=True, **kw)


def env(tmp_path):
    e = dict(os.environ, HEAVY_LOCK_DIR=str(tmp_path / "lk")); e.pop("CTDC_HEAVY_LOCK_HELD", None)
    return e


def test_second_job_waits_names_the_holder_and_times_out(tmp_path):
    e = env(tmp_path)
    holder = subprocess.Popen(["bash", "-c", f"source {LIB}; heavy_lock_acquire 'nightly backup' 5; sleep 6"], env=e)
    time.sleep(1)
    t0 = time.time()
    r = sh("heavy_lock_acquire 'full-load rollout' 1 || echo TIMEDOUT", e)
    assert "TIMEDOUT" in r.stdout and "nightly backup" in r.stderr + r.stdout
    holder.wait()
    r = sh("heavy_lock_acquire 'full-load rollout' 5 && echo GOT", e)
    assert "GOT" in r.stdout
    assert (tmp_path / "lk" / "holder").read_text().startswith("full-load rollout")


def test_waiter_gets_the_turn_when_the_holder_finishes(tmp_path):
    e = env(tmp_path)
    holder = subprocess.Popen(["bash", "-c", f"source {LIB}; heavy_lock_acquire 'restore test' 5; sleep 2"], env=e)
    time.sleep(0.5)
    r = sh("heavy_lock_acquire 'nightly backup' 30 && echo GOT", e, timeout=60)
    holder.wait()
    assert "GOT" in r.stdout and "got the turn after waiting" in r.stdout


def test_child_of_the_holder_does_not_deadlock(tmp_path):
    e = env(tmp_path)
    r = sh(f"heavy_lock_acquire 'stack refresh' 5 && bash -c 'source {LIB}; heavy_lock_acquire \"full-load rollout\" 2 && echo CHILD-OK'", e, timeout=30)
    assert "CHILD-OK" in r.stdout and "inside stack refresh's turn" in r.stdout
