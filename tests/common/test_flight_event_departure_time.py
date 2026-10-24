"""
2026-10-23: flight_events.departure_time was COALESCE(existing, new) -- the first
filing ever stored won. FDPS rows with no gufi are keyed by callsign and reused
across days, so departure_time could be days old on a fresh row. A newer filing
now replaces it; a NULL incoming value still keeps the stored one; an older
(out-of-order) message cannot roll it back. Synthetic identifiers only.
"""
from __future__ import annotations

import pytest

import common.db as db


@pytest.fixture
def fdb():
    db.init_db_all()
    yield db


def _plan(dep, msg_time=None, cs="QQQ123"):
    db.upsert_flight_event(cs, "QQQ", "123", "KQQA", "KQQB", None, dep, None, "active",
                           None, None, None, None, "<x/>", message_time=msg_time)


def _row(cs="QQQ123"):
    with db.conn() as c:
        return dict(c.execute("SELECT departure_time, updated_at FROM flight_events WHERE flight_id=?",
                              (cs,)).fetchone())


DAY1 = 1792800000.0          # a filed departure on day 1
DAY2 = DAY1 + 86400.0        # the same callsign's plan the next day


def test_next_days_plan_replaces_the_stored_departure_time(fdb):
    _plan(DAY1)
    assert _row()["departure_time"] == DAY1
    _plan(DAY2)
    assert _row()["departure_time"] == DAY2


def test_a_message_without_a_departure_time_keeps_the_stored_one(fdb):
    _plan(DAY1)
    _plan(None)                                  # e.g. a TH position message
    assert _row()["departure_time"] == DAY1


def test_an_older_out_of_order_message_does_not_roll_it_back(fdb):
    _plan(DAY2)
    stored = _row()["updated_at"]
    _plan(DAY1, msg_time=stored - 3600)          # older than the stored row
    assert _row()["departure_time"] == DAY2
    _plan(DAY1 + 2 * 86400, msg_time=stored + 60)   # newer message
    assert _row()["departure_time"] == DAY1 + 2 * 86400
