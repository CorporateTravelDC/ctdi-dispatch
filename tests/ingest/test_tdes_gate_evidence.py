"""2026-10-18 (operator, gate batch): a TDES clearance's parkingGate is SWIM
departure-gate evidence; the TDES watchlist push dedups on leg + gate (never the
event_time), never shows a placeholder as a gate, and marks the departure-gate
slot so the poller's resolved-assignment push does not repeat it.
Isolated SQLite, captured pushes, synthetic flights."""
from __future__ import annotations

import time

import pytest

import common.db as db
from common import flight_instance as fi
from common import gate_assignments as ga


def _iso(e):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(e))


@pytest.fixture
def tdes(monkeypatch):
    db.init_db_all()
    fi.clear_alias_cache()
    monkeypatch.setattr(db, "faa_ladd_listed_batch", lambda ids: set())
    monkeypatch.setattr(db, "faa_ladd_hex_batch", lambda hx: set())
    from common import db_swim
    monkeypatch.setattr(db_swim, "insert_tdes_departure_event", lambda **k: True)
    import shared.watchlist as wl
    hits = []
    monkeypatch.setattr(wl, "watchlist_event_hit",
                        lambda eid, summary, detail, priority=3: hits.append((summary, detail)))
    from ingest.parsers import smes_parser
    entry = {"id": "wl-tdes-1", "identifier": "UAL989", "origin": "DCA", "destination": "ORD"}
    monkeypatch.setattr(smes_parser, "_match_watchlist_flight", lambda rec: entry)
    dep = time.time() + 1800
    with db.conn() as c:
        c.execute("INSERT INTO flight_ooooi_times (gufi, callsign, airline, flight_num, origin, destination, "
                  "original_departure, original_arrival, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                  ("G-UAL989", "UAL989", "UAL", "989", "KDCA", "KORD", _iso(dep), _iso(dep + 7000), time.time()))
    yield smes_parser, entry, hits, dep
    fi.clear_alias_cache()


def _rec(gate, et):
    return {"kind": "tdes_departure", "airport": "KDCA", "callsign": "UAL989", "event_time": _iso(et),
            "parking_gate": gate, "destination_airport": "KORD", "last_seen": _iso(et)}


def test_reclearance_at_the_same_gate_does_not_push_again(tdes):
    sp, entry, hits, dep = tdes
    assert sp.handle_tdes_apds_record(_rec("C27", dep - 1500)) is True
    sp.handle_tdes_apds_record(_rec("C27", dep - 600))          # new event_time, same gate
    assert len(hits) == 1 and "gate C27" in hits[0][0]
    key = ga.make_key("UAL", "989", "DCA", "ORD", dep)
    assert ga.leg_assignments(key)["departure_gate"]["value"] == "C27"
    # the poller's resolved-assignment push sees the slot as already pushed
    assert ga.push_watch_changes(entry, "UAL989", key) == 0
    sp.handle_tdes_apds_record(_rec("C29", dep - 300))          # a real gate change pushes
    assert len(hits) == 2 and "gate C29" in hits[1][0]


def test_placeholder_gate_is_never_shown_as_a_gate(tdes):
    sp, entry, hits, dep = tdes
    sp.handle_tdes_apds_record(_rec("G", dep - 1500))
    assert hits and "gate" not in hits[0][0]
    key = ga.make_key("UAL", "989", "DCA", "ORD", dep)
    assert ga.leg_assignments(key)["departure_gate"]["status"] == "unknown"


def test_gate_store_failure_never_blocks_the_tdes_row_or_push(tdes, monkeypatch):
    sp, entry, hits, dep = tdes

    def boom(rec):
        raise RuntimeError("gate tables missing")
    monkeypatch.setattr(ga, "record_tdes", boom)
    assert sp.handle_tdes_apds_record(_rec("C27", dep - 1500)) is True
    assert len(hits) == 1
