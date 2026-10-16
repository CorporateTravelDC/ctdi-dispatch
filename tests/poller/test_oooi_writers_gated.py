"""
tests/poller/test_oooi_writers_gated.py -- (operator 2026-10-16): OOOI authority
hard-set to SWIM for every writer. The three phase writers that bypassed
db._oooi_authority_check through the lock-less db.update_watchlist_oooi_phase()
(the poller OOOI block, the schedule-inference IN, the sweep-time ACARS phase)
now go through update_watchlist_oooi_phase_authoritative() and name their
source; schedule inference never writes the asserted phase at all. The plain
writer itself requires a source and delegates to the gate, so nothing in the
tree can write a phase without naming who said so.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import poller.main as pm  # noqa: E402
import shared.watchlist as wl  # noqa: E402
from common import db  # noqa: E402

SRC = Path(__file__).resolve().parents[2] / "src"


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _entry(**over) -> dict:
    base = {"id": "wl-flight-ual123-test", "entry_type": "flight", "tier": "transient",
            "identifier": "UAL123", "origin": "KORD", "destination": "KDCA", "route_name": None,
            "scheduled_departure": None, "scheduled_arrival": None,
            "auto_remove_at": _iso(datetime.now(timezone.utc) + timedelta(hours=6)),
            "added_at": _iso(datetime.now(timezone.utc)), "added_by": "test", "notes": None,
            "last_event_at": None, "last_event_summary": None, "hex_id": "a1b2c3",
            "registration": "Q3QH2", "oooi_phase": None}
    base.update(over)
    return base


def _init_db_with_lock_columns() -> None:
    """init_db_all() plus the pg_schema/0065 lock columns (a Postgres-only migration), the
    same shim tests/ingest/test_watchlist_seed_from_tfms.py uses."""
    db.init_db_all()
    with db.conn() as c:
        have = {r[1] for r in c.execute("PRAGMA table_info(watchlist_entries)").fetchall()}
        for col, typ in (("oooi_lock_source", "TEXT"), ("oooi_lock_tier", "INTEGER"), ("oooi_lock_at", "TEXT"),
                         ("oooi_local_track_at", "TEXT"), ("oooi_local_track_source", "TEXT"),
                         ("oooi_authority_note", "TEXT")):
            if col not in have:
                c.execute(f"ALTER TABLE watchlist_entries ADD COLUMN {col} {typ}")


# -- the gate itself, on a real (tmp_path) sqlite ------------------------------

def test_plain_writer_requires_a_source_and_goes_through_the_gate(monkeypatch):
    _init_db_with_lock_columns()
    e = _entry()
    db.upsert_watchlist_entry(e)
    ts = _iso(datetime.now(timezone.utc))
    with pytest.raises(TypeError):
        db.update_watchlist_oooi_phase(e["id"], "off", ts)          # no source: refused at the call
    assert db.update_watchlist_oooi_phase(e["id"], "in", ts, source="adsb") is False      # ADS-B never ON/IN
    assert db.update_watchlist_oooi_phase(e["id"], "in", ts, source="schedule_inference") is False
    assert db.update_watchlist_oooi_phase(e["id"], "on", ts, source="fids") is False
    row = db.get_watchlist_entries(entry_type="flight")[0]
    assert row["oooi_phase"] is None and "DENY" in (row["oooi_authority_note"] or "")
    assert db.update_watchlist_oooi_phase(e["id"], "off", ts, source="fdps") is True
    row = db.get_watchlist_entries(entry_type="flight")[0]
    assert row["oooi_phase"] == "off" and row["oooi_source"] == "fdps" and row["oooi_lock_tier"] == 40


def test_non_swim_source_cannot_overwrite_a_swim_asserted_phase(monkeypatch):
    _init_db_with_lock_columns()
    e = _entry()
    db.upsert_watchlist_entry(e)
    ts = _iso(datetime.now(timezone.utc))
    assert db.update_watchlist_oooi_phase_authoritative(e["id"], "off", source="fdps", updated_at=ts)
    monkeypatch.setattr(db, "swim_is_live", lambda *a, **k: True)
    assert db.update_watchlist_oooi_phase_authoritative(e["id"], "on", source="acars", updated_at=ts) is False
    assert db.update_watchlist_oooi_phase_authoritative(e["id"], "on", source="adsb", updated_at=ts) is False
    assert db.get_watchlist_entries(entry_type="flight")[0]["oooi_phase"] == "off"
    assert db.update_watchlist_oooi_phase_authoritative(e["id"], "on", source="smes", updated_at=ts) is True
    assert db.get_watchlist_entries(entry_type="flight")[0]["oooi_phase"] == "on"


# -- writer 1: the poller OOOI block (_check_flight_airplanes_live) ------------

@pytest.fixture
def oooi_rig(monkeypatch):
    hits, writes = [], []
    ac = {"hex": "a1b2c3", "r": "Q3QH2", "alt_baro": "ground", "gs": 3.0, "lat": 38.85, "lon": -77.04,
          "squawk": "1200", "dst": ""}
    monkeypatch.setattr(wl, "resolve_flight_identity", lambda entry, ident, source=None: dict(ac))
    monkeypatch.setattr(wl, "watchlist_event_hit", lambda eid, s, d, priority=3: hits.append((s, d, priority)))
    monkeypatch.setattr(pm, "_acars_reason_context", lambda *a, **k: "ACARS: none")
    monkeypatch.setattr(pm, "_acars_phase", lambda *a, **k: ("in", {"label": "IN", "_source": "JUMPSEAT"}))
    monkeypatch.setattr(db, "update_watchlist_oooi_phase",
                        lambda *a, **k: pytest.fail("the lock-less phase writer must not be used"))
    return hits, writes, monkeypatch


def test_oooi_block_writes_through_the_gate_and_fires_only_when_accepted(oooi_rig):
    hits, writes, mp = oooi_rig
    mp.setattr(db, "update_watchlist_oooi_phase_authoritative",
               lambda eid, ph, source, updated_at: writes.append((ph, source)) or True)
    assert pm._check_flight_airplanes_live(_entry(oooi_phase="on"), "UAL123") is True
    assert writes == [("in", "acars")]
    assert len(hits) == 1 and hits[0][1]["watchlist_trigger"] == "oooi_in" and hits[0][1]["source"] == "acars"


def test_oooi_block_holds_and_stays_silent_when_the_gate_rejects(oooi_rig):
    hits, writes, mp = oooi_rig
    mp.setattr(db, "update_watchlist_oooi_phase_authoritative",
               lambda eid, ph, source, updated_at: writes.append((ph, source)) or False)
    assert pm._check_flight_airplanes_live(_entry(oooi_phase="on"), "UAL123") is True
    assert writes == [("in", "acars")]
    assert hits == []                       # no push for a phase the gate refused


def test_oooi_block_names_fdps_when_fdps_confirmed_off(oooi_rig):
    hits, writes, mp = oooi_rig
    mp.setattr(pm, "_acars_phase", lambda *a, **k: None)
    mp.setattr(pm, "_fdps_confirms_off", lambda *a, **k: True)
    mp.setattr(wl, "resolve_flight_identity",
               lambda entry, ident, source=None: {"hex": "a1b2c3", "r": "Q3QH2", "alt_baro": 4000,
                                                   "gs": 220.0, "lat": 38.9, "lon": -77.1, "squawk": "", "dst": ""})
    mp.setattr(db, "update_watchlist_oooi_phase_authoritative",
               lambda eid, ph, source, updated_at: writes.append((ph, source)) or True)
    assert pm._check_flight_airplanes_live(_entry(oooi_phase="out"), "UAL123") is True
    assert writes == [("off", "fdps")]
    assert hits[0][1]["source"] == "fdps"   # used to say "adsb" for an FDPS-confirmed OFF


# -- writer 2: schedule inference never asserts IN ----------------------------

@pytest.fixture
def infer_rig(monkeypatch):
    hits, history = [], []
    monkeypatch.setattr(wl, "watchlist_event_hit", lambda eid, s, d, priority=3: hits.append((s, d, priority)))
    monkeypatch.setattr(pm, "_acars_reason_context", lambda *a, **k: "ACARS: none")
    monkeypatch.setattr(pm, "_acars_phase", lambda *a, **k: ("in", {"label": "IN"}))
    monkeypatch.setattr(db, "get_watchlist_history", lambda entry_id=None, limit=50: list(history))
    monkeypatch.setattr(db, "update_watchlist_oooi_phase",
                        lambda *a, **k: pytest.fail("schedule inference must not write the OOOI phase"))
    monkeypatch.setattr(db, "update_watchlist_oooi_phase_authoritative",
                        lambda *a, **k: pytest.fail("schedule inference must not assert a phase, gated or not"))
    return hits, history


def test_schedule_inference_records_inferred_in_without_asserting(infer_rig):
    hits, _ = infer_rig
    past = _iso(datetime.now(timezone.utc) - timedelta(hours=1))
    pm._check_flight_schedule_inference(_entry(oooi_phase="off", scheduled_arrival=past), "UAL123")
    assert len(hits) == 1
    summary, detail, prio = hits[0]
    assert detail["watchlist_trigger"] == "oooi_in_inferred"
    assert detail["inferred_phase"] == "in" and detail["asserted"] is False
    assert detail["source"] == "schedule_inference"
    assert "not asserted" in summary and prio == 4


def test_schedule_inference_records_once_per_entry(infer_rig):
    hits, history = infer_rig
    history.append({"event_type": "oooi_in_inferred"})
    past = _iso(datetime.now(timezone.utc) - timedelta(hours=1))
    pm._check_flight_schedule_inference(_entry(oooi_phase="off", scheduled_arrival=past), "UAL123")
    assert hits == []


# -- writer 3: sweep-time ACARS phase ------------------------------------------

def test_sweep_time_acars_phase_goes_through_the_gate(monkeypatch):
    db.init_db_all()
    e = _entry(scheduled_arrival=_iso(datetime.now(timezone.utc) - timedelta(hours=2)))
    db.upsert_watchlist_entry(e)
    writes = []
    monkeypatch.setattr(wl, "_acars_phase", lambda *a, **k: ("off", {"_source": "JUMPSEAT"}))
    monkeypatch.setattr(db, "update_watchlist_oooi_phase",
                        lambda *a, **k: pytest.fail("the lock-less phase writer must not be used"))
    monkeypatch.setattr(db, "update_watchlist_oooi_phase_authoritative",
                        lambda eid, ph, source, updated_at: writes.append((eid, ph, source)) or True)
    wl.sweep_landed_flights()
    assert writes == [(e["id"], "off", "acars")]
    assert [x["id"] for x in db.get_watchlist_entries(entry_type="flight")] == [e["id"]]   # retained


# -- bug once, lint forever ----------------------------------------------------

def test_no_caller_uses_the_plain_phase_writer():
    offenders = []
    for f in SRC.rglob("*.py"):
        if f.name == "db.py" and f.parent.name == "common":
            continue
        for n, line in enumerate(f.read_text().splitlines(), 1):
            if "update_watchlist_oooi_phase(" in line and not line.lstrip().startswith("#"):
                offenders.append(f"{f.relative_to(SRC)}:{n}")
    assert offenders == [], offenders
