"""tests/common/test_surface_event_log.py -- 2026-10-14: the append-only surface event log
(pg_schema/0075) keeps the ramp exit that surface_movement_events overwrites with the takeoff.
Synthetic identities only."""
from __future__ import annotations

import pytest

import common.db as db


@pytest.fixture
def fresh():
    db.init_db_all()
    db._surface_event_log_ready = False
    with db.conn() as c:
        c.execute(db._SURFACE_EVENT_LOG_DDL)
        c.execute("DELETE FROM surface_event_log")
    yield


def rec(event, t, airport="KDCA", track="7"):
    return {"track_id": track, "airport": airport, "callsign": "ZZZ101", "event": event, "status": "onsurface",
            "runway": None, "event_time": t, "departure_airport": "KDCA", "destination_airport": "KBOS",
            "last_seen": "2026-10-14T12:00:00Z"}


def rows():
    with db.conn() as c:
        return [dict(r) for r in c.execute("SELECT event, event_time FROM surface_event_log ORDER BY event_time")]


def test_ramp_exit_survives_the_takeoff_and_duplicates_are_ignored(fresh):
    assert db.append_surface_event_log(rec("spotout", "2026-10-14T11:40:00Z"))
    db.append_surface_event_log(rec("spotout", "2026-10-14T11:40:00Z"))     # the same message again
    assert db.append_surface_event_log(rec("off", "2026-10-14T11:55:00Z"))
    assert [r["event"] for r in rows()] == ["spotout", "off"]


def test_out_of_scope_is_not_logged(fresh):
    assert not db.append_surface_event_log(rec("off", "2026-10-14T11:55:00Z", airport="KATL"))
    assert not db.append_surface_event_log(rec("position", "2026-10-14T11:55:00Z"))
    assert not db.append_surface_event_log(rec("off", None))
    assert rows() == []


def test_prune_keeps_recent_rows(fresh):
    db.append_surface_event_log(rec("off", "2026-10-14T11:55:00Z"))
    with db.conn() as c:
        c.execute("UPDATE surface_event_log SET logged_at = '2026-01-01T00:00:00Z'")
    db.append_surface_event_log(rec("on", "2026-10-14T13:00:00Z", track="8") | {"last_seen": "2099-01-01T00:00:00Z"})
    assert db.prune_surface_event_log(days=120) == 1
    assert [r["event"] for r in rows()] == ["on"]
