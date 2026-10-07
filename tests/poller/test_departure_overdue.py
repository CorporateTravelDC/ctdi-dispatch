"""
tests/poller/test_departure_overdue.py -- 2026-10-06: past scheduled departure
+90 min with nothing observed, the poller no longer asserts "OFF (schedule
inferred)". A real source may confirm OFF through the authority gate; otherwise
one "departure overdue -- no OFF asserted" push and the phase is untouched.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import poller.main as pm  # noqa: E402
import shared.watchlist as wl  # noqa: E402
from common import db  # noqa: E402

ENTRY = {"id": "wl-t", "identifier": "JZA825", "origin": "CYUL", "destination": "KDCA",
         "registration": "Q8UH4", "hex_id": "d6e017", "oooi_phase": "pre_departure",
         "scheduled_departure": "2026-10-06T00:00:00Z"}
NOW = datetime(2026, 10, 6, 2, 0, tzinfo=timezone.utc)


@pytest.fixture
def rig(monkeypatch):
    hits, writes = [], []
    monkeypatch.setattr(wl, "watchlist_event_hit", lambda eid, s, d, priority=3: hits.append((s, d, priority)))
    monkeypatch.setattr(pm, "_acars_reason_context", lambda *a, **k: "ACARS: none")
    monkeypatch.setattr(pm, "_acars_phase", lambda *a, **k: None)
    monkeypatch.setattr(pm, "_fdps_confirms_off", lambda *a, **k: False)
    monkeypatch.setattr(wl, "tfms_departure_state", lambda *a, **k: None)
    monkeypatch.setattr(db, "update_watchlist_oooi_phase_authoritative",
                        lambda eid, ph, source, updated_at: writes.append((ph, source)) or True)
    monkeypatch.setattr(db, "update_watchlist_oooi_phase",
                        lambda *a, **k: pytest.fail("the unguarded phase writer must not be used"))
    return hits, writes, monkeypatch


def test_nothing_observed_asserts_nothing(rig):
    hits, writes, _ = rig
    assert pm._departure_overdue_check(ENTRY, "JZA825", ENTRY["scheduled_departure"], NOW) == "overdue"
    assert writes == []
    assert "departure overdue" in hits[0][0] and "no OFF asserted" in hits[0][0]
    assert hits[0][1]["watchlist_trigger"] == "departure_overdue"


@pytest.mark.parametrize("which,expect", [("acars", "acars"), ("fdps", "fdps"), ("tfms", "tfms_airline")])
def test_a_real_source_confirms_off_through_the_gate(rig, which, expect):
    hits, writes, mp = rig
    if which == "acars":
        mp.setattr(pm, "_acars_phase", lambda *a, **k: ("off", {"label": "QP"}))
    elif which == "fdps":
        mp.setattr(pm, "_fdps_confirms_off", lambda *a, **k: True)
    else:
        mp.setattr(wl, "tfms_departure_state", lambda *a, **k: ("off", "2026-10-06T00:25:00Z"))
    assert pm._departure_overdue_check(ENTRY, "JZA825", ENTRY["scheduled_departure"], NOW) == expect
    assert writes == [("off", expect)]
    assert hits[0][1]["watchlist_trigger"] == "oooi_off" and "not seen by ADS-B" in hits[0][0]


def test_inferred_off_text_is_gone():
    src = (Path(__file__).resolve().parents[2] / "src" / "poller" / "main.py").read_text()
    assert "schedule inferred, ADS-B not seen" not in src and '"oooi_off_inferred"' not in src
