"""
tests/scripts/test_long_runner_lock_contract.py

2026-10-04: every Quadlet that takes the box-wide long-runner lock
(/var/lib/corporatetraveldc/llama-pool/long-runner-unit.lock) must bound
its queue wait. The 2026-10-02 second-brain-daily hang (97b9075) was a
bare `flock` with no -w: four ~1h siblings held the lock back-to-back,
the unit queued for 43 min, and systemd killed the flock at
TimeoutStartSec -- the queue wait had eaten the run budget. ops-brief
died the same way the same night. This test is parameterised over the
live set of lock-taking quadlets so a future unit cannot ship with a
bare flock again.

Contract (per unit):
  * Exec= uses `flock ... -w <N>` (bounded wait)
  * either `-E 75` + `SuccessExitStatus` containing 75 (97b9075 form), or
    an explicit sh -c wrapper that maps flock's give-up exit to a clean
    skip (transport-pattern-digest's documented form)
  * TimeoutStartSec >= wait + run budget, where the run budget is the
    pre-existing figure recorded in the unit's own comment; we assert the
    weaker, mechanical part: TimeoutStartSec > wait.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
QUADLET_DIR = REPO / ".config/containers/systemd"
LOCK = "/var/lib/corporatetraveldc/llama-pool/long-runner-unit.lock"


def _lock_taking_quadlets() -> list[Path]:
    return sorted(p for p in QUADLET_DIR.glob("*.container") if LOCK in p.read_text())


def _field(text: str, key: str) -> str | None:
    m = re.search(rf"^{re.escape(key)}=(.*)$", text, re.M)
    return m.group(1).strip() if m else None


class LongRunnerLockContract(unittest.TestCase):
    def test_at_least_the_known_set_is_present(self):
        names = {p.stem for p in _lock_taking_quadlets()}
        # If this shrinks, a unit silently stopped taking the lock -- check why.
        self.assertGreaterEqual(len(names), 17, names)
        self.assertIn("corporatetraveldc-second-brain-daily", names)
        self.assertIn("corporatetraveldc-ops-brief", names)

    def test_every_lock_taking_quadlet_bounds_its_wait(self):
        for p in _lock_taking_quadlets():
            with self.subTest(unit=p.stem):
                text = p.read_text()
                exec_line = _field(text, "Exec")
                self.assertIsNotNone(exec_line, "no Exec=")
                m = re.search(r"flock\b[^;']*?-w\s+(\d+)", exec_line)
                self.assertIsNotNone(m, f"bare flock (no -w) in Exec=: {exec_line[:120]}")
                wait = int(m.group(1))
                self.assertGreater(wait, 0)

                timeout = _field(text, "TimeoutStartSec")
                self.assertIsNotNone(timeout, "no TimeoutStartSec=")
                self.assertGreater(int(timeout), wait,
                                   "TimeoutStartSec must exceed the lock wait, else the queue "
                                   "wait alone can trip the start timeout (the 2026-10-02 hang)")

                if "-E 75" in exec_line:
                    ses = _field(text, "SuccessExitStatus") or ""
                    self.assertIn("75", ses.split(),
                                  "-E 75 without SuccessExitStatus=75: a lock give-up would "
                                  "be a unit failure (page) instead of a clean skip")
                else:
                    # The only other accepted form: an explicit wrapper that
                    # maps flock's exit 1 (gave up) to exit 0 with a log line.
                    self.assertTrue(exec_line.startswith("/bin/sh -c") and "rc=$?" in exec_line,
                                    "lock give-up must be mapped to a clean skip "
                                    "(-E 75 + SuccessExitStatus=75, or the sh -c rc wrapper)")


if __name__ == "__main__":
    unittest.main()


class TransportPatternDigestBudget(unittest.TestCase):
    """2026-10-06: killed at 28m14s -- TimeoutStartSec=1600 never grew when
    `flock -w 1200` was added, so lock wait + its measured run (mining up to
    ~1000s + 420s gates + 600s digest call) could not fit."""

    def test_timeout_covers_wait_plus_measured_run(self):
        text = (QUADLET_DIR / "corporatetraveldc-transport-pattern-digest.container").read_text()
        wait = int(re.search(r"flock\b[^;']*?-w\s+(\d+)", _field(text, "Exec")).group(1))
        self.assertGreaterEqual(int(_field(text, "TimeoutStartSec")), wait + 1000 + 420 + 600)
