"""2026-10-23 (operator): scripts/heavy-lock-status.sh says, without taking the heavy lock, whether it is
held and whether a heavy timer (backup, stack-refresh tripwire, weekly external update, rollout) fires
soon -- 2026-10-22 the tripwire took the turn right after a "backup done" ping and the relay refused."""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/heavy-lock-status.sh"
LIB = ROOT / "scripts/lib/heavy_lock.sh"


def run(tmp_path, timers, *args):
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    (tmp_path / "timers.json").write_text(json.dumps(timers))
    fake = bindir / "systemctl"
    fake.write_text(f"#!/bin/bash\ncat {tmp_path / 'timers.json'}\n")
    fake.chmod(0o755)
    env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}", HEAVY_LOCK_DIR=str(tmp_path / "lk"))
    env.pop("CTDC_HEAVY_LOCK_HELD", None)
    return subprocess.run(["bash", str(SCRIPT), *args], env=env, capture_output=True, text=True, timeout=60)


def t(unit, in_s):
    return {"unit": unit, "next": int((time.time() + in_s) * 1e6), "activates": unit.replace(".timer", ".service")}


def test_clear_when_free_and_nothing_due(tmp_path):
    r = run(tmp_path, [t("corporatetraveldc-backup.timer", 5 * 3600),
                       t("corporatetraveldc-backup-freshness.timer", 60)])   # light timer: ignored
    assert r.returncode == 0, r.stdout + r.stderr
    assert "heavy lock: free" in r.stdout and "clear" in r.stdout


def test_busy_when_heavy_timer_due_within_window(tmp_path):
    timers = [t("corporatetraveldc-stack-refresh-tripwire.timer", 10 * 60)]
    r = run(tmp_path, timers)
    assert r.returncode == 1 and "stack-refresh-tripwire" in r.stdout and "BUSY" in r.stdout
    r = run(tmp_path, timers, "5")          # outside a 5-minute window
    assert r.returncode == 0, r.stdout


def test_busy_when_lock_held_and_never_takes_it(tmp_path):
    env = dict(os.environ, HEAVY_LOCK_DIR=str(tmp_path / "lk"))
    env.pop("CTDC_HEAVY_LOCK_HELD", None)
    holder = subprocess.Popen(["bash", "-c", f"source {LIB}; heavy_lock_acquire 'nightly backup' 5 >/dev/null; sleep 4"],
                              env=env)
    try:
        for _ in range(50):
            if (tmp_path / "lk" / "holder").exists():
                break
            time.sleep(0.1)
        r = run(tmp_path, [])
        assert r.returncode == 1, r.stdout + r.stderr
        assert "HELD by nightly backup" in r.stdout
    finally:
        holder.wait()
    # the status check left the holder file as it was (it never takes the lock)
    assert (tmp_path / "lk" / "holder").read_text().startswith("nightly backup")
    r = run(tmp_path, [])
    assert r.returncode == 0 and "heavy lock: free" in r.stdout
