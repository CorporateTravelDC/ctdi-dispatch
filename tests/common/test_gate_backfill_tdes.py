"""Phase D1 (2026-10-20): historical departure gates from stored TDES clearances.

Isolated SQLite (tests/conftest.py); synthetic flights only (09xx), no real tail,
hex or registration. Covers the design's test list: idempotent, the 24 h cut,
LADD page drop, LADD failure stops the run, keyset coverage exactly once, no push."""
from __future__ import annotations

import calendar
import importlib.util
import time
from pathlib import Path

import pytest

import common.db as db
from common import db_swim
from common import flight_instance as fi
from common import gate_assignments as ga

_P = Path(__file__).resolve().parents[2] / "scripts" / "gate-backfill-tdes.py"
_spec = importlib.util.spec_from_file_location("gate_backfill_tdes", _P)
bf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bf)


def ep(iso: str) -> float:
    return float(calendar.timegm(time.strptime(iso, "%Y-%m-%dT%H:%M:%SZ")))


NOW = ep("2026-10-20T12:00:00Z")


@pytest.fixture
def gdb(monkeypatch):
    db.init_db_all()
    db_swim.init_db_swim_v41()
    db_swim.init_db_swim_v42()
    fi.clear_alias_cache()
    monkeypatch.setattr(db, "faa_ladd_listed_batch", lambda ids: set())
    monkeypatch.setattr(db, "faa_ladd_hex_batch", lambda hx: set())
    monkeypatch.setattr(bf, "wait_for_headroom", lambda *a, **k: 0.0)
    yield
    fi.clear_alias_cache()


@pytest.fixture
def no_push(monkeypatch):
    import shared.watchlist as wl
    calls = []
    monkeypatch.setattr(wl, "watchlist_event_hit", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(ga, "push_watch_changes", lambda *a, **k: calls.append(a))
    return calls


def tdes(callsign, et, gate="C27", airport="DCA", dest="ORD"):
    db_swim.insert_tdes_departure_event(
        airport=airport, callsign=callsign, event_time=et, beacon_code=None, aircraft_type=None,
        computer_id=None, clearance_delivery_time=None, parking_gate=gate, eram_gufi=None,
        sfdps_gufi=None, destination_airport=dest, last_seen=et)


def obs_count():
    with db.conn() as c:
        return c.execute("SELECT count(*) AS n FROM gate_baggage_observations").fetchone()["n"]


def run(**kw):
    return bf.run(dry_run=False, now=NOW, headroom=lambda: 0.0, **kw)


def test_backfill_stores_old_clearances_and_rerun_stores_nothing(gdb, no_push):
    tdes("UAL0901", "2026-10-15T12:05:00.000Z")
    tdes("UAL0902", "2026-10-16T12:05:00.000Z", gate="D4")
    first = run()
    assert first["stored"] == 2 and obs_count() == 2
    second = run()
    assert second["stored"] == 0 and second["duplicates"] == 2 and obs_count() == 2
    assert no_push == []


def test_history_rows_carry_the_backfill_reason(gdb, no_push):
    tdes("UAL0903", "2026-10-15T12:05:00.000Z")
    run()
    with db.conn() as c:
        reasons = {r["reason"] for r in c.execute("SELECT reason FROM gate_baggage_assignment_history")}
    assert reasons == {"backfill:tdes"}


def test_the_24h_cut_never_touches_a_recent_leg(gdb, no_push):
    tdes("UAL0904", "2026-10-15T12:05:00.000Z")          # old: stored
    tdes("UAL0905", "2026-10-19T08:00:00.000Z")          # 28 h before NOW: could bind a leg < 24 h old
    tdes("UAL0906", "2026-10-20T10:00:00.000Z")          # 2 h before NOW
    c = run()
    assert c["stored"] == 1 and c["skipped_recent"] == 2 and c["rows"] == 1


def test_ladd_listed_rows_are_skipped_per_page(gdb, no_push, monkeypatch):
    tdes("UAL0907", "2026-10-15T12:05:00.000Z")
    tdes("UAL0908", "2026-10-15T12:06:00.000Z")
    monkeypatch.setattr(db, "faa_ladd_listed_batch", lambda ids: {i for i in ids if i == "UAL0908"})
    c = run()
    assert c["skipped_ladd"] == 1 and c["stored"] == 1


def test_ladd_failure_stops_the_run_and_stores_nothing_more(gdb, no_push, monkeypatch):
    for i in range(5):
        tdes(f"UAL091{i}", f"2026-10-15T12:0{i}:00.000Z")

    def boom(ids):
        raise RuntimeError("db down")
    monkeypatch.setattr(db, "faa_ladd_listed_batch", boom)
    assert bf.main(["--limit", "5"]) == 5
    assert obs_count() == 0


def test_keyset_paging_visits_every_row_exactly_once(gdb, no_push, monkeypatch):
    for i in range(7):                                    # shared event_time forces the tiebreak
        tdes(f"UAL092{i}", "2026-10-15T12:05:00.000Z", airport="DCA" if i % 2 else "IAD")
    tdes("UAL0930", "2026-10-15T12:06:00.000Z")
    cut = bf.iso(NOW - bf.CUT_S)
    seen = [(r["event_time"], r["airport"], r["callsign"]) for p in bf.pages(cut, page=3) for r in p]
    assert len(seen) == 8 and len(set(seen)) == 8 and seen == sorted(seen)


def test_dry_run_counts_and_writes_nothing(gdb, no_push):
    tdes("UAL0931", "2026-10-15T12:05:00.000Z")
    tdes("UAL0932", "2026-10-15T12:06:00.000Z", gate="G")
    tdes("UAL0933", "2026-10-15T12:07:00.000Z", gate=None)
    c = bf.run(dry_run=True, now=NOW, headroom=lambda: 0.0)
    assert obs_count() == 0
    assert c["would_store"] == 2 and c["placeholders"] == 1 and c["no_gate"] == 1


def test_overlap_with_a_live_write_is_a_duplicate_not_a_second_row(gdb, no_push):
    rec = {"airport": "DCA", "callsign": "UAL0934", "event_time": "2026-10-15T12:05:00.000Z",
           "parking_gate": "C9", "destination_airport": "ORD"}
    tdes("UAL0934", rec["event_time"], gate="C9")
    ga.record_tdes(rec)                                   # the live path, earlier
    c = run()
    assert c["stored"] == 0 and c["duplicates"] == 1 and obs_count() == 1


def test_g_prefix_report_is_counts_only(gdb):
    tdes("UAL0935", "2026-10-15T12:05:00.000Z", gate="GC33")
    tdes("UAL0936", "2026-10-15T12:06:00.000Z", gate="C33")
    tdes("UAL0937", "2026-10-15T12:07:00.000Z", gate="N/A")
    assert bf.g_prefix_report() == {"DCA": {"g_prefixed": 1, "plain": 1, "placeholder": 1}}
