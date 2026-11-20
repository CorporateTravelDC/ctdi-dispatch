"""2026-11-20: UAL376 KAUS-KIAD, 2026-11-19. TFMS FlightModify carried airline OUT 23:18Z / OFF 23:33Z
with flightStatus PLANNED; both times passed while the aircraft was still at the stand. A passed airline
time is a milestone only once TFMS's own status says the flight departed (ACTIVE / COMPLETED).
SYNTHETIC values; the element shape is the one tfms_parser._handle_flight_times documents."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import pytest

from common import db
from ingest.parsers import tfms_parser as tp


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _msg(status: str) -> ET.Element:
    now = datetime.now(timezone.utc)
    return ET.fromstring(
        "<fltdMessage><ncsmFlightModify><airlineData>"
        f"<flightStatusAndSpec><flightStatus>{status}</flightStatus></flightStatusAndSpec>"
        f'<flightTimeData airlineOutTime="{_iso(now - timedelta(minutes=20))}" '
        f'airlineOffTime="{_iso(now - timedelta(minutes=5))}" '
        f'airlineOnTime="{_iso(now + timedelta(hours=2))}"/>'
        "</airlineData></ncsmFlightModify></fltdMessage>")


@pytest.fixture
def rig(monkeypatch):
    phases, hits = [], []
    entry = {"id": "wl-flight-qqq376-test", "identifier": "QQQ376", "hex_id": "000001", "origin": "KAUS",
             "destination": "KIAD"}
    monkeypatch.setattr(tp, "_qualified_aircraft_id", lambda m: ("QQQ376", "SYN-GUFI", "KAUS", "KIAD"))
    monkeypatch.setattr(tp, "_leg_context", lambda m, o, d: {})
    monkeypatch.setattr(tp, "_match_watchlist_flight", lambda cs, **kw: entry)
    monkeypatch.setattr(tp, "_fire_tfms_watchlist_hit", lambda e, s, d, priority=3: hits.append(d))
    monkeypatch.setattr(db, "upsert_flight_ooooi", lambda **kw: None)
    monkeypatch.setattr(db, "update_watchlist_oooi_phase_authoritative",
                        lambda eid, phase, source=None, updated_at=None: phases.append(phase) or True)
    import shared.watchlist as wl
    monkeypatch.setattr(wl, "extend_auto_remove_for_delay", lambda *a, **k: None)
    return phases, hits


def test_planned_row_with_passed_out_and_off_asserts_no_milestone(rig):
    phases, hits = rig
    tp._handle_flight_times(_msg("PLANNED"))
    assert phases == []
    assert hits and hits[0]["flight_status"] == "PLANNED"      # the times still reach the event log


def test_active_row_asserts_the_latest_passed_milestone(rig):
    phases, _ = rig
    tp._handle_flight_times(_msg("ACTIVE"))
    assert phases == ["off"]                                   # ON is still in the future
