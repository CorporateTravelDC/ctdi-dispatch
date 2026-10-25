"""2026-10-24 (map rework, wave 1): the runner's /api/map/* -- local receiver +
SWIM merged on ONE map. Pure merge rules, scope-bounds math, the DEMO_MODE
synthetic-only guarantee for all three routes, and the dispatch-unreachable
fallback. No network, no DB: every fetch is patched. Identifiers are synthetic
(hex abc123/abc124, callsigns TEST123/TEST456, points near 0.5,0.5)."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from fastapi import HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from starlette.requests import Request  # noqa: E402

from runner import main as rm  # noqa: E402


def _request(headers: dict | None = None, client=("127.0.0.1", 12345)) -> Request:
    encoded = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request({"type": "http", "headers": encoded, "method": "GET",
                    "path": "/api/map/aircraft", "client": client, "query_string": b""})


def _boom(*_a, **_kw):
    raise AssertionError("must never touch a real-time source in DEMO_MODE")


def _local(hex_="abc123", flight="TEST123 ", r="N000AA", lat=0.50, lon=0.50, **kw):
    ac = {"hex": hex_, "flight": flight, "r": r, "t": "A320", "lat": lat, "lon": lon,
          "alt_baro": 31000, "gs": 440.0, "track": 90.0, "seen_pos": 1.5}
    ac.update(kw)
    return ac


def _swim(hex_="abc123", callsign="TEST123", registration="N000AA", lat=0.52, lon=0.52, **kw):
    p = {"flight_id": "gufi-1", "callsign": callsign, "hex": hex_, "registration": registration,
         "lat": lat, "lon": lon, "altitude_ft": 30000, "ground_speed_kt": 430, "track_deg": None,
         "updated_at": 1000.0, "origin": "KBOS", "destination": "KDCA", "aircraft_type": "A321",
         "source": "fdps"}
    p.update(kw)
    return p


# -- merge --------------------------------------------------------------------

def test_merge_matched_by_hex_keeps_local_position_and_takes_swim_route():
    aircraft, counts = rm._merge_map_aircraft([_local()], [_swim()], now=1010.0)
    assert counts == {"matched": 1, "local": 0, "swim": 0, "conflict": 0}
    (ac,) = aircraft
    assert ac["src"] == "matched" and ac["conflict"] is False and ac["conflict_note"] is None
    assert (ac["lat"], ac["lon"]) == (0.50, 0.50)           # LOCAL position, not SWIM's
    assert ac["alt_baro"] == 31000 and ac["gs"] == 440.0      # local kinematics too
    assert (ac["origin"], ac["destination"]) == ("KBOS", "KDCA")
    assert ac["flight"] == "TEST123" and ac["hex"] == "abc123" and ac["r"] == "N000AA"
    assert ac["pos_age_s"] == 1.5


def test_merge_matched_by_callsign_when_swim_has_no_hex():
    local = [_local(flight="test456", r=None)]
    swim = [_swim(hex_=None, callsign="TEST456", registration="N000BB")]
    aircraft, counts = rm._merge_map_aircraft(local, swim, now=1010.0)
    assert counts["matched"] == 1 and counts["swim"] == 0
    assert aircraft[0]["src"] == "matched"
    assert aircraft[0]["r"] == "N000BB"                      # SWIM fills the registration
    assert aircraft[0]["hex"] == "abc123"                     # local hex kept


def test_merge_hex_wins_over_callsign():
    """Same hex with a different callsign on the two sides is ONE aircraft
    (matched + conflict), never a local and a separate SWIM entry."""
    local = [_local(hex_="abc123", flight="TEST123")]
    swim = [_swim(hex_="abc123", callsign="TEST999")]
    aircraft, counts = rm._merge_map_aircraft(local, swim, now=1010.0)
    assert counts == {"matched": 1, "local": 0, "swim": 0, "conflict": 1}
    assert aircraft[0]["conflict"] is True
    assert "TEST123" in aircraft[0]["conflict_note"] and "TEST999" in aircraft[0]["conflict_note"]


def test_merge_registration_conflict_shows_both_and_swim_is_authority():
    local = [_local(r="N000AA")]
    swim = [_swim(registration="N000ZZ")]
    aircraft, counts = rm._merge_map_aircraft(local, swim, now=1010.0)
    assert counts["conflict"] == 1
    assert aircraft[0]["r"] == "N000ZZ"
    assert "N000AA" in aircraft[0]["conflict_note"] and "N000ZZ" in aircraft[0]["conflict_note"]


def test_merge_no_conflict_when_one_side_is_blank_or_differs_only_in_case_or_dash():
    local = [_local(flight="  test123", r="n-000aa")]
    swim = [_swim(callsign="TEST123", registration="N000AA")]
    aircraft, counts = rm._merge_map_aircraft(local, swim, now=1010.0)
    assert counts["conflict"] == 0 and aircraft[0]["conflict"] is False
    local = [_local(flight="", r=None)]
    aircraft, counts = rm._merge_map_aircraft(local, swim, now=1010.0)
    assert counts["conflict"] == 0 and aircraft[0]["flight"] == "TEST123"


def test_merge_local_only_and_swim_only_with_ages():
    local = [_local(hex_="abc124", flight="TEST456", lat=0.51, lon=0.51, seen_pos=3.0)]
    swim = [_swim(hex_="abc125", callsign="TEST789", lat=2.0, lon=2.0, updated_at=950.0)]
    aircraft, counts = rm._merge_map_aircraft(local, swim, now=1010.0)
    assert counts == {"matched": 0, "local": 1, "swim": 1, "conflict": 0}
    by_src = {a["src"]: a for a in aircraft}
    assert by_src["local"]["pos_age_s"] == 3.0 and by_src["local"]["origin"] is None
    assert by_src["swim"]["pos_age_s"] == 60.0 and by_src["swim"]["alt_baro"] == 30000
    assert by_src["swim"]["t"] == "A321" and by_src["swim"]["track"] is None


def test_merge_skips_entries_without_a_position():
    aircraft, counts = rm._merge_map_aircraft([_local(lat=None)], [_swim(lon=None)], now=1010.0)
    assert aircraft == [] and counts == {"matched": 0, "local": 0, "swim": 0, "conflict": 0}


# -- scope bounds math ----------------------------------------------------------

def test_circle_bounds_nm_to_degrees():
    b = rm._circle_bounds(0.0, 0.0, 60.0)                     # 60 nm = 1 deg at the equator
    assert b == [[-1.0, -1.0], [1.0, 1.0]]
    b60 = rm._circle_bounds(60.0, 10.0, 60.0)                 # lon widens by 1/cos(60) = 2
    assert b60[0][0] == pytest.approx(59.0) and b60[1][0] == pytest.approx(61.0)
    assert b60[0][1] == pytest.approx(8.0) and b60[1][1] == pytest.approx(12.0)


def test_union_bounds_and_map_bounds_union_receiver_with_swim():
    assert rm._union_bounds([[0, 0], [1, 1]], None, [[-1, 0.5], [0.5, 3]]) == [[-1, 0], [1, 3]]
    assert rm._union_bounds(None) is None
    with patch.object(rm, "DEFAULT_LAT", 0.5), patch.object(rm, "DEFAULT_LON", 0.5), \
         patch.object(rm, "DEFAULT_DIST", 60):
        only_rx = rm._map_bounds(True, None)
        assert only_rx[0][0] == pytest.approx(-0.5) and only_rx[1][0] == pytest.approx(1.5)
        swim = {"center": [3.0, 3.0], "radius_nm": 120.0}
        b = rm._map_bounds(True, swim)
        # lon half-widths carry the 1/cos(lat) factor (lat 0.5 and 3 deg here), hence the tolerance
        assert b[0][0] == pytest.approx(-0.5) and b[0][1] == pytest.approx(-0.5, abs=1e-2)
        assert b[1][0] == pytest.approx(5.0) and b[1][1] == pytest.approx(5.0, abs=1e-2)
        # untrusted: the receiver half of the union is the public placeholder, never DEFAULT_LAT/LON
        ub = rm._map_bounds(False, None)
        assert ub[0][0] == pytest.approx(rm._UNTRUSTED_PLACEHOLDER_LAT - 1.0)


def test_in_bbox_handles_antimeridian():
    assert rm._in_bbox(0.5, 0.5, (0, 0, 1, 1)) and not rm._in_bbox(2, 0.5, (0, 0, 1, 1))
    assert rm._in_bbox(0.5, 179.5, (0, 170, 1, -170)) and not rm._in_bbox(0.5, 0.0, (0, 170, 1, -170))
    assert rm._in_bbox(0.5, 0.5, None)


def test_map_bounds_national_scope_is_the_nas_box_for_trusted_and_untrusted():
    """2026-10-24: SWIM_STORAGE_SCOPE=national -> dispatch answers scope
    "national" with the NAS box; the map extent is that box united with the
    receiver circle (which lies inside it), for trusted and untrusted alike."""
    nas = [[17.5, -179.5], [72.0, -60.0]]
    national = {"swim_enabled": True, "scope": "national", "center": None, "radius_nm": None,
                "airports": ["KDCA"], "bounds": nas, "label": "NAS-wide (FAA SWIM)"}
    with patch.object(rm, "DEFAULT_LAT", 38.9), patch.object(rm, "DEFAULT_LON", -77.4), \
         patch.object(rm, "DEFAULT_DIST", 250):
        assert rm._swim_box(national) == nas
        assert rm._map_bounds(True, national) == nas
        assert rm._map_bounds(False, national) == nas
        # an older dispatch that says "national" without bounds -> the runner's fallback box
        assert rm._swim_box({"scope": "national"}) == rm._NAS_BOUNDS_FALLBACK == nas
        # a receiver outside the box still widens the union (never shrinks the NAS box)
        with patch.object(rm, "DEFAULT_LAT", 13.5), patch.object(rm, "DEFAULT_LON", -100.0):
            b = rm._map_bounds(True, national)
            assert b[0][0] < 17.5 and b[0][1] == -179.5 and b[1] == [72.0, -60.0]
    # regional answers are untouched by the new branch
    assert rm._swim_box({"scope": "regional", "center": [3.0, 3.0], "radius_nm": 60.0}) == \
        rm._circle_bounds(3.0, 3.0, 60.0)
    assert rm._swim_box(None) is None and rm._swim_box({"center": None, "radius_nm": None}) is None


def test_map_scope_route_under_national_scope():
    nas = [[17.5, -179.5], [72.0, -60.0]]
    national = {"swim_enabled": True, "scope": "national", "center": None, "radius_nm": None,
                "airports": ["KDCA"], "bounds": nas, "label": "NAS-wide (FAA SWIM)"}
    with patch.object(rm, "DEMO_MODE", False), \
         patch.object(rm, "_swim_scope", new=AsyncMock(return_value=national)), \
         patch.object(rm, "TAR1090_URL", "http://tar1090.test:8080"):
        with patch.object(rm, "_is_trusted", lambda request: False):
            out = asyncio.run(rm.map_scope(_request()))
        assert out["bounds"] == nas and out["swim"]["scope"] == "national"
        assert out["swim"]["center"] is None and out["swim"]["bounds"] == nas
        assert out["receiver"]["center"] == [rm._UNTRUSTED_PLACEHOLDER_LAT, rm._UNTRUSTED_PLACEHOLDER_LON]
        assert out["tar1090_url"] is None                          # untrusted rules unchanged
        with patch.object(rm, "_is_trusted", lambda request: True):
            out = asyncio.run(rm.map_scope(_request()))
        assert out["bounds"] == nas and out["tar1090_url"] == "http://tar1090.test:8080"


# -- DEMO_MODE: synthetic only, zero network, all three routes -------------------

@pytest.fixture
def demo_no_network():
    with patch.object(rm, "DEMO_MODE", True), \
         patch("runner.main.httpx.AsyncClient", side_effect=_boom), \
         patch.object(rm, "_fetch_local_aircraft", new=AsyncMock(side_effect=_boom)), \
         patch.object(rm, "_dispatch_get_json", new=AsyncMock(side_effect=_boom)):
        yield


def test_demo_map_aircraft_is_synthetic_with_all_source_kinds(demo_no_network):
    out = asyncio.run(rm.map_aircraft(_request(), south=None, west=None, north=None, east=None,
                                      max_age_s=120))
    assert out["swim_available"] is True
    c = out["counts"]
    assert c["matched"] >= 2 and c["local"] >= 1 and c["swim"] >= 3 and c["conflict"] >= 1
    assert {a["src"] for a in out["aircraft"]} == {"matched", "local", "swim"}
    for a in out["aircraft"]:
        assert set(a) >= {"hex", "flight", "r", "t", "lat", "lon", "alt_baro", "gs", "track",
                          "src", "pos_age_s", "conflict", "conflict_note", "origin", "destination"}
    swim_only = [a for a in out["aircraft"] if a["src"] == "swim"]
    assert all(a["origin"] and a["destination"] for a in swim_only)


def test_demo_map_scope_is_synthetic_and_hides_tar1090_from_untrusted(demo_no_network):
    with patch.object(rm, "TAR1090_URL", "http://tar1090.test:8080"), \
         patch.object(rm, "_is_trusted", lambda request: False):
        out = asyncio.run(rm.map_scope(_request()))
    assert out["tar1090_url"] is None
    assert out["receiver"]["center"] == [rm._UNTRUSTED_PLACEHOLDER_LAT, rm._UNTRUSTED_PLACEHOLDER_LON]
    assert out["swim"]["center"] is None and out["swim"]["swim_enabled"] is True
    assert out["bounds"][0][0] < out["receiver"]["center"][0] < out["bounds"][1][0]
    with patch.object(rm, "TAR1090_URL", "http://tar1090.test:8080"), \
         patch.object(rm, "_is_trusted", lambda request: True):
        out = asyncio.run(rm.map_scope(_request()))
    assert out["tar1090_url"] == "http://tar1090.test:8080"
    assert out["receiver"]["range_nm"] == rm.DEFAULT_DIST
    assert out["swim"]["center"] is not None


def test_demo_map_focus_live_then_404(demo_no_network):
    hexcode = rm._FAKE_ADSB_HEX_POOL[0]
    out = asyncio.run(rm.map_focus(_request(), ident=hexcode))
    assert out["live"] is True and out["aircraft"]["hex"] == hexcode and out["hex"] == hexcode
    assert out["aircraft"]["src"] == "matched"
    by_cs = asyncio.run(rm.map_focus(_request(), ident=out["aircraft"]["flight"].lower()))
    assert by_cs["live"] is True and by_cs["aircraft"]["hex"] == hexcode
    with pytest.raises(HTTPException) as ei:
        asyncio.run(rm.map_focus(_request(), ident="zz9999"))
    assert ei.value.status_code == 404


def test_demo_bbox_filters_the_synthetic_set(demo_no_network):
    out = asyncio.run(rm.map_aircraft(_request(), south=89.0, west=0.0, north=90.0, east=1.0,
                                      max_age_s=120))
    assert out["aircraft"] == [] and out["counts"]["local"] == 0


def test_demo_routes_via_http_need_no_network(demo_no_network, monkeypatch):
    monkeypatch.setattr(rm, "_is_trusted", lambda request: True)
    c = TestClient(rm.app)
    assert c.get("/api/map/aircraft", params={"south": -10, "west": -100, "north": 60, "east": 0}).status_code == 200
    assert c.get("/api/map/scope").status_code == 200
    assert c.get("/api/map/aircraft", params={"south": 91}).status_code == 422


# -- live path: dispatch unreachable -> local only, never a failure ---------------

def test_dispatch_unreachable_returns_local_only_with_swim_available_false():
    raw = {"now": 1000.0, "aircraft": [_local(), _local(hex_="abc124", flight="TEST456", lat=0.6, lon=0.6)]}
    with patch.object(rm, "DEMO_MODE", False), \
         patch.object(rm, "_fetch_local_aircraft", new=AsyncMock(return_value=raw)), \
         patch.object(rm, "_dispatch_get_json", new=AsyncMock(return_value=None)), \
         patch.object(rm, "_is_trusted", lambda request: True):
        out = asyncio.run(rm.map_aircraft(_request(), south=0, west=0, north=1, east=1, max_age_s=120))
    assert out["swim_available"] is False and out["local_available"] is True
    assert out["counts"] == {"matched": 0, "local": 2, "swim": 0, "conflict": 0}
    assert all(a["src"] == "local" for a in out["aircraft"])


def test_dispatch_reachable_merges_and_passes_bbox_through():
    raw = {"now": 1000.0, "aircraft": [_local()]}
    calls = {}

    async def fake_dispatch(request, path, params, timeout=5.0):
        calls[path] = params
        if path == "api/v1/swim/positions":
            return {"generated_at": 1000.0, "count": 2, "truncated": True,
                    "positions": [_swim(), _swim(hex_="abc125", callsign="TEST789", lat=0.9, lon=0.9)]}
        return None

    with patch.object(rm, "DEMO_MODE", False), \
         patch.object(rm, "_fetch_local_aircraft", new=AsyncMock(return_value=raw)), \
         patch.object(rm, "_dispatch_get_json", new=fake_dispatch), \
         patch.object(rm, "_is_trusted", lambda request: True):
        out = asyncio.run(rm.map_aircraft(_request(), south=0, west=0, north=1, east=1, max_age_s=90))
    assert out["swim_available"] is True and out["swim_truncated"] is True
    assert out["counts"] == {"matched": 1, "local": 0, "swim": 1, "conflict": 0}
    p = calls["api/v1/swim/positions"]
    assert (p["south"], p["west"], p["north"], p["east"], p["max_age_s"]) == (0, 0, 1, 1, 90)


def test_local_receiver_down_still_returns_swim():
    with patch.object(rm, "DEMO_MODE", False), \
         patch.object(rm, "_fetch_local_aircraft", new=AsyncMock(side_effect=RuntimeError("down"))), \
         patch.object(rm, "_dispatch_get_json", new=AsyncMock(return_value={"positions": [_swim()], "truncated": False})), \
         patch.object(rm, "_is_trusted", lambda request: True):
        out = asyncio.run(rm.map_aircraft(_request(), south=0, west=0, north=1, east=1, max_age_s=120))
    assert out["local_available"] is False and out["swim_available"] is True
    assert out["counts"]["swim"] == 1


def test_focus_falls_back_to_last_leg_then_404():
    leg = {"callsign": "TEST123", "hex": "abc123", "registration": "N000AA", "lat": 0.3, "lon": 0.3,
           "altitude_ft": 0, "ground_speed_kt": 0, "updated_at": 500.0, "origin": "KBOS",
           "destination": "KDCA", "origin_coords": [1.0, 1.0], "destination_coords": [2.0, 2.0],
           "status": "landed", "aircraft_type": "A320"}

    async def fake_dispatch(request, path, params, timeout=5.0):
        if path == "api/v1/swim/last-leg":
            return leg if params["ident"] == "TEST123" else None
        return {"positions": [], "truncated": False}

    with patch.object(rm, "DEMO_MODE", False), \
         patch.object(rm, "_fetch_local_aircraft", new=AsyncMock(return_value={"aircraft": []})), \
         patch.object(rm, "_dispatch_get_json", new=fake_dispatch), \
         patch.object(rm, "_is_trusted", lambda request: True):
        out = asyncio.run(rm.map_focus(_request(), ident="test123"))
        assert out["live"] is False
        assert out["last"]["flight"] == "TEST123" and out["last"]["origin_coords"] == [1.0, 1.0]
        assert out["last"]["src"] == "swim" and out["last"]["status"] == "landed"
        with pytest.raises(HTTPException) as ei:
            asyncio.run(rm.map_focus(_request(), ident="nope99"))
        assert ei.value.status_code == 404


def test_dispatch_get_json_degrades_to_none_on_any_failure():
    with patch("runner.main.httpx.AsyncClient", side_effect=RuntimeError("refused")):
        assert asyncio.run(rm._dispatch_get_json(_request(), "api/v1/swim/scope", {})) is None


# -- token injection: the runner sends its service token for the SWIM paths ----

def test_swim_paths_are_tier1_injected():
    for p in ("api/v1/swim/positions", "api/v1/swim/scope", "api/v1/swim/last-leg"):
        assert p in rm._TIER1_PATHS
    with patch.object(rm, "RUNNER_ENRICHED_TOKEN", "ctdc_svc_example"):
        h = rm._dispatch_proxy_headers(_request(), "api/v1/swim/positions")
    assert h == {"Authorization": "Bearer ctdc_svc_example"}


def test_frontend_config_untrusted_placeholder_unchanged():
    """The placeholder constant refactor must not change what an untrusted
    caller of /api/v1/frontend-config has received since 2026-08-24."""
    with patch.object(rm, "_is_trusted", lambda request: False), \
         patch.object(rm, "DEFAULT_LAT", 0.5), patch.object(rm, "DEFAULT_LON", 0.5):
        out = asyncio.run(rm.frontend_config(_request()))
    assert (out["receiver_lat"], out["receiver_lon"]) == (rm._UNTRUSTED_PLACEHOLDER_LAT, rm._UNTRUSTED_PLACEHOLDER_LON)
    assert (out["receiver_lat"], out["receiver_lon"]) != (0.5, 0.5)


# -- focus search (2026-10-24, operator-reported): IATA -> ICAO, codeshare, tail /
#    serial -> hex, airplanes.live fallback only when receiver + SWIM are empty,
#    never in demo. The resolver and aggregator live on dispatch-web and are
#    mocked through _dispatch_get_json like every other dispatch call here.

def _focus_env(local_aircraft, dispatch):
    return (patch.object(rm, "DEMO_MODE", False),
            patch.object(rm, "_fetch_local_aircraft", new=AsyncMock(return_value={"aircraft": local_aircraft})),
            patch.object(rm, "_dispatch_get_json", new=dispatch),
            patch.object(rm, "_is_trusted", lambda request: True))


def _dispatch_recorder(resolve=None, aggregator=None, last_leg=None, positions=None):
    calls: list[tuple[str, dict]] = []

    async def fake(request, path, params, timeout=5.0):
        calls.append((path, dict(params)))
        if path == "api/v1/swim/positions":
            return {"positions": positions or [], "truncated": False}
        if path == "api/v1/swim/resolve":
            return resolve(params["ident"]) if callable(resolve) else resolve
        if path == "api/v1/swim/aggregator":
            return aggregator(params) if callable(aggregator) else aggregator
        if path == "api/v1/swim/last-leg":
            return last_leg(params["ident"]) if callable(last_leg) else last_leg
        return None
    fake.calls = calls
    return fake


def test_focus_iata_flight_number_matches_icao_callsign_without_a_resolver_call():
    """UA919 -> UAL919 is the platform's own prefix table, applied in the runner
    before any dispatch call -- the live hit costs nothing extra."""
    fake = _dispatch_recorder()
    a, b, c, d = _focus_env([_local(hex_="abc123", flight="UAL919 ")], fake)
    with a, b, c, d:
        out = asyncio.run(rm.map_focus(_request(), ident="ua919"))
    assert out["live"] is True and out["aircraft"]["flight"] == "UAL919" and out["aircraft"]["hex"] == "abc123"
    assert [p for p, _ in fake.calls if p in ("api/v1/swim/resolve", "api/v1/swim/aggregator")] == []


def test_focus_codeshare_operating_callsign_via_resolver():
    """A marketed flight whose operating callsign only dispatch knows
    (codeshare_map): the resolver supplies it and the snapshot is searched again."""
    fake = _dispatch_recorder(resolve={"ident": "UA8366", "hex": None, "registration": None,
                                       "callsigns": ["UA8366", "UAL8366", "SKW8366"]})
    a, b, c, d = _focus_env([_local(hex_="abc124", flight="SKW8366")], fake)
    with a, b, c, d:
        out = asyncio.run(rm.map_focus(_request(), ident="UA8366"))
    assert out["live"] is True and out["aircraft"]["flight"] == "SKW8366"
    assert ("api/v1/swim/resolve", {"ident": "UA8366"}) in fake.calls
    assert not any(p == "api/v1/swim/aggregator" for p, _ in fake.calls)


def test_focus_serial_matches_receiver_r_field_with_or_without_dash():
    fake = _dispatch_recorder()
    a, b, c, d = _focus_env([_local(hex_="abc125", flight="", r="25-33000")], fake)
    with a, b, c, d:
        for ident in ("25-33000", "2533000", "25-33000 "):
            out = asyncio.run(rm.map_focus(_request(), ident=ident))
            assert out["live"] is True and out["aircraft"]["hex"] == "abc125" and out["aircraft"]["r"] == "25-33000"
    assert not any(p == "api/v1/swim/resolve" for p, _ in fake.calls)   # the r field alone was enough


def test_focus_registration_resolves_to_hex_via_registry_then_matches_by_hex():
    """N-number not carried by the receiver's r field (or SWIM): the resolver's
    registry hex (FAA / OpenSky via shared.watchlist._local_registry_hex_lookup)
    finds the aircraft by hex."""
    def resolve(ident):
        assert ident == "Q8991S"
        return {"ident": ident, "hex": "ABC126", "registration": "Q8991S", "callsigns": []}
    fake = _dispatch_recorder(resolve=resolve)
    a, b, c, d = _focus_env([_local(hex_="abc126", flight="TEST321", r=None)], fake)
    with a, b, c, d:
        out = asyncio.run(rm.map_focus(_request(), ident="q8991s"))
    assert out["live"] is True and out["aircraft"]["hex"] == "abc126" and out["aircraft"]["src"] == "local"


def test_focus_aggregator_only_when_receiver_and_swim_are_empty():
    agg_hit = {"aggregator": "airplanes.live",
               "aircraft": {"hex": "abc127", "r": "N000ZZ", "t": "B738", "flight": "UAL919", "lat": 0.7,
                            "lon": 0.7, "alt_baro": 35000, "gs": 450, "track": 270.0, "seen_pos": 4.0}}
    # Live on the receiver -> the aggregator is never asked.
    fake = _dispatch_recorder(resolve={"ident": "UA919", "hex": None, "registration": None,
                                       "callsigns": ["UA919", "UAL919"]}, aggregator=agg_hit)
    a, b, c, d = _focus_env([_local(hex_="abc123", flight="UAL919")], fake)
    with a, b, c, d:
        asyncio.run(rm.map_focus(_request(), ident="UA919"))
    assert not any(p == "api/v1/swim/aggregator" for p, _ in fake.calls)

    # Receiver + SWIM empty -> aggregator contact comes back in the map shape, marked as such.
    fake = _dispatch_recorder(resolve={"ident": "UA919", "hex": None, "registration": None,
                                       "callsigns": ["UA919", "UAL919"]}, aggregator=agg_hit)
    a, b, c, d = _focus_env([], fake)
    with a, b, c, d:
        out = asyncio.run(rm.map_focus(_request(), ident="UA919"))
    assert out["live"] is True
    ac = out["aircraft"]
    assert ac["src"] == "aggregator" and ac["aggregator"] == "airplanes.live"
    assert ac["hex"] == "abc127" and ac["flight"] == "UAL919" and ac["r"] == "N000ZZ"
    assert (ac["lat"], ac["lon"], ac["alt_baro"], ac["pos_age_s"]) == (0.7, 0.7, 35000, 4.0)
    assert ac["conflict"] is False and ac["origin"] is None
    assert set(ac) >= {"hex", "flight", "r", "t", "lat", "lon", "alt_baro", "gs", "track", "src", "pos_age_s",
                       "conflict", "conflict_note", "origin", "destination"}
    agg_params = [p for path, p in fake.calls if path == "api/v1/swim/aggregator"]
    assert agg_params == [{"callsigns": "UAL919"}]            # ICAO form only, the IATA input is not sent
    assert not any(path == "api/v1/swim/last-leg" for path, _ in fake.calls)   # a live fix beat the stale leg


def test_focus_aggregator_miss_then_last_leg_tries_icao_candidate_then_404():
    leg = {"callsign": "UAL919", "hex": "abc123", "lat": 0.3, "lon": 0.3, "updated_at": 500.0,
           "origin": "KBOS", "destination": "KDCA", "status": "landed"}
    fake = _dispatch_recorder(resolve=lambda ident: {"ident": ident, "hex": None, "registration": None,
                                                     "callsigns": [ident] + (["UAL919"] if ident == "UA919" else [])},
                              aggregator=None, last_leg=lambda ident: leg if ident == "UAL919" else None)
    a, b, c, d = _focus_env([], fake)
    with a, b, c, d:
        out = asyncio.run(rm.map_focus(_request(), ident="UA919"))
        assert out["live"] is False and out["last"]["flight"] == "UAL919" and out["last"]["src"] == "swim"
        assert [p["ident"] for path, p in fake.calls if path == "api/v1/swim/last-leg"] == ["UA919", "UAL919"]
        with pytest.raises(HTTPException) as ei:
            asyncio.run(rm.map_focus(_request(), ident="ZZ9999"))
        assert ei.value.status_code == 404


def test_focus_hex_resolved_from_registry_is_sent_to_aggregator_by_hex():
    fake = _dispatch_recorder(resolve={"ident": "Q8991S", "hex": "abc128", "registration": "Q8991S",
                                       "callsigns": []},
                              aggregator=lambda p: {"aggregator": "airplanes.live",
                                                    "aircraft": {"hex": p["hex"], "lat": 0.1, "lon": 0.1,
                                                                 "seen_pos": 1.0}})
    a, b, c, d = _focus_env([], fake)
    with a, b, c, d:
        out = asyncio.run(rm.map_focus(_request(), ident="Q8991S"))
    assert out["aircraft"]["src"] == "aggregator" and out["aircraft"]["hex"] == "abc128"
    # Hex first (the stronger key); the tail stays as a callsign candidate because a GA
    # aircraft transmits its N-number as its flight id -- dispatch tries it only after the hex.
    assert [p for path, p in fake.calls if path == "api/v1/swim/aggregator"] == [{"hex": "abc128", "callsigns": "Q8991S"}]


def test_focus_demo_never_resolves_or_calls_the_aggregator(demo_no_network):
    """DEMO_MODE: the synthetic snapshot is the only source. The fixture makes
    any dispatch call (resolve, aggregator, last-leg) raise."""
    with pytest.raises(HTTPException) as ei:
        asyncio.run(rm.map_focus(_request(), ident="UA919"))
    assert ei.value.status_code == 404
    with pytest.raises(HTTPException):
        asyncio.run(rm.map_focus(_request(), ident="Q8991S"))


def test_focus_routes_are_tier1_injected():
    for p in ("api/v1/swim/resolve", "api/v1/swim/aggregator"):
        assert p in rm._TIER1_PATHS
