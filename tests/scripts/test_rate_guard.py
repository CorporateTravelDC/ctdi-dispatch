"""tests/scripts/test_rate_guard.py -- 2026-10-12: nightly off-box sync caps (soft 2.5 / hard 3 MB/s)
and the backup dry run that must not dump, write the ledger or read the caller's stdin."""
from __future__ import annotations

import importlib.util
import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("rate_guard", REPO / "scripts/backup/rate_guard.py")
rg = importlib.util.module_from_spec(spec); spec.loader.exec_module(rg)
MB = 1_000_000


def drive(rates_mb, soft=2.5, hard=3.0, window=3):
    """Feed per-interval upload rates; rclone 'obeys' a lowered limit from the next interval."""
    state = {"b": 0, "i": 0, "limit": soft, "alive": True, "set": [], "stopped": False}

    def counter():
        return state["b"]

    def sleeper(_):
        r = rates_mb[min(state["i"], len(rates_mb) - 1)]
        if state["limit"] < soft:
            r = min(r, state["limit"] * 1.1)       # rclone overshoots ~10%
        state["b"] += int(r * MB * 10); state["i"] += 1
        if state["i"] >= len(rates_mb):
            state["alive"] = False

    def setter(mb):
        state["limit"] = mb; state["set"].append(mb); return True

    def stopper():
        state["stopped"] = True; state["alive"] = False

    res = rg.guard(1, "/x.sock", "lo", soft, hard, interval=10, window=window, counter=counter, setter=setter,
                   stopper=stopper, sleeper=sleeper, is_alive=lambda: state["alive"])
    return res, state


def test_within_soft_does_nothing():
    res, st = drive([2.6] * 12)
    assert res["actions"] == [] and not res["stopped"] and 2.5 < res["max_window_mb_s"] < 3


def test_over_hard_lowers_live_then_restores_when_calm():
    res, st = drive([3.4] * 3 + [2.0] * 9)
    acts = [a["action"] for a in res["actions"]]
    assert acts[0] == "lowered" and st["set"][0] == 2.0 and "restored" in acts and not res["stopped"]


def test_persistent_overrun_is_stopped_after_two_reductions():
    # a link that stays over the hard cap whatever rclone does (other traffic) -> lowered twice, then stopped
    res2, _ = drive_unobedient([5.0] * 30)
    assert res2["stopped"] and [a["action"] for a in res2["actions"]] == ["lowered", "lowered", "stopped"]


def drive_unobedient(rates_mb, soft=2.5, hard=3.0):
    state = {"b": 0, "i": 0, "alive": True}

    def sleeper(_):
        state["b"] += int(rates_mb[min(state["i"], len(rates_mb) - 1)] * MB * 10); state["i"] += 1
    stopped = {}
    res = rg.guard(1, "/x.sock", "lo", soft, hard, interval=10, window=3, counter=lambda: state["b"],
                   setter=lambda mb: True, stopper=lambda: stopped.setdefault("x", True), sleeper=sleeper,
                   is_alive=lambda: "x" not in stopped and state["i"] < len(rates_mb))
    return res, state


def test_rate_string_is_rclone_kib():
    assert rg.kib(2.5) == "2441k"     # 2.5 decimal MB/s in rclone's KiB units


def test_dry_run_does_no_work_and_leaves_stdin_alone(tmp_path):
    bindir = tmp_path / "bin"; bindir.mkdir()
    calls = tmp_path / "calls.log"
    for tool in ("podman", "borg", "rclone"):
        (bindir / tool).write_text(f"#!/bin/sh\necho {tool} \"$@\" >> {calls}\n")
        (bindir / tool).chmod(0o755)
    pw = tmp_path / "pass"; pw.write_text("x\n"); pw.chmod(0o600)
    env = dict(os.environ, HOME=str(tmp_path), PATH=f"{bindir}:{os.environ['PATH']}", BACKUP_PASSPHRASE_FILE=str(pw),
               BACKUP_TARGET="remote:dst", BACKUP_LEDGER=str(tmp_path / "ledger.jsonl"))
    r = subprocess.run(["bash", str(REPO / "scripts/backup/ctdi-backup.sh"), "--dry-run"], env=env,
                       input="echo STDIN-WAS-READ\n", capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "dry run: would dump" in r.stdout and "soft 2.5 MB/s, hard 3 MB/s" in r.stdout
    assert not calls.exists()                                  # no podman/borg/rclone call at all
    assert not (tmp_path / "ledger.jsonl").exists()            # no ledger write
    assert not list((tmp_path / ".local/state/ctdc-backup").glob("stage.*"))


def test_a_stalled_sync_is_stopped_and_reported_as_a_stall():
    # 2026-10-14: four uploads sat at 6% with no bytes moving for ~2 h, holding the heavy-job turn
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("rg2", Path(__file__).resolve().parents[2] / "scripts/backup/rate_guard.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    t = [0.0]
    stopped = []
    res = m.guard(1, "sock", "lo", 2.5, 3.0, interval=60, window=3,
                  counter=lambda: 0, setter=lambda mb: True, stopper=lambda: stopped.append(1),
                  sleeper=lambda s: t.__setitem__(0, t[0] + s), is_alive=lambda: not stopped,
                  progress=lambda: 240_000_000, stall_s=1200, clock=lambda: t[0])
    assert stopped and res["stall"] and res["actions"][-1]["reason"] == "stall"


def test_moving_bytes_is_not_a_stall():
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("rg3", Path(__file__).resolve().parents[2] / "scripts/backup/rate_guard.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    t = [0.0]; n = [0]
    def alive():
        n[0] += 1
        return n[0] < 60                                   # 60 min of samples, then rclone exits
    b = [0]
    def prog():
        b[0] += 1_000_000
        return b[0]
    res = m.guard(1, "sock", "lo", 2.5, 3.0, interval=60, window=3, counter=lambda: 0,
                  setter=lambda mb: True, stopper=lambda: None, sleeper=lambda s: t.__setitem__(0, t[0] + s),
                  is_alive=alive, progress=prog, stall_s=1200, clock=lambda: t[0])
    assert not res.get("stall") and not res["stopped"]
