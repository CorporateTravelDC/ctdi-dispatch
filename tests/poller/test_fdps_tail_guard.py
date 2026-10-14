"""
2026-10-13 -- the FDPS tail-change repoint in poller.main._check_flight_fdps_cache
now goes through common.flight_instance.decide_tail_change, and a plan for
another leg of a reused number is ignored outright.

Before: any FDPS plan found for the callsign with a different hex repointed
the watch (and a hex-only plan wrote registration=None over the old tail),
including the plan of a same-number flight on another route.
Identities are synthetic (hex 000001/000002, tails N000TS/N001TS).
"""
import time

import pytest

import common.db as db
from common import flight_instance as fi


def _raw(callsign, hex_id, reg=None):
    reg_attr = f' registration="{reg}"' if reg else ""
    return ("<MessageCollection><message><flight>"
            f"<flightIdentification aircraftIdentification=\"{callsign}\"/>"
            f"<aircraftDescription aircraftAddress=\"{hex_id}\"{reg_attr}/>"
            "</flight></message></MessageCollection>")


def _iso(ep):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ep))


@pytest.fixture
def poller(monkeypatch):
    db.init_db_all()
    fi.clear_alias_cache()
    import shared.watchlist as wl
    hits = []
    monkeypatch.setattr(wl, "watchlist_event_hit",
                        lambda eid, summary, detail, priority=3: hits.append((summary, detail)))
    import poller.main as pm
    yield pm, hits
    fi.clear_alias_cache()


def _watch(dep_ep, origin="DCA", dest="ORD", ident="ZZZ4520"):
    e = {"id": "wl-flight-zzz4520-test", "entry_type": "flight", "tier": "transient",
         "identifier": ident, "origin": origin, "destination": dest, "route_name": None,
         "scheduled_departure": _iso(dep_ep), "scheduled_arrival": _iso(dep_ep + 2 * 3600),
         "auto_remove_at": None, "added_at": "2026-10-11T00:00:00Z", "added_by": "test",
         "notes": None, "last_event_at": None, "last_event_summary": None,
         "hex_id": "000001", "registration": "N000TS"}
    db.upsert_watchlist_entry(e)
    return next(x for x in db.get_watchlist_entries(entry_type="flight") if x["id"] == e["id"])


def test_rejected_change_keeps_the_hex_and_notes_once(poller):
    pm, hits = poller
    dep = time.time() + 3 * 3600
    entry = _watch(dep)
    # FAA plan for the watched leg now names 000002, but 000002 is also filed on another
    # leg that overlaps it -> rule (b) rejects (2026-10-13: FDPS showing this flight is
    # enough on its own otherwise; there is no "unverified inbound" acceptance)
    db.upsert_flight_event("G-W", "ZZZ", "4520", "KDCA", "KORD", None, None, None, "active",
                           None, None, None, None, _raw("ZZZ4520", "000002", "N001TS"))
    db.upsert_flight_event("G-O", "ZZZ", "4999", "KBOS", "KMIA", None, dep - 1800, dep + 3 * 3600, "active",
                           None, None, None, None, _raw("ZZZ4999", "000002", "N001TS"))
    pm._check_flight_fdps_cache(entry, "ZZZ4520")
    pm._check_flight_fdps_cache(entry, "ZZZ4520")

    stored = next(x for x in db.get_watchlist_entries(entry_type="flight") if x["id"] == entry["id"])
    assert stored["hex_id"] == "000001" and stored["registration"] == "N000TS"
    notes = [s for s, d in hits if d.get("watchlist_trigger") == "fdps_tail_change_rejected"]
    assert len(notes) == 1
    assert "tail change NOT accepted" in notes[0] and "filed on another instance" in notes[0]


def test_accepted_change_repoints_with_reason_and_resolves_tail(poller):
    pm, hits = poller
    dep = time.time() + 3 * 3600
    entry = _watch(dep)
    with db.conn() as c:
        c.execute("INSERT INTO faa_aircraft_registry (n_number, mode_s_hex, updated_at) VALUES (?, ?, ?)",
                  ("001TS", "000002", 0))
    # hex-only plan shape for the watched leg ...
    db.upsert_flight_event("G-W", "ZZZ", "4520", "KDCA", "KORD", None, None, None, "active",
                           None, None, None, None, _raw("ZZZ4520", "000002"))
    # (no previous leg on file: FDPS showing the tail on this flight is the proof, 2026-10-13)
    pm._check_flight_fdps_cache(entry, "ZZZ4520")

    stored = next(x for x in db.get_watchlist_entries(entry_type="flight") if x["id"] == entry["id"])
    assert stored["hex_id"] == "000002"
    assert stored["registration"] == "N001TS"      # looked up, not overwritten with None
    change = [(s, d) for s, d in hits if d.get("watchlist_trigger") == "fdps_tail_change"]
    assert len(change) == 1 and "FDPS shows this tail on this flight" in change[0][1]["reason"]


def test_plan_for_another_leg_is_ignored(poller):
    pm, hits = poller
    entry = _watch(time.time() + 3 * 3600)
    # same number, opposite direction: not this watch's tail, status or diversion
    db.upsert_flight_event("G-X", "ZZZ", "4520", "KORD", "KDCA", None, None, None, "active",
                           None, None, None, None, _raw("ZZZ4520", "000002"))
    pm._check_flight_fdps_cache(entry, "ZZZ4520")
    stored = next(x for x in db.get_watchlist_entries(entry_type="flight") if x["id"] == entry["id"])
    assert stored["hex_id"] == "000001" and stored["last_fdps_status"] is None
    assert hits == []
