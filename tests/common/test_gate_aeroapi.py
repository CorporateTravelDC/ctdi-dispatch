"""2026-10-18 (operator, gate batch): AeroAPI is the gate/baggage FALLBACK ONLY --
called only when no SWIM/ACARS value exists for a watched leg's arrival gate (or a
non-DC baggage claim), at most twice per leg (~45 min before the ETA, after ON),
under a daily cap, every call ledgered before it is made.

The HTTP session is a fake: no AeroAPI call ever leaves the box, and the key is a
dummy string that is asserted never to appear in the ledger."""
from __future__ import annotations

import calendar
import time

import pytest

import common.db as db
from common import flight_instance as fi
from common import gate_aeroapi as gaa
from common import gate_assignments as ga


def ep(iso):
    return float(calendar.timegm(time.strptime(iso, "%Y-%m-%dT%H:%M:%SZ")))


ETA = ep("2026-10-17T15:10:00Z")
KEY = ga.make_key("UAL", "999", "ORD", "BOS", "2026-10-17")
KEY_DC = ga.make_key("UAL", "998", "ORD", "IAD", "2026-10-17")
DUMMY = "dummy-aeroapi-key-for-tests"


class _Resp:
    status_code = 200

    def __init__(self, data):
        self._d = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._d


class FakeSession:
    def __init__(self, dest="BOS", num="999"):
        self.calls = []
        self.dest, self.num = dest, num

    def get(self, url, headers=None, params=None, timeout=None):
        self.calls.append((url, dict(headers or {}), dict(params or {})))
        return _Resp({"flights": [{"origin": {"code_iata": "ORD"}, "destination": {"code_iata": self.dest},
                                   "scheduled_out": "2026-10-17T12:30:00Z", "gate_destination": "B7",
                                   "terminal_destination": "B", "baggage_claim": "5",
                                   "status": "En Route", "estimated_in": "2026-10-17T15:05:00Z"}]})


@pytest.fixture
def aero(monkeypatch):
    db.init_db_all()
    fi.clear_alias_cache()
    monkeypatch.setattr(db, "faa_ladd_listed_batch", lambda ids: set())
    monkeypatch.setattr(db, "faa_ladd_hex_batch", lambda hx: set())
    monkeypatch.setenv("FLIGHTAWARE_API_KEY", DUMMY)
    monkeypatch.delenv("AEROAPI_GATE_DAILY_CAP", raising=False)
    yield


def _ledger():
    with db.conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM aeroapi_gate_calls ORDER BY id").fetchall()]


def test_no_key_no_call(aero, monkeypatch):
    monkeypatch.delenv("FLIGHTAWARE_API_KEY")
    s = FakeSession()
    assert gaa.maybe_fetch(KEY, eta=ETA, phase=None, phase_at=None, now=ETA - 40 * 60, session=s)["reason"] == "no_key"
    assert s.calls == []


def test_two_calls_per_leg_at_most_pre_eta_then_post_on(aero):
    s = FakeSession()
    # too early: nothing
    assert gaa.maybe_fetch(KEY, eta=ETA, phase=None, phase_at=None, now=ETA - 3 * 3600, session=s)["reason"] == "not_due"
    r = gaa.maybe_fetch(KEY, eta=ETA, phase=None, phase_at=None, now=ETA - 40 * 60, session=s)
    assert r["called"] and r["stored"] == 3          # gate + terminal + baggage (BOS is not DC)
    # the same window again: no second pre-ETA call (and the values exist anyway -- AeroAPI
    # is below SWIM/ACARS, so its own values do not stop a later post-ON refresh)
    assert gaa.maybe_fetch(KEY, eta=ETA, phase=None, phase_at=None, now=ETA - 30 * 60, session=s)["reason"] == "not_due"
    r = gaa.maybe_fetch(KEY, eta=ETA, phase="on", phase_at=ETA, now=ETA + 60, session=s)
    assert r["called"]
    assert gaa.maybe_fetch(KEY, eta=ETA, phase="in", phase_at=ETA, now=ETA + 600, session=s)["reason"] == "not_due"
    assert len(s.calls) == 2
    assert [row["purpose"] for row in _ledger()] == ["pre_eta", "post_on"]
    assert all(DUMMY not in str(row) for row in _ledger())
    url, headers, params = s.calls[0]
    assert headers["x-apikey"] == DUMMY and url.endswith("/flights/UAL999")


def test_fallback_only_a_higher_source_suppresses_the_call(aero):
    ga.record([ga.Observation(key=KEY, side="arrival", field="gate", value_raw="C9", source="acars_airframes",
                              observed_at="2026-10-17T14:00:00Z", leg_match="exact",
                              evidence_type="assignment_msg", confidence=0.9, parser_version="t",
                              airport="BOS"),
               ga.Observation(key=KEY, side="arrival", field="baggage", value_raw="2", source="acars_airframes",
                              observed_at="2026-10-17T14:00:00Z", leg_match="exact",
                              evidence_type="assignment_msg", confidence=0.9, parser_version="t",
                              airport="BOS")], now=ETA - 50 * 60)
    s = FakeSession()
    r = gaa.maybe_fetch(KEY, eta=ETA, phase=None, phase_at=None, now=ETA - 40 * 60, session=s)
    assert r["reason"] == "higher_source_has_value" and s.calls == []


def test_fids_does_not_suppress_aeroapi_but_dc_baggage_never_comes_from_it(aero):
    ga.record([ga.Observation(key=KEY_DC, side="arrival", field="baggage", value_raw="7", source="mwaa_fids",
                              observed_at="2026-10-17T14:00:00Z", leg_match="route_watch",
                              evidence_type="display", confidence=0.7, parser_version="t", airport="IAD")],
              now=ETA - 41 * 60)
    s = FakeSession(dest="IAD", num="998")
    r = gaa.maybe_fetch(KEY_DC, eta=ETA, phase=None, phase_at=None, now=ETA - 40 * 60, session=s)
    assert r["called"] and r["stored"] == 2          # gate + terminal only: FIDS is the DC carousel source
    v = ga.leg_assignments(KEY_DC, ETA - 40 * 60)
    assert v["arrival_gate"]["source_id"] == "aeroapi"
    assert v["arrival_baggage"]["source_id"] == "mwaa_fids"


def test_daily_cap(aero, monkeypatch):
    monkeypatch.setenv("AEROAPI_GATE_DAILY_CAP", "1")
    s = FakeSession()
    assert gaa.maybe_fetch(KEY, eta=ETA, phase=None, phase_at=None, now=ETA - 40 * 60, session=s)["called"]
    other = ga.make_key("UAL", "997", "ORD", "BOS", "2026-10-17")
    r = gaa.maybe_fetch(other, eta=ETA, phase=None, phase_at=None, now=ETA - 40 * 60, session=FakeSession(num="997"))
    assert r == {"called": False, "stored": 0, "reason": "daily_cap"}


def test_call_failure_is_ledgered_and_spends_the_slot(aero):
    class Down(FakeSession):
        def get(self, *a, **k):
            self.calls.append(a)
            raise ConnectionError("down")
    s = Down()
    assert gaa.maybe_fetch(KEY, eta=ETA, phase=None, phase_at=None, now=ETA - 40 * 60, session=s)["reason"] == "error"
    assert _ledger()[0]["outcome"].startswith("error")
    assert gaa.maybe_fetch(KEY, eta=ETA, phase=None, phase_at=None, now=ETA - 35 * 60, session=s)["reason"] == "not_due"
    assert len(s.calls) == 1


def test_ladd_listed_leg_is_never_queried(aero, monkeypatch):
    monkeypatch.setattr(db, "faa_ladd_listed_batch", lambda ids: set(ids))
    s = FakeSession()
    assert gaa.maybe_fetch(KEY, eta=ETA, phase=None, phase_at=None, now=ETA - 40 * 60, session=s)["reason"] == "ladd"
    assert s.calls == [] and _ledger() == []
