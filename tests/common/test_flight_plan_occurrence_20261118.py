"""
tests/common/test_flight_plan_occurrence_20261118.py -- 2026-11-18 (UA4006 KSAV-KIAD):
common.flight_instance.flight_plan_occurrence decides which FDPS row is the current
occurrence for /api/v1/flightplan (route tests: tests/web/test_flightplan_occurrence_20261118.py).
Pure logic, no web import.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from common.flight_instance import flight_plan_occurrence

NY = ZoneInfo("America/New_York")


def test_yesterdays_landed_row_is_not_current():
    now = datetime(2026, 11, 18, 13, 30, tzinfo=NY).timestamp()
    arr = datetime(2026, 11, 17, 15, 0, tzinfo=NY).timestamp()      # landed yesterday 15:00 ET
    assert flight_plan_occurrence({"departure_time": None, "arrival_time": arr}, now) == ("2026-11-17", False)


def test_todays_row_is_current():
    now = datetime(2026, 11, 18, 13, 30, tzinfo=NY).timestamp()
    dep = datetime(2026, 11, 18, 13, 35, tzinfo=NY).timestamp()
    assert flight_plan_occurrence({"departure_time": dep}, now) == ("2026-11-18", True)


def test_evening_leg_across_midnight_stays_current_while_airborne():
    now = datetime(2026, 11, 19, 0, 40, tzinfo=NY).timestamp()          # 00:40 ET
    dep = datetime(2026, 11, 18, 22, 50, tzinfo=NY).timestamp()          # departed 22:50 ET the day before
    assert flight_plan_occurrence({"departure_time": dep, "arrival_time": now + 1800}, now) == ("2026-11-18", True)


@pytest.mark.parametrize("plan", [{}, {"departure_time": None, "arrival_time": None, "updated_at": None}])
def test_untimed_row_fails_closed(plan):
    assert flight_plan_occurrence(plan) == (None, False)
