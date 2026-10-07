"""tests/common/test_optime.py -- common.optime (2026-10-04, backlog #9)."""
from __future__ import annotations

import os
import unittest
from datetime import date, datetime, timezone
from unittest import mock

from common import optime


def _utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


class Defaults(unittest.TestCase):
    def setUp(self):
        self._env = mock.patch.dict(os.environ, {}, clear=False)
        self._env.start()
        os.environ.pop("DISPATCH_LOCAL_TZ", None); os.environ.pop("DISPATCH_DAY_ROLLOVER", None)

    def tearDown(self):
        self._env.stop()

    def test_late_evening_et_is_same_local_day_not_utc_day(self):
        # 23:50 ET on 10-03 = 03:50Z on 10-04: date.today() in a UTC container said 10-04
        self.assertEqual(optime.op_today(_utc(2026, 10, 4, 3, 50)), date(2026, 10, 3))

    def test_before_rollover_belongs_to_previous_day(self):
        self.assertEqual(optime.op_today(_utc(2026, 10, 4, 8, 30)), date(2026, 10, 3))   # 04:30 ET

    def test_at_rollover_is_new_day(self):
        self.assertEqual(optime.op_today(_utc(2026, 10, 4, 9, 0)), date(2026, 10, 4))    # 05:00 ET

    def test_override_wins(self):
        self.assertEqual(optime.resolve_target_date(_utc(2026, 10, 4, 12), "2026-09-30"), date(2026, 9, 30))

    def test_naive_now_is_utc(self):
        self.assertEqual(optime.op_today(datetime(2026, 10, 4, 3, 50)), date(2026, 10, 3))

    def test_day_window_is_local_midnight_to_midnight(self):
        s, e = optime.op_day_window(date(2026, 10, 3))
        self.assertEqual(datetime.fromtimestamp(s, timezone.utc), _utc(2026, 10, 3, 4, 0))
        self.assertEqual(e - s, 86400)

    def test_dst_end_day_is_25h(self):
        s, e = optime.op_day_window(date(2026, 11, 1))
        self.assertEqual(e - s, 25 * 3600)

    def test_dst_start_day_is_23h(self):
        s, e = optime.op_day_window(date(2026, 3, 8))
        self.assertEqual(e - s, 23 * 3600)

    def test_dst_end_rollover(self):
        # 2026-11-01 05:30Z = 01:30 EDT/EST ambiguity zone -> before 05:00 -> previous day
        self.assertEqual(optime.op_today(_utc(2026, 11, 1, 5, 30)), date(2026, 10, 31))

    def test_to_local_and_to_utc_roundtrip(self):
        ep = _utc(2026, 7, 1, 16, 0).timestamp()
        loc = optime.to_local(ep)
        self.assertEqual(loc.hour, 12)
        self.assertEqual(optime.to_utc(loc), _utc(2026, 7, 1, 16, 0))
        self.assertEqual(optime.to_utc(datetime(2026, 7, 1, 12, 0)), _utc(2026, 7, 1, 16, 0))  # naive = local here


class Configurable(unittest.TestCase):
    def test_env_overrides(self):
        with mock.patch.dict(os.environ, {"DISPATCH_LOCAL_TZ": "UTC", "DISPATCH_DAY_ROLLOVER": "00:00"}):
            self.assertEqual(optime.op_today(_utc(2026, 10, 4, 3, 50)), date(2026, 10, 4))


if __name__ == "__main__":
    unittest.main()
