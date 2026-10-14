#!/usr/bin/env python3
"""scripts/backup/rate_guard.py -- soft/hard upload caps for the nightly off-box sync (2026-10-12).

Operator: "2.5 MB/s soft with a 3 MB/s hard cap" (the 2026-10-12 uplink ramp measured
2.2-3.2 MB/s deliverable with the SWIM feeds healthy throughout).

  soft  rclone's own --bwlimit (it overshoots by ~10%; the ramp saw 1.11 at 1, 2.31 at 2).
  hard  this guard: it samples the interface's TOTAL upload every --interval seconds and
        averages --window samples. Above the hard cap it first LOWERS rclone's limit live
        through rclone's rc unix socket (to 80%, then 50% of soft); only if the link is still
        over the hard cap after that does it stop rclone (exit 3). When upload drops below
        90% of soft for a full window after a reduction, it restores soft.

  rate_guard.py --pid PID --rc-sock PATH --iface wld0 --soft-mb 2.5 --hard-mb 3
Prints one JSON summary line at the end (max window average, actions); exit 0 normally,
3 when it had to stop rclone for bandwidth, 4 when it stopped a STALLED rclone.

Stall (2026-10-14): the 10-14 03:47Z sync hung for ~2 h with four uploads at 6% and no bytes
moving (rclone's own idle timeout never fired on the Proton backend), holding the heavy-job
turn all night. If rclone's own transferred-bytes counter (rc core/stats, not the interface,
which carries other traffic) has not grown for --stall-min minutes, the guard asks rclone to
quit and reports stall=true; ctdi-backup.sh then retries the sync once. Never needs a secret.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


def tx_bytes(iface: str) -> int:
    return int((Path("/sys/class/net") / iface / "statistics" / "tx_bytes").read_text())


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def kib(mb_s: float) -> str:
    """Decimal MB/s -> rclone rate string (rclone's 'k' is KiB)."""
    return f"{int(mb_s * 1_000_000 / 1024)}k"


def set_rate(sock: str, mb_s: float, rclone: str = "rclone") -> bool:
    r = subprocess.run([rclone, "rc", "--unix-socket", sock, "core/bwlimit", f"rate={kib(mb_s)}"],
                       capture_output=True, text=True, timeout=20)
    return r.returncode == 0


def rclone_bytes(sock: str, rclone: str = "rclone") -> int | None:
    try:
        r = subprocess.run([rclone, "rc", "--unix-socket", sock, "core/stats"], capture_output=True, text=True, timeout=20)
        return int(json.loads(r.stdout).get("bytes", 0)) if r.returncode == 0 else None
    except Exception:
        return None


def guard(pid: int, sock: str, iface: str, soft: float, hard: float, interval: float = 10, window: int = 3,
          counter=None, setter=None, stopper=None, sleeper=time.sleep, is_alive=None,
          progress=None, stall_s: float = 1200, clock=time.monotonic) -> dict:
    counter = counter or (lambda: tx_bytes(iface))
    setter = setter or (lambda mb: set_rate(sock, mb))
    def _stop():
        # ask rclone to quit cleanly over its own socket first; SIGTERM the wrapper as a backstop
        subprocess.run(["rclone", "rc", "--unix-socket", sock, "core/quit"], capture_output=True, timeout=20)
        time.sleep(5)
        if alive(pid):
            os.kill(pid, signal.SIGTERM)
    stopper = stopper or _stop
    is_alive = is_alive or (lambda: alive(pid))
    progress = progress or (lambda: rclone_bytes(sock))
    last_bytes, last_move = None, clock()
    steps = [soft, round(soft * 0.8, 3), round(soft * 0.5, 3)]
    level, rates, actions, max_avg, calm = 0, [], [], 0.0, 0
    last = counter()
    while is_alive():
        sleeper(interval)
        now = counter()
        rates.append((now - last) / interval / 1_000_000)
        last = now
        b = progress()
        if b is not None:
            if last_bytes is None or b > last_bytes:
                last_bytes, last_move = b, clock()
            elif clock() - last_move >= stall_s:
                stopper()
                actions.append({"action": "stopped", "reason": "stall", "bytes": b,
                                "stalled_min": round((clock() - last_move) / 60, 1)})
                return {"max_window_mb_s": round(max_avg, 3), "actions": actions, "stopped": True, "stall": True}
        if len(rates) < window:
            continue
        avg = sum(rates[-window:]) / window
        max_avg = max(max_avg, avg)
        if avg > hard:
            calm = 0
            if level + 1 < len(steps):
                level += 1
                ok = setter(steps[level])
                actions.append({"action": "lowered", "to_mb_s": steps[level], "window_avg_mb_s": round(avg, 3), "rc_ok": ok})
                rates.clear()
            else:
                stopper()
                actions.append({"action": "stopped", "window_avg_mb_s": round(avg, 3)})
                return {"max_window_mb_s": round(max_avg, 3), "actions": actions, "stopped": True}
        elif level and avg < 0.9 * soft:
            calm += 1
            if calm >= window:
                level = 0
                ok = setter(soft)
                actions.append({"action": "restored", "to_mb_s": soft, "window_avg_mb_s": round(avg, 3), "rc_ok": ok})
                calm = 0
        else:
            calm = 0
    return {"max_window_mb_s": round(max_avg, 3), "actions": actions, "stopped": False}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pid", type=int, required=True)
    ap.add_argument("--rc-sock", required=True)
    ap.add_argument("--iface", default="wld0")
    ap.add_argument("--soft-mb", type=float, default=2.5)
    ap.add_argument("--hard-mb", type=float, default=3.0)
    ap.add_argument("--interval", type=float, default=10)
    ap.add_argument("--window", type=int, default=3)
    ap.add_argument("--stall-min", type=float, default=20)
    a = ap.parse_args(argv)
    res = guard(a.pid, a.rc_sock, a.iface, a.soft_mb, a.hard_mb, a.interval, a.window, stall_s=a.stall_min * 60)
    print(json.dumps({"soft_mb_s": a.soft_mb, "hard_mb_s": a.hard_mb, **res}, sort_keys=True), flush=True)
    return 4 if res.get("stall") else (3 if res["stopped"] else 0)


if __name__ == "__main__":
    sys.exit(main())
