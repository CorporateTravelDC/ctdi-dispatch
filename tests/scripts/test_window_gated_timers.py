"""A unit gated by maintenance-window-guard.sh must be scheduled inside the window.

2026-10-21: the weekly second-brain dump fired at 02:00 UTC (22:00 ET), outside the 23:00-05:00 ET
window, so its ExecCondition skipped every run for weeks with Result=exec-condition and no alert.
Each gated service's timer must name America/New_York and fire inside the window.
"""
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
UNITS = REPO / ".config" / "systemd" / "user"
WINDOW_START_H, WINDOW_END_H = 23, 5  # CTDC_MAINTENANCE_WINDOW_START / _END


def _gated_services():
    for svc in sorted(UNITS.glob("*.service")):
        if re.search(r"^ExecCondition=.*maintenance-window-guard\.sh", svc.read_text(), re.M):
            yield svc


def _in_window(hour):
    return hour >= WINDOW_START_H or hour < WINDOW_END_H


class TestWindowGatedTimers(unittest.TestCase):
    def test_gated_services_exist(self):
        self.assertTrue(list(_gated_services()))

    def test_gated_timers_fire_inside_window_in_et(self):
        for svc in _gated_services():
            timer = svc.with_suffix(".timer")
            with self.subTest(unit=svc.name):
                self.assertTrue(timer.exists(), f"{timer.name} missing")
                cals = re.findall(r"^OnCalendar=(.+)$", timer.read_text(), re.M)
                self.assertTrue(cals, f"{timer.name} has no OnCalendar")
                for cal in cals:
                    self.assertTrue(cal.strip().endswith("America/New_York"),
                                    f"{timer.name}: '{cal}' is host time (UTC), not ET")
                    m = re.search(r"\b(\d{1,2}):\d{2}(?::\d{2})?\b", cal)
                    self.assertIsNotNone(m, f"{timer.name}: no fixed time in '{cal}'")
                    self.assertTrue(_in_window(int(m.group(1))),
                                    f"{timer.name}: '{cal}' is outside the 23:00-05:00 ET window")


if __name__ == "__main__":
    unittest.main()
