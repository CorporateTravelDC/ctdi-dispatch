"""
2026-10-06: the host moved to UTC at 17:18:25Z. thermal-sample.sh stamped
host-local wall-clock and quiet-window-report.py read every row as Eastern,
skewing the rolling maintenance windows by 4 h. Rows are now stamped with an
explicit Z; legacy naive rows are Eastern before the cutover and UTC after.
"""
from __future__ import annotations

import importlib.util
import unittest
from datetime import timezone
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[2]
ET = ZoneInfo("America/New_York")


def load():
    spec = importlib.util.spec_from_file_location("qwr", REPO / "scripts" / "quiet-window-report.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class SampleTimezones(unittest.TestCase):
    def test_both_sides_of_the_cutover_land_on_the_same_instant_line(self):
        m = load()
        last_eastern = m.parse_sample_ts("2026-10-06 13:15:00")   # 17:15Z
        first_utc = m.parse_sample_ts("2026-10-06 17:20:06")      # 17:20Z
        explicit = m.parse_sample_ts("2026-10-06T17:25:01Z")
        self.assertEqual(last_eastern.astimezone(timezone.utc).hour, 17)
        self.assertEqual(first_utc.astimezone(timezone.utc).strftime("%H:%M"), "17:20")
        self.assertLess(last_eastern, first_utc)
        self.assertLess(first_utc, explicit)
        self.assertEqual(first_utc.astimezone(ET).hour, 13)

    def test_older_naive_rows_stay_eastern(self):
        m = load()
        self.assertEqual(m.parse_sample_ts("2026-09-15 03:00:00").astimezone(timezone.utc).hour, 7)

    def test_sampler_stamps_explicit_utc(self):
        self.assertIn("date -u '+%Y-%m-%dT%H:%M:%SZ'", (REPO / "scripts" / "thermal-sample.sh").read_text())
