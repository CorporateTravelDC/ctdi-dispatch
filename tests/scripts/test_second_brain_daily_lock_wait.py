"""
tests/scripts/test_second_brain_daily_lock_wait.py

Guards the 2026-10-03 fix for the second-brain-daily 43-minute hang: the
unit's flock on the box-wide long-runner lock was unbounded, so a queue
of other long-runners (five of them that night, ~1h each at load 19)
ate the whole TimeoutStartSec before python ever started, and systemd
SIGKILLed a process with 8s of CPU. The unit now bounds the wait
(flock -w), maps give-up to EX_TEMPFAIL (-E 75), declares 75 a clean
skip (SuccessExitStatus=75), and sizes TimeoutStartSec as wait + run.

Two checks: the tracked Quadlet keeps that contract, and flock on this
box really does return 75 promptly when the lock is held by someone else.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
QUADLET = REPO / ".config/containers/systemd/corporatetraveldc-second-brain-daily.container"
RUN_BUDGET_S = 2600  # the Phase-4 measured run budget the unit documents


def _quadlet_fields() -> dict:
    text = QUADLET.read_text()
    exec_line = next(l for l in text.splitlines() if l.startswith("Exec="))
    m_w = re.search(r"\bflock\b.*?\s-w\s+(\d+)", exec_line)
    m_e = re.search(r"\s-E\s+(\d+)", exec_line)
    m_t = re.search(r"^TimeoutStartSec=(\d+)", text, re.M)
    m_s = re.search(r"^SuccessExitStatus=(.+)$", text, re.M)
    return {
        "exec": exec_line,
        "wait": int(m_w.group(1)) if m_w else None,
        "exit": int(m_e.group(1)) if m_e else None,
        "timeout": int(m_t.group(1)) if m_t else None,
        "success": m_s.group(1).split() if m_s else [],
    }


class QuadletContract(unittest.TestCase):
    def test_lock_wait_is_bounded_and_mapped_to_tempfail(self):
        f = _quadlet_fields()
        self.assertIn("--verbose", f["exec"], "flock --verbose logs the real queue wait")
        self.assertIsNotNone(f["wait"], "flock must carry -w <seconds>: unbounded wait was the hang")
        self.assertEqual(f["exit"], 75, "lock give-up must exit EX_TEMPFAIL (75)")
        self.assertIn("75", f["success"], "75 must be a clean skip, not a failure/page")

    def test_start_budget_covers_queue_wait_plus_run(self):
        f = _quadlet_fields()
        self.assertGreaterEqual(f["timeout"], f["wait"] + RUN_BUDGET_S,
                                "TimeoutStartSec must cover lock wait + run budget")
        self.assertLess(f["timeout"], 120 * 60,
                        "must stay under the timer's 120min OnUnitActiveSec so firings never overlap")


@unittest.skipUnless(shutil.which("flock"), "util-linux flock not on PATH")
class FlockGivesUp(unittest.TestCase):
    def test_contended_lock_returns_75_within_bound(self):
        with tempfile.TemporaryDirectory() as d:
            lock = os.path.join(d, "long-runner-unit.lock")
            holder = subprocess.Popen(["flock", lock, "sleep", "30"])
            try:
                time.sleep(0.5)  # let the holder acquire
                t0 = time.monotonic()
                r = subprocess.run(["flock", "--verbose", "-w", "1", "-E", "75", lock, "true"],
                                   capture_output=True, text=True)
                elapsed = time.monotonic() - t0
            finally:
                holder.kill(); holder.wait()
            self.assertEqual(r.returncode, 75, r.stderr)
            self.assertLess(elapsed, 5.0, "gave up far later than -w 1")
            # util-linux 2.41: the give-up reason goes to stderr, the timing
            # line to stdout -- both land in the journal under podman.
            out = (r.stdout or "") + (r.stderr or "")
            self.assertIn("timeout", out.lower(), "flock --verbose should say why it gave up")

    def test_free_lock_runs_and_reports_wait(self):
        with tempfile.TemporaryDirectory() as d:
            lock = os.path.join(d, "l")
            r = subprocess.run(["flock", "--verbose", "-w", "1", "-E", "75", lock, "true"],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("getting lock took", (r.stdout or "") + (r.stderr or ""))


if __name__ == "__main__":
    unittest.main()
