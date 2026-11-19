"""2026-11-19 operator: the overnight occurrence grace (America/New_York).

"Start it at 4 hours prior to midnight Eastern, and anything started between 8 pm Eastern and midnight gets a
grace through 2 am Eastern, as being the previous day or that same day, respectively."
Synthetic callsigns only.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

from common import flight_instance as fi
from shared import pending_identity as pid

ET = ZoneInfo("America/New_York")


def et(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=ET).timestamp()


def watch(date_ymd, **kw):
    e = {"id": f"wl-flight-zzq4700-{date_ymd}", "identifier": "ZZQ4700", "origin": "KDCA", "destination": "KORD"}
    e.update(kw)
    return e


# ── same_occurrence_day ──────────────────────────────────────────────────────

def test_evening_and_early_morning_are_the_same_day():
    assert fi.same_occurrence_day(et(2026, 11, 18, 22, 39), et(2026, 11, 19, 0, 39))


def test_before_20_et_is_not_graced():
    assert not fi.same_occurrence_day(et(2026, 11, 18, 19, 59), et(2026, 11, 19, 0, 30))


def test_after_02_et_is_not_graced():
    assert not fi.same_occurrence_day(et(2026, 11, 18, 23, 50), et(2026, 11, 19, 2, 1))


def test_two_days_apart_is_never_graced():
    assert not fi.same_occurrence_day(et(2026, 11, 17, 23, 0), et(2026, 11, 19, 0, 30))


def test_dst_spring_forward_night():
    # 2026-03-08: clocks go 02:00 -> 03:00 ET; wall-clock hours still decide
    assert fi.same_occurrence_day(et(2026, 3, 7, 21, 0), et(2026, 3, 8, 1, 30))
    assert not fi.same_occurrence_day(et(2026, 3, 7, 21, 0), et(2026, 3, 8, 3, 30))


def test_dst_fall_back_night():
    # 2026-11-01: 01:00-02:00 ET happens twice; both are inside the grace
    first_1am = datetime(2026, 11, 1, 5, 30, tzinfo=ZoneInfo("UTC")).timestamp()   # 01:30 EDT
    second_1am = datetime(2026, 11, 1, 6, 30, tzinfo=ZoneInfo("UTC")).timestamp()  # 01:30 EST
    ev = et(2026, 10, 31, 22, 0)
    assert fi.same_occurrence_day(ev, first_1am) and fi.same_occurrence_day(ev, second_1am)


# ── fdps_plan_occurrence_rejection (no schedule) ─────────────────────────────

def test_plan_2239_binds_watch_added_0039_next_day():
    plan = {"updated_at": et(2026, 11, 18, 22, 39)}
    assert fi.fdps_plan_occurrence_rejection(watch("20261119"), plan, now_epoch=et(2026, 11, 19, 0, 39)) is None


def test_plan_1959_does_not_bind_watch_added_0030():
    plan = {"updated_at": et(2026, 11, 18, 19, 59)}
    assert fi.fdps_plan_occurrence_rejection(watch("20261119"), plan, now_epoch=et(2026, 11, 19, 0, 30)) \
        == "untimed_plan_not_updated_on_watch_date"


def test_plan_2350_does_not_bind_after_0200():
    plan = {"updated_at": et(2026, 11, 18, 23, 50)}
    assert fi.fdps_plan_occurrence_rejection(watch("20261119"), plan, now_epoch=et(2026, 11, 19, 2, 1)) \
        == "untimed_plan_not_updated_on_watch_date"


def test_evening_departure_time_also_graced():
    plan = {"departure_time": et(2026, 11, 18, 23, 0)}
    assert fi.fdps_plan_occurrence_rejection(watch("20261119"), plan, now_epoch=et(2026, 11, 19, 1, 0)) is None


def test_four_day_old_plan_still_fails_closed():
    plan = {"updated_at": et(2026, 11, 15, 22, 0)}
    assert fi.fdps_plan_occurrence_rejection(watch("20261119"), plan, now_epoch=et(2026, 11, 19, 0, 30)) \
        == "untimed_plan_not_updated_on_watch_date"


# ── flight_plan_occurrence (route's current_occurrence) ─────────────────────

def test_evening_plan_is_current_until_0200():
    plan = {"updated_at": et(2026, 11, 18, 22, 0)}
    occ, cur = fi.flight_plan_occurrence(plan, now=et(2026, 11, 19, 1, 0))
    assert occ == "2026-11-18" and cur is True
    _, cur_after = fi.flight_plan_occurrence(plan, now=et(2026, 11, 19, 2, 30))
    assert cur_after is False


# ── pending-identity expiry ──────────────────────────────────────────────────

def test_evening_queued_watch_expires_at_0200_next_day():
    e = watch("20261118", added_at=et(2026, 11, 18, 21, 0))
    assert pid._day_end(e, et(2026, 11, 18, 21, 5)) == et(2026, 11, 19, 2, 0)


def test_daytime_queued_watch_expires_at_midnight():
    e = watch("20261118", added_at=et(2026, 11, 18, 14, 0))
    assert pid._day_end(e, et(2026, 11, 18, 14, 5)) == et(2026, 11, 19, 0, 0)
