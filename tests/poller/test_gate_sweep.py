"""2026-10-18 (operator, gate batch): the poller's gate/baggage step.

  - FIDS writes evidence only; the push comes from the resolved assignment, with
    no FIDS status word or time, and only when the value is known;
  - at a watch's ORIGIN the departures board is read (departure gate only);
  - an enrichment error never reaches OOOI or the rest of the sweep;
  - the dead FIDS-to-OOOI code is gone and any source named *fids* is denied.

Isolated SQLite, captured pushes, mocked MWAA HTTP. Synthetic flights only."""
from __future__ import annotations

import calendar
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import common.db as db
from common import airport_fids as af
from common import flight_instance as fi
from common import gate_assignments as ga

SRC = (Path(__file__).resolve().parents[2] / "src/poller/main.py").read_text()
ET = ZoneInfo("America/New_York")


def _iso(e):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(e))


def _et(e):
    return datetime.fromtimestamp(e, tz=timezone.utc).astimezone(ET).strftime("%Y-%m-%d %H:%M:%S")


class _Resp:
    status_code = 200

    def __init__(self, d):
        self._d = d

    def raise_for_status(self):
        pass

    def json(self):
        return self._d


@pytest.fixture
def sweep(monkeypatch):
    db.init_db_all()
    fi.clear_alias_cache()
    monkeypatch.setattr(db, "faa_ladd_listed_batch", lambda ids: set())
    monkeypatch.setattr(db, "faa_ladd_hex_batch", lambda hx: set())
    monkeypatch.delenv("FLIGHTAWARE_API_KEY", raising=False)
    import shared.watchlist as wl
    hits = []
    monkeypatch.setattr(wl, "watchlist_event_hit",
                        lambda eid, summary, detail, priority=3: hits.append((summary, detail)))
    af._cache.clear(); af._last_fail.clear(); af._last_error.clear()
    import poller.main as pm
    yield pm, hits, monkeypatch
    af._cache.clear(); af._last_fail.clear(); af._last_error.clear()
    fi.clear_alias_cache()


def _leg(dep, arr, origin="KBOS", dest="KDCA", cs="UAL991"):
    with db.conn() as c:
        c.execute("INSERT INTO flight_ooooi_times (gufi, callsign, airline, flight_num, origin, destination, "
                  "original_departure, original_arrival, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                  (f"G-{cs}-{int(dep)}", cs, cs[:3], cs[3:], origin, dest, _iso(dep), _iso(arr), time.time()))


def _fids(monkeypatch, payload_fn):
    monkeypatch.setattr(af.requests, "get", lambda url, headers=None, timeout=None: _Resp(payload_fn(url)))


def test_fids_arrival_gate_pushes_once_without_status_or_time(sweep):
    pm, hits, mp = sweep
    dep = time.time() - 3600
    arr = time.time() + 1800
    _leg(dep, arr)
    _fids(mp, lambda url: {"arrivals": [{"IATA": "UA", "flightnumber": "991", "dep_airport_code": "BOS",
                                         "status": "Landed", "gate": "C35", "arr_terminal": "2",
                                         "baggage": "6", "publishedTime": _et(arr)}], "departures": []})
    entry = {"id": "wl-gs-1", "identifier": "UAL991", "origin": "BOS", "destination": "DCA",
             "scheduled_departure": _iso(dep)}
    pm._check_flight_gates(entry, "UAL991")
    pm._check_flight_gates(entry, "UAL991")              # unchanged: no second push
    assert len(hits) == 2                                 # arrival gate + baggage claim, once each
    slots = {d["slot"] for _s, d in hits}
    assert slots == {"arrival:gate", "arrival:baggage"}
    for summary, detail in hits:
        assert "Landed" not in summary and "status" not in detail and "scheduled" not in detail
        assert detail["assignment_status"] == "provisional"
        assert "[MWAA FIDS]" in summary and "(provisional)" in summary
    gate = next(s for s, d in hits if d["slot"] == "arrival:gate")
    assert "C35 (terminal 2) at DCA" in gate


def test_origin_side_reads_the_departures_board(sweep):
    pm, hits, mp = sweep
    dep = time.time() + 1800
    _leg(dep, dep + 5000, origin="KDCA", dest="KBOS", cs="UAL992")
    # the ARRIVALS board at DCA also carries UA992 (the inbound leg, gate E1): never used here
    _fids(mp, lambda url: {
        "arrivals": [{"IATA": "UA", "flightnumber": "992", "dep_airport_code": "BOS", "gate": "E1",
                      "publishedTime": _et(dep - 3600)}],
        "departures": [{"IATA": "UA", "flightnumber": "992", "airportcode": "BOS", "gate": "B20",
                        "dep_terminal": "1", "publishedTime": _et(dep)}]})
    entry = {"id": "wl-gs-2", "identifier": "UAL992", "origin": "DCA", "destination": "BOS",
             "scheduled_departure": _iso(dep)}
    pm._check_flight_gates(entry, "UAL992")
    assert [(d["slot"], d["value"]) for _s, d in hits] == [("departure:gate", "B20")]


def test_fids_absent_or_empty_no_push_no_error(sweep):
    pm, hits, mp = sweep
    dep = time.time() - 3600
    _leg(dep, time.time() + 1800, cs="UAL993")
    import requests

    def down(url, headers=None, timeout=None):
        raise requests.ConnectionError("blackhole")
    mp.setattr(af.requests, "get", down)
    entry = {"id": "wl-gs-3", "identifier": "UAL993", "origin": "BOS", "destination": "DCA",
             "scheduled_departure": _iso(dep)}
    pm._check_flight_gates(entry, "UAL993")
    af._last_fail.clear()
    _fids(mp, lambda url: {"arrivals": [], "departures": []})
    pm._check_flight_gates(entry, "UAL993")
    assert hits == []


def test_enrichment_error_never_reaches_oooi_or_the_rest_of_the_sweep(sweep):
    pm, hits, mp = sweep
    calls = []
    entry = {"id": "wl-gs-4", "identifier": "UAL994", "origin": "BOS", "destination": "DCA", "entry_type": "flight"}
    import shared.watchlist as wl
    mp.setattr(wl, "get_active_entries", lambda entry_type=None, cached=False: [entry])
    mp.setattr(wl, "sweep_landed_flights", lambda: 0)
    mp.setattr(pm, "_check_flight_airplanes_live", lambda e, i: calls.append("oooi") or True)
    mp.setattr(pm, "_check_flight_fdps_cache", lambda e, i: calls.append("fdps"))
    mp.setattr(pm, "_check_flight_inbound", lambda e, i: calls.append("inbound"))
    mp.setattr(pm, "_check_flight_tdes_tail", lambda e, i: calls.append("tdes_tail"))

    def boom(*a, **k):
        raise RuntimeError("gate store down")
    mp.setattr(ga, "watch_leg_key", boom)
    pm.WatchlistSweep._do_flight_sweep()
    mp.setattr(ga, "watch_leg_key", lambda *a, **k: ga.make_key("UAL", "994", "BOS", "DCA", "2026-10-17"))
    _fids(mp, lambda url: {"arrivals": [{"IATA": "UA", "flightnumber": "994", "dep_airport_code": "BOS",
                                         "gate": "C1", "publishedTime": "2026-10-17 10:00:00"}],
                           "departures": []})
    mp.setattr(ga, "record", boom)
    mp.setattr(ga, "push_watch_changes", boom)
    pm.WatchlistSweep._do_flight_sweep()
    assert calls == ["oooi", "fdps", "inbound", "tdes_tail"] * 2


def test_dead_fids_oooi_code_is_gone():
    for name in ("def _fids_shows_landed", "_FIDS_STATUS_TO_PHASE = {", 'confirm_src == "fids"',
                 '{summary} — confirmed via FIDS', '"fids_update"'):
        assert name not in SRC, name
    pusher = (Path(__file__).resolve().parents[2] / "src/pusher/main.py").read_text()
    assert 'return "landed_fids"' not in pusher and 'return "landed_poller"' in pusher


def test_any_fids_named_source_is_denied_oooi():
    for src in ("fids", "swim_fids", "mwaa_fids", "FIDS_DESTINATION"):
        ok, note = db._oooi_authority_check("on", src, {})
        assert not ok and "FIDS" in note, src
    assert "swim_fids" not in db._OOOI_SOURCE_TIER
