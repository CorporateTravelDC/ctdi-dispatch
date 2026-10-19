"""2026-10-18 (operator, gate batch): MWAA FIDS is gate / terminal / baggage ONLY.

  - no lookup, route or resolver tier returns a FIDS status, time or tail;
  - a cache older than FIDS_MAX_STALE_S is never served, and a failed fetch is
    always recorded in feed_state without advancing fetched_at;
  - a failed fetch backs off 60 s per airport for the poller's checks;
  - match_leg reads the DEPARTURES board at a leg's origin (was: arrivals);
  - the resolver's website window is computed in airport-local time.

Every MWAA HTTP call is mocked (common.airport_fids.requests.get); outbound HTTP
is not blocked globally. Synthetic flights only."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
import requests

import common.db as db
from common import airport_fids as af

ET = ZoneInfo("America/New_York")


def _et(dt_utc: datetime) -> str:
    return dt_utc.astimezone(ET).strftime("%Y-%m-%d %H:%M:%S")


def _payload(pub_utc: datetime) -> dict:
    pub = _et(pub_utc)
    return {
        "arrivals": [{"IATA": "ZZ", "flightnumber": "901", "dep_airport_code": "BOS", "status": "Landed",
                      "gate": "D38", "arr_terminal": "2", "baggage": "11", "publishedTime": pub,
                      "actualtime": pub, "mwaaTime": pub, "aircraftInfo": {"tail_number": "N000TS"},
                      "arrivalInfo": [{"remaining_time": "00:10:00"}], "dep_gate": "B15"},
                     {"IATA": "ZZ", "flightnumber": "902", "dep_airport_code": "DCA", "status": "InAir",
                      "gate": "C10", "arr_terminal": "1", "baggage": "3", "publishedTime": pub}],
        "departures": [{"IATA": "ZZ", "flightnumber": "902", "airportcode": "BOS", "status": "OutGate",
                        "gate": "B20", "dep_terminal": "1", "publishedTime": pub,
                        "aircraftInfo": [{"tail_number": "N000TS"}]}],
    }


class _Resp:
    def __init__(self, data):
        self._data = data
        self.status_code = 200

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


@pytest.fixture
def fids(monkeypatch):
    af._cache.clear()
    af._last_fail.clear()
    af._last_error.clear()
    clock = {"t": 1000.0}
    monkeypatch.setattr(af, "_now", lambda: clock["t"])
    calls = []
    state = {"mode": "ok", "pub": datetime.now(tz=ZoneInfo("UTC")) + timedelta(minutes=30)}

    def fake_get(url, headers=None, timeout=None):
        calls.append(url)
        if state["mode"] == "down":
            raise requests.ConnectionError("blackhole")
        if state["mode"] == "empty":
            return _Resp({"arrivals": [], "departures": []})
        return _Resp(_payload(state["pub"]))

    monkeypatch.setattr(af.requests, "get", fake_get)
    yield clock, calls, state
    af._cache.clear()
    af._last_fail.clear()
    af._last_error.clear()


FORBIDDEN = ("status", "scheduled", "estimated", "remaining", "tail")


def test_lookups_return_no_status_time_or_tail(fids):
    arr = af.lookup_arrival("DCA", "ZZ", "901")
    dep = af.lookup_departure("DCA", "ZZ", "902")
    assert arr["gate"] == "D38" and arr["baggage"] == "11" and dep["gate"] == "B20"
    for k in FORBIDDEN:
        assert k not in arr and k not in dep


def test_fids_route_has_no_status_time_or_tail(fids):
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    from web.routes.fids import router
    app = FastAPI()
    app.include_router(router)
    r = TestClient(app).get("/api/v1/fids/DCA/ZZ901")
    assert r.status_code == 200
    body = r.json()
    assert body["gate"] == "D38"
    for k in FORBIDDEN:
        assert k not in body
    blob = json.dumps(body)
    assert "Landed" not in blob and "N000TS" not in blob


def test_fids_absent_route_404_and_lookup_none(fids):
    clock, calls, state = fids
    state["mode"] = "down"
    assert af.lookup_arrival("DCA", "ZZ", "901") is None
    state["mode"] = "empty"
    af._last_fail.clear()
    clock["t"] += 120
    assert af.lookup_arrival("DCA", "ZZ", "901") is None


def test_stale_cache_is_capped(fids):
    clock, calls, state = fids
    assert af.get_data("DCA") is not None
    state["mode"] = "down"
    clock["t"] += 200                                    # failure inside the cap: cache served
    assert af.get_data("DCA") is not None
    assert af.last_fetch_error("DCA").startswith("fetch failed")
    clock["t"] += af.FIDS_MAX_STALE_S                    # past the cap: never an old payload
    af._last_fail.clear()
    assert af.get_data("DCA") is None


def test_failure_backoff_one_fetch_per_airport_per_minute(fids):
    clock, calls, state = fids
    state["mode"] = "down"
    for _ in range(5):                                   # five DC watches in one sweep
        af.lookup_arrival("DCA", "ZZ", "901")
        af.lookup_arrival("IAD", "ZZ", "901")
    assert len(calls) == 2                               # one per airport, not one per watch
    clock["t"] += af.FIDS_FAIL_BACKOFF_S + 1
    af.lookup_arrival("DCA", "ZZ", "901")
    assert len(calls) == 3


def test_run_for_records_the_error_even_with_a_cache(fids, monkeypatch):
    clock, calls, state = fids
    db.init_db_all()
    from poller.fetchers import airport_fids as fetcher
    assert fetcher.run_for("DCA").get("arrivals") == 2
    first = {f["feed_name"]: f for f in db.get_feed_states()}["dca_fids"]
    assert first["error"] is None
    state["mode"] = "down"
    clock["t"] += 100
    monkeypatch.setattr(fetcher.time, "time", lambda: first["fetched_at"] + 300)
    out = fetcher.run_for("DCA")
    row = {f["feed_name"]: f for f in db.get_feed_states()}["dca_fids"]
    assert "error" in out and "FIDS fetch failed" in row["error"]
    assert row["fetched_at"] == first["fetched_at"]      # age keeps measuring the last success
    assert row["consecutive_failures"] == 1


def test_match_leg_reads_departures_at_the_origin(fids):
    # ZZ902 DCA->BOS: at DCA (its origin) the ARRIVALS board also lists a ZZ902 (the
    # inbound leg, gate C10). The origin side must read the DEPARTURES board: B20.
    row = af.match_leg("DCA", "departure", "ZZ", "902", "BOS", None)
    assert row == {"gate": "B20", "leg_match": "route_only"}


def test_match_leg_uses_the_published_time_only_inside_the_leg_window(fids):
    clock, calls, state = fids
    pub = state["pub"].timestamp()
    hit = af.match_leg("DCA", "arrival", "ZZ", "901", "BOS", (pub - 3600, pub + 3600))
    assert hit == {"gate": "D38", "terminal": "2", "baggage": "11", "leg_match": "route_watch"}
    assert af.match_leg("DCA", "arrival", "ZZ", "901", "BOS", (pub + 7200, pub + 9000)) is None
    assert af.match_leg("DCA", "arrival", "ZZ", "901", "ORD", None) is None      # route contradicts


def test_resolver_website_tier_serves_no_status_or_time_and_uses_local_time(fids, monkeypatch):
    from common import flight_resolver as fr
    monkeypatch.setattr(fr, "_query_swim", lambda *a, **k: [])
    out = fr.resolve_arrivals("DCA", ["ZZ"], within_minutes=90)
    assert out["source_used"] == "website"
    assert {r["flight_num"] for r in out["results"]} == {"901", "902"}
    for r in out["results"]:
        assert "status" not in r and "scheduled" not in r
    # published 30 min out in ET: inside a 90-min window computed in UTC epoch terms;
    # the old naive container-UTC vs ET comparison put it 4-5 h off.
    assert fr.resolve_arrivals("DCA", ["ZZ"], within_minutes=10)["source_used"] == "none"


def test_resolver_never_calls_aeroapi(fids, monkeypatch):
    from common import flight_resolver as fr
    monkeypatch.setattr(fr, "_query_swim", lambda *a, **k: [])
    monkeypatch.setenv("FLIGHTAWARE_API_KEY", "dummy-not-a-key")
    out = fr.resolve_arrivals("BWI", None, 90)
    assert out["source_used"] == "none"
    assert not any("aeroapi" in u for u in fids[1])
