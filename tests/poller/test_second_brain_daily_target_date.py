"""
tests/poller/test_second_brain_daily_target_date.py

2026-10-04: second_brain_daily keyed its vault day-file on date.today() at
execution. The poller image runs in UTC, so the 23:45 ET anchor firing
(03:45 UTC) wrote the NEXT day's file every night, and the shared-lock
queue (97b9075) could push it further. resolve_target_date() pins the
operational day (America/New_York, rollover at 05:00 local) once at
startup; these tests pin that rule and the override paths.
"""
from __future__ import annotations

import os
import unittest
from datetime import date, datetime, timezone

os.environ.setdefault("DISPATCH_DB_BACKEND", "sqlite")

from poller.skills import second_brain_daily as sbd  # noqa: E402


def _utc(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=timezone.utc)


class ResolveTargetDate(unittest.TestCase):
    def test_2345_anchor_resolved_after_midnight_is_previous_day(self):
        # 23:45 ET firing on Oct 2 that got the lock at 00:30 ET Oct 3
        # (= 04:30 UTC Oct 3) must still write 2026-10-02.md
        self.assertEqual(sbd.resolve_target_date(_utc(2026, 10, 3, 4, 30)), date(2026, 10, 2))

    def test_2345_anchor_resolved_same_evening_is_same_day(self):
        # 23:50 ET Oct 2 = 03:50 UTC Oct 3 -- the old code said Oct 3
        self.assertEqual(sbd.resolve_target_date(_utc(2026, 10, 3, 3, 50)), date(2026, 10, 2))

    def test_overnight_sibling_before_0500_is_previous_day(self):
        # 03:45 ET Oct 3 (2h sibling) = 07:45 UTC
        self.assertEqual(sbd.resolve_target_date(_utc(2026, 10, 3, 7, 45)), date(2026, 10, 2))

    def test_0500_local_is_rollover(self):
        # 05:00 ET Oct 3 = 09:00 UTC -> Oct 3
        self.assertEqual(sbd.resolve_target_date(_utc(2026, 10, 3, 9, 0)), date(2026, 10, 3))

    def test_midday_manual_run_is_today(self):
        # 13:00 ET Oct 3 = 17:00 UTC
        self.assertEqual(sbd.resolve_target_date(_utc(2026, 10, 3, 17, 0)), date(2026, 10, 3))

    def test_explicit_override_wins(self):
        self.assertEqual(sbd.resolve_target_date(_utc(2026, 10, 3, 17, 0), override="2026-09-30"),
                         date(2026, 9, 30))

    def test_naive_datetime_treated_as_utc(self):
        self.assertEqual(sbd.resolve_target_date(datetime(2026, 10, 3, 4, 30)), date(2026, 10, 2))

    def test_dst_boundary_november(self):
        # 2026-11-01 01:30 EDT (DST ends 02:00) = 05:30 UTC -> before 05:00 local -> Oct 31
        self.assertEqual(sbd.resolve_target_date(_utc(2026, 11, 1, 5, 30)), date(2026, 10, 31))


class OverrideSources(unittest.TestCase):
    def test_argv_forms(self):
        self.assertEqual(sbd._override_from_argv_env(["--date", "2026-10-01"]), "2026-10-01")
        self.assertEqual(sbd._override_from_argv_env(["--date=2026-10-01"]), "2026-10-01")

    def test_env_fallback(self):
        old = os.environ.get("SECOND_BRAIN_DAILY_DATE")
        os.environ["SECOND_BRAIN_DAILY_DATE"] = "2026-10-02"
        try:
            self.assertEqual(sbd._override_from_argv_env([]), "2026-10-02")
        finally:
            if old is None:
                del os.environ["SECOND_BRAIN_DAILY_DATE"]
            else:
                os.environ["SECOND_BRAIN_DAILY_DATE"] = old

    def test_no_override(self):
        os.environ.pop("SECOND_BRAIN_DAILY_DATE", None)
        self.assertIsNone(sbd._override_from_argv_env([]))


class WindowStart(unittest.TestCase):
    def test_window_is_local_midnight(self):
        ts = sbd.window_start_ts(date(2026, 10, 2))
        self.assertEqual(datetime.fromtimestamp(ts, tz=sbd.LOCAL_TZ).strftime("%Y-%m-%d %H:%M"),
                         "2026-10-02 00:00")
        # and it is 04:00 UTC on EDT dates
        self.assertEqual(datetime.fromtimestamp(ts, tz=timezone.utc).hour, 4)


if __name__ == "__main__":
    unittest.main()
