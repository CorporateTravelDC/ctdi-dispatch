"""2026-10-14 (operator): the TDES departure clearance is a tail source for every watched flight
departing DCA/IAD/BWI -- linked by the FAA-internal GUFI to the FDPS plan, whose aircraft address is the
tail. Synthetic identities only (hex 000001/000002, tails N000TS/N001TS, flights ZZZ...)."""
import time

import pytest

import common.db as db
from common import flight_instance as fi

LONG_GUFI = "us.fdps.2026-10-14T12:00:00Z.000/00/000"


def _iso(ep):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ep))


def _raw(callsign, hex_id, reg, long_gufi=LONG_GUFI):
    return ("<MessageCollection><message><flight>"
            f"<flightIdentification aircraftIdentification=\"{callsign}\"/>"
            f"<aircraftDescription aircraftAddress=\"{hex_id}\" registration=\"{reg}\"/>"
            f"<nameValue name=\"FDPS_GUFI\" value=\"{long_gufi}\"/>"
            "</flight></message></MessageCollection>")


@pytest.fixture
def tdb():
    db.init_db_all()
    from common import db_swim
    db_swim.init_db_swim_v41()
    fi.clear_alias_cache()
    with db.conn() as c:
        c.execute("DELETE FROM tdes_departure_events")
    yield
    fi.clear_alias_cache()


def _clearance(callsign, event_ep, airport="KDCA", long_gufi=LONG_GUFI):
    with db.conn() as c:
        c.execute("INSERT INTO tdes_departure_events (airport, callsign, event_time, sfdps_gufi, last_seen) "
                  "VALUES (?, ?, ?, ?, ?)", (airport, callsign, _iso(event_ep), long_gufi, _iso(event_ep)))


def test_clearance_links_to_the_plan_and_its_tail(tdb):
    dep = time.time() + 1800
    db.upsert_flight_event("g-tdes-1", "ZZZ", "4520", "DCA", "ORD", None, None, None, "proposed",
                           None, None, None, None, _raw("ZZZ4520", "000002", "N001TS"))
    _clearance("ZZZ4520", dep - 900)
    plan = fi.tdes_clearance_plan(["ZZZ4520"], "DCA", _iso(dep))
    assert plan is not None and plan.hex == "000002" and plan.registration == "N001TS"
    # a clearance far from this instance's scheduled departure belongs to another instance
    assert fi.tdes_clearance_plan(["ZZZ4520"], "DCA", _iso(dep + 8 * 3600)) is None
    # another origin: nothing
    assert fi.tdes_clearance_plan(["ZZZ4520"], "IAD", _iso(dep)) is None


def test_tdes_is_a_swim_tail_source():
    assert "tdes" in fi.AUTHORITATIVE_TAIL_SOURCES and "tdes" in fi.SWIM_TAIL_SOURCES


def test_poller_routes_a_tdes_tail_through_the_guard(tdb, monkeypatch):
    import poller.main as pm
    pm._TDES_TAIL_LAST_RUN.clear()
    pm._TDES_LAST_CANDIDATE.clear()
    dep = time.time() + 1800
    plan = fi.FlightInstance(key=fi.FlightInstanceKey.make("ZZZ4520", "DCA", "ORD", _iso(dep)),
                             hex="000002", registration="N001TS", gufi="g-tdes-1")
    monkeypatch.setattr(fi, "tdes_clearance_plan", lambda *a, **k: plan)
    calls = []
    monkeypatch.setattr(pm, "_guarded_tail_change", lambda entry, ident, **kw: calls.append(kw))
    entry = {"id": "wl-tdes", "identifier": "ZZZ4520", "origin": "KDCA", "destination": "KORD",
             "scheduled_departure": _iso(dep), "hex_id": "000001", "registration": "N000TS", "oooi_phase": None}
    pm._check_flight_tdes_tail(entry, "ZZZ4520")
    assert calls and calls[0]["source"] == "tdes" and calls[0]["new_hex"] == "000002"
    # not from a non-DC origin, and not once airborne
    calls.clear(); pm._TDES_TAIL_LAST_RUN.clear()
    pm._check_flight_tdes_tail(dict(entry, origin="KATL"), "ZZZ4520")
    pm._check_flight_tdes_tail(dict(entry, id="wl-tdes-2", oooi_phase="off"), "ZZZ4520")
    assert calls == []
