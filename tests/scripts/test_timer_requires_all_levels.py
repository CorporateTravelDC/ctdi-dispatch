"""
2026-10-06 (operator: "a standing guard that all timers regardless of level
are captured"). scripts/check-timer-requires.sh scanned only user timers; the
two root timers (watchdog, tailscale-cert-renew) carried the self-referential
Requires= that makes a timer's service run at every boot, tracked and live.
"""
from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
GUARD = REPO / "scripts" / "check-timer-requires.sh"


class TimerGuardAllLevels(unittest.TestCase):
    def test_guard_scans_every_level(self):
        code = GUARD.read_text()
        self.assertIn("git ls-files '*.timer'", code)
        self.assertIn("/etc/systemd/system", code)
        self.assertIn('"${HOME}/.config/systemd/user"', code)

    def test_no_tracked_timer_requires_its_own_service(self):
        timers = subprocess.run(["git", "ls-files", "*.timer"], cwd=REPO, capture_output=True,
                                text=True, check=True).stdout.split()
        timers = [t for t in timers if "/retired" not in t]
        self.assertGreater(len(timers), 70)
        for t in timers:
            text = (REPO / t).read_text()
            svc = Path(t).name[:-len(".timer")] + ".service"
            unit = re.search(r"^\[Unit\]\n(.*?)(?=^\[|\Z)", text, re.M | re.S)
            body = unit.group(1) if unit else ""
            with self.subTest(timer=t):
                self.assertNotRegex(body, rf"(?m)^(Requires|Wants)={re.escape(svc)}$")
