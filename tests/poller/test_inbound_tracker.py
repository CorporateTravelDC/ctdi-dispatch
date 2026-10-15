"""2026-10-14 (flight-instance resolver, section 4): the inbound tracker is wired into the flight sweep.
One push per status transition; 'unknown' is never pushed. Synthetic identities only."""
import time

import pytest

import common.db as db
from common import flight_instance as fi


@pytest.fixture
def poller(monkeypatch):
    db.init_db_all()
    fi.clear_alias_cache()
    import shared.watchlist as wl
    hits = []
    monkeypatch.setattr(wl, "watchlist_event_hit",
                        lambda eid, summary, detail, priority=3: hits.append((summary, detail, priority)))
    import poller.main as pm
    pm._INBOUND_LAST_RUN.clear()
    pm._HEX_INSTANCES_CACHE.clear()
    yield pm, hits
    fi.clear_alias_cache()


def _entry(dep_ep, **kw):
    iso = lambda e: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(e))  # noqa: E731
    return {"id": f"wl-inbound-{int(dep_ep)}", "identifier": "ZZZ1893", "origin": "DCA", "destination": "FLL",
            "scheduled_departure": iso(dep_ep), "scheduled_arrival": iso(dep_ep + 2.5 * 3600),
            "hex_id": "000001", "registration": "N000TS", "oooi_phase": None, **kw}


def _inbound(arr_ep, type_="A321"):
    return fi.FlightInstance(key=fi.FlightInstanceKey.make("ZZZ1892", "FLL", "DCA", arr_ep - 2.5 * 3600),
                             hex="000001", est_arr=arr_ep, aircraft_type=type_)


def test_late_inbound_pushes_once_per_status(poller, monkeypatch):
    pm, hits = poller
    dep = time.time() + 2 * 3600
    monkeypatch.setattr(fi, "instances_for_hex", lambda hx, window_h=30.0: [_inbound(dep - 10 * 60)])
    e = _entry(dep)
    pm._check_flight_inbound(e, "ZZZ1893")
    pm._INBOUND_LAST_RUN.clear()
    pm._check_flight_inbound(e, "ZZZ1893")                 # same status again: no second push
    assert len(hits) == 1
    summary, detail, prio = hits[0]
    assert detail["watchlist_trigger"] == "inbound_late_beyond_turn" and prio == 4
    assert detail["min_turn_min"] == 35                     # A321 -> narrowbody


def test_unknown_inbound_is_not_pushed_and_departed_watches_are_skipped(poller, monkeypatch):
    pm, hits = poller
    dep = time.time() + 2 * 3600
    monkeypatch.setattr(fi, "instances_for_hex", lambda hx, window_h=30.0: [])
    pm._check_flight_inbound(_entry(dep), "ZZZ1893")
    called = []
    monkeypatch.setattr(fi, "instances_for_hex", lambda hx, window_h=30.0: called.append(1) or [])
    pm._INBOUND_LAST_RUN.clear()
    pm._check_flight_inbound(_entry(dep, oooi_phase="out"), "ZZZ1893")
    pm._check_flight_inbound(_entry(time.time() + 12 * 3600), "ZZZ1893")   # too early
    assert hits == [] and called == []


def test_aircraft_class_for_type():
    assert fi.aircraft_class_for_type("B789") == "widebody"
    assert fi.aircraft_class_for_type("A319") == "narrowbody"
    assert fi.aircraft_class_for_type("CRJ9") == "regional"
    assert fi.aircraft_class_for_type(None) == "narrowbody"


def test_schedule_derived_when_the_watch_has_none(poller, monkeypatch):
    # swarm audit NB-04: 0 of 13 live watches carry scheduled_departure; today's unique instance is used
    pm, _ = poller
    dep = time.time() + 3 * 3600
    inst = fi.FlightInstance(key=fi.FlightInstanceKey.make("ZZZ1893", "DCA", "FLL", dep), sched_dep=dep)
    other_day = fi.FlightInstance(key=fi.FlightInstanceKey.make("ZZZ1893", "DCA", "FLL", dep + 2 * 86400),
                                  sched_dep=dep + 2 * 86400)
    monkeypatch.setattr(fi, "resolve", lambda *a, **k: [inst, other_day])
    assert pm._watch_sched_dep({"origin": "KDCA", "destination": "KFLL"}, "ZZZ1893") == round(dep)
    second = fi.FlightInstance(key=fi.FlightInstanceKey.make("ZZZ1893", "DCA", "FLL", dep + 3600), sched_dep=dep + 3600)
    monkeypatch.setattr(fi, "resolve", lambda *a, **k: [inst, second])
    assert pm._watch_sched_dep({"origin": "KDCA", "destination": "KFLL"}, "ZZZ1893") is None   # not unique: no guess


def test_hex_scan_is_cached_per_aircraft(poller, monkeypatch):
    pm, _ = poller
    pm._HEX_INSTANCES_CACHE.clear()
    calls = []
    monkeypatch.setattr(fi, "instances_for_hex", lambda hx, window_h=30.0: calls.append(hx) or [])
    pm._hex_instances("000001"); pm._hex_instances("000001"); pm._hex_instances("000002")
    assert calls == ["000001", "000002"]
