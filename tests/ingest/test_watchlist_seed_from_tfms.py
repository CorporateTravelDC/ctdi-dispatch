"""
tests/ingest/test_watchlist_seed_from_tfms.py -- 2026-10-05 JZA825 incident:
a watch added mid-flight learns the departure it missed from TFMS (departure
side only), and a first airborne sighting is never labelled a takeoff.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from common import db  # noqa: E402
from shared import watchlist as wl  # noqa: E402


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture
def setup(monkeypatch):
    db.init_db_all()
    with db.conn() as c:                     # pg_schema/0065 lock columns (Postgres-only migration)
        have = {r[1] for r in c.execute("PRAGMA table_info(watchlist_entries)").fetchall()}
        for col, typ in (("oooi_lock_source", "TEXT"), ("oooi_lock_tier", "INTEGER"), ("oooi_lock_at", "TEXT"),
                         ("oooi_local_track_at", "TEXT"), ("oooi_local_track_source", "TEXT"),
                         ("oooi_authority_note", "TEXT")):
            if col not in have:
                c.execute(f"ALTER TABLE watchlist_entries ADD COLUMN {col} {typ}")
    hits = []
    monkeypatch.setattr(wl, "watchlist_event_hit", lambda eid, summary, detail, priority=3: hits.append((summary, detail, priority)))
    now = datetime.now(timezone.utc)
    entry = {"id": "wl-flight-jza825-test", "entry_type": "flight", "tier": "transient", "identifier": "JZA825",
             "origin": "CYUL", "destination": "KDCA", "route_name": None, "scheduled_departure": None,
             "scheduled_arrival": None, "auto_remove_at": _iso(now + timedelta(hours=6)), "added_at": _iso(now),
             "added_by": "test", "notes": None, "hex_id": None, "registration": None, "last_event_at": None,
             "last_event_summary": None}
    db.upsert_watchlist_entry(entry)
    yield entry, hits, now
    with db.conn() as c:
        c.execute("DELETE FROM watchlist_entries WHERE id = ?", (entry["id"],))
        c.execute("DELETE FROM flight_ooooi_times WHERE gufi LIKE 'TEST-%'")


def _phase(entry_id):
    return next(e for e in db.get_watchlist_entries() if e["id"] == entry_id).get("oooi_phase")


def test_added_mid_flight_seeds_off_only_even_with_early_airline_arrival_times(setup):
    entry, hits, now = setup
    db.upsert_flight_ooooi("TEST-1", callsign="JZA825", origin="CYUL", destination="DCA",
                           airline_out_time=_iso(now - timedelta(minutes=58)),
                           airline_off_time=_iso(now - timedelta(minutes=33)),
                           airline_on_time=_iso(now - timedelta(minutes=2)),      # airline "cosplaying early"
                           airline_in_time=_iso(now - timedelta(minutes=1)))
    assert wl.seed_departure_phase_from_tfms(entry) == "off"
    assert _phase(entry["id"]) == "off"
    summary, detail, prio = hits[0]
    assert "already airborne -- OFF" in summary and "per airline (TFMS)" in summary and prio == 4
    assert detail["watchlist_trigger"] == "oooi_seeded_at_add"


def test_future_or_other_route_seeds_nothing(setup):
    entry, hits, now = setup
    db.upsert_flight_ooooi("TEST-2", callsign="JZA825", origin="CYUL", destination="DCA",
                           airline_out_time=_iso(now + timedelta(minutes=20)),
                           airline_off_time=_iso(now + timedelta(minutes=40)))
    assert wl.seed_departure_phase_from_tfms(entry) is None
    with db.conn() as c:
        c.execute("DELETE FROM flight_ooooi_times WHERE gufi = 'TEST-2'")
    db.upsert_flight_ooooi("TEST-3", callsign="JZA825", origin="CYYZ", destination="DCA",
                           airline_off_time=_iso(now - timedelta(minutes=30)))
    assert wl.seed_departure_phase_from_tfms(entry) is None
    assert hits == [] and (_phase(entry["id"]) or "pre_departure") == "pre_departure"


def test_pushed_back_but_not_airborne_seeds_out(setup):
    entry, hits, now = setup
    db.upsert_flight_ooooi("TEST-4", callsign="JZA825", origin="CYUL", destination="DCA",
                           airline_out_time=_iso(now - timedelta(minutes=5)),
                           airline_off_time=_iso(now + timedelta(minutes=10)))
    assert wl.seed_departure_phase_from_tfms(entry) == "out"
    assert "already left the gate -- OUT" in hits[0][0]


def test_first_airborne_sighting_is_not_called_a_takeoff():
    src = (Path(__file__).parent.parent.parent / "src" / "poller" / "main.py").read_text()
    assert '("pre_departure", "off"): (f"{ident} first seen airborne (takeoff not observed)", 4)' in src
    assert 'OFF — airborne", 5)' not in src


def test_adsb_never_asserts_on_or_in():
    for src in ("adsb", "local_adsb", "dump1090"):
        for ph in ("on", "in"):
            ok, note = db._oooi_authority_check(ph, src, {})
            assert not ok and "ADS-B may never assert" in note


def test_swim_beats_airline_posted_times_where_swim_observes():
    dca_arrival = {"origin": "CYUL", "destination": "KDCA"}
    for ph in ("on", "in"):
        ok, note = db._oooi_authority_check(ph, "tfms_airline", dca_arrival)
        assert not ok and "SMES" in note
        assert db._oooi_authority_check(ph, "smes", dca_arrival)[0]
    assert db._oooi_authority_check("off", "tfms_airline", dca_arrival)[0]          # YUL: no SWIM surface there
    dca_departure = {"origin": "DCA", "destination": "CYYZ"}
    assert not db._oooi_authority_check("off", "tfms_airline", dca_departure)[0]
    assert db._oooi_authority_check("in", "tfms_airline", dca_departure)[0]         # YYZ: airline time still counts


def test_airline_cosplaying_early_cannot_land_a_dca_watch(setup):
    entry, hits, now = setup
    assert db.update_watchlist_oooi_phase_authoritative(entry["id"], "off", source="tfms_airline",
                                                        updated_at=_iso(now - timedelta(minutes=40)))
    assert not db.update_watchlist_oooi_phase_authoritative(entry["id"], "on", source="tfms_airline",
                                                            updated_at=_iso(now))
    assert _phase(entry["id"]) == "off"
    assert db.update_watchlist_oooi_phase_authoritative(entry["id"], "on", source="smes", updated_at=_iso(now))
    assert _phase(entry["id"]) == "on"
