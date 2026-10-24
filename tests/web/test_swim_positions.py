"""2026-10-24 (map rework, wave 1): dispatch-web's /api/v1/swim/* -- stored SWIM
positions for the runner's aircraft map. bbox + max_age filter in SQL,
TAIS merge/dedup, the (flight_id, updated_at) hex-parse cache, the Tier-0
LADD fail-closed filter, scope from geo_filter's constants, last-leg by
callsign and by hex. Isolated SQLite via tests/conftest.py (same pattern as
test_watchlist_registrations_ladd.py). Every identifier is synthetic
(hex ABC123/ABC124, callsigns TEST123/TEST456, tails N000xx, points ~0.5,0.5).
"""
from __future__ import annotations

import json
import time

import pytest
from fastapi import HTTPException

import common.db as db
from auth.auth import Tier


def _fixm(callsign: str, hex_: str | None, reg: str | None) -> str:
    desc = ""
    if hex_ or reg:
        attrs = (f' aircraftAddress="{hex_}"' if hex_ else "") + (f' registration="{reg}"' if reg else "")
        desc = f"<ns2:aircraftDescription{attrs}/>"
    return ('<ns5:MessageCollection xmlns:ns5="urn:x" xmlns:ns2="urn:y"><message><flight>'
            f'<flightIdentification aircraftIdentification="{callsign}"/>{desc}'
            '</flight></message></ns5:MessageCollection>')


def _fe(flight_id, airline, num, lat, lon, age_s, hex_=None, reg=None, origin="KBOS",
        dest="KDCA", status="active", alt=31000, gs=440):
    with db.conn() as c:
        c.execute(
            "INSERT INTO flight_events (flight_id, airline, flight_num, origin, destination, aircraft_type, "
            "status, position_lat, position_lon, altitude_ft, ground_speed_kt, raw_json, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (flight_id, airline, num, origin, dest, "A320", status, lat, lon, alt, gs,
             _fixm(f"{airline}{num}", hex_, reg), time.time() - age_s))


def _tt(track_id, callsign, mode_s, lat, lon, age_s, facility="PCT"):
    last_seen = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - age_s))
    db.upsert_terminal_track(track_id, facility, callsign, "1200", mode_s, lat, lon, 5000.0, 180, last_seen)


@pytest.fixture
def seeded():
    db.init_db_all()
    db._SWIM_HEX_REG_CACHE.clear()
    with db.conn() as c:
        for t in ("flight_events", "terminal_tracks", "faa_ladd_aircraft", "faa_aircraft_registry", "cifp_fixes"):
            c.execute(f"DELETE FROM {t}")
    _fe("g-in-fresh", "TES", "T123", 0.50, 0.50, 30, hex_="ABC123", reg="N000AA")
    _fe("g-in-stale", "TES", "T456", 0.60, 0.60, 900, hex_="ABC124", reg="N000BB")
    _fe("g-out-fresh", "TES", "T789", 5.00, 5.00, 30, hex_="ABC125", reg="N000CC")
    _fe("g-nopos", "TES", "T000", None, None, 30, hex_="ABC126", reg="N000DD")
    _tt("t1", "TEST123", "ABC123", 0.51, 0.51, 20)          # same hex as g-in-fresh -> deduped
    _tt("t2", "TAIS01", "ABC127", 0.55, 0.55, 20)           # new TAIS-only aircraft
    _tt("t3", "TAIS02", "ABC128", 0.56, 0.56, 900)          # stale
    import web.routes.swim_positions as route
    return route


def _positions(route, tier=Tier.T1, **kw):
    args = dict(south=0.0, west=0.0, north=1.0, east=1.0, max_age_s=120, limit=2000)
    args.update(kw)
    resp = route.swim_positions(tier=tier, **args)
    return resp.status_code, json.loads(resp.body)


def test_bbox_and_age_filter_in_sql_plus_tais_merge(seeded):
    status, body = _positions(seeded)
    assert status == 200 and body["truncated"] is False
    by_cs = {p["callsign"]: p for p in body["positions"]}
    assert set(by_cs) == {"TEST123", "TAIS01"}                 # stale, out-of-box, no-position, dup TAIS all gone
    fdps = by_cs["TEST123"]
    assert fdps["hex"] == "abc123" and fdps["registration"] == "N000AA" and fdps["source"] == "fdps"
    assert (fdps["origin"], fdps["destination"]) == ("KBOS", "KDCA")
    assert fdps["altitude_ft"] == 31000 and fdps["ground_speed_kt"] == 440 and fdps["track_deg"] is None
    assert fdps["flight_id"] == "g-in-fresh" and abs(fdps["updated_at"] - (time.time() - 30)) < 5
    tais = by_cs["TAIS01"]
    assert tais["source"] == "tais" and tais["hex"] == "abc127" and tais["origin"] is None
    assert body["count"] == 2 and set(body) == {"generated_at", "count", "truncated", "positions"}
    # a wider age window admits the stale FDPS row and the stale TAIS track
    _, wide = _positions(seeded, max_age_s=3600)
    assert {p["callsign"] for p in wide["positions"]} == {"TEST123", "TEST456", "TAIS01", "TAIS02"}
    # a wider box admits the out-of-box row
    _, big = _positions(seeded, north=10.0, east=10.0)
    assert "TEST789" in {p["callsign"] for p in big["positions"]}


def test_limit_sets_truncated(seeded):
    _, body = _positions(seeded, limit=1)
    assert body["count"] == 1 and body["truncated"] is True
    assert body["positions"][0]["source"] == "fdps"           # FDPS first, newest first


def test_hex_parse_cached_by_flight_id_and_updated_at(seeded, monkeypatch):
    calls = {"n": 0}
    real = db._extract_aircraft_hex_registration

    def counting(flight):
        calls["n"] += 1
        return real(flight)

    monkeypatch.setattr(db, "_extract_aircraft_hex_registration", counting)
    db._SWIM_HEX_REG_CACHE.clear()
    _positions(seeded)
    first = calls["n"]
    assert first >= 1
    _positions(seeded)
    assert calls["n"] == first                                 # unchanged rows: no re-parse
    with db.conn() as c:
        c.execute("UPDATE flight_events SET updated_at = ? WHERE flight_id = 'g-in-fresh'", (time.time(),))
    _positions(seeded)
    assert calls["n"] == first + 1                             # a new updated_at re-parses once


def test_tier0_is_ladd_filtered_fail_closed(seeded, monkeypatch):
    with db.conn() as c:
        c.execute("INSERT INTO faa_ladd_aircraft (n_number, updated_at) VALUES (?, ?)", ("N000AA", time.time()))
        c.execute("INSERT INTO faa_aircraft_registry (n_number, mode_s_hex, updated_at) VALUES (?, ?, ?)",
                  ("000LH", "ABC127", 0))
        c.execute("INSERT INTO faa_ladd_aircraft (n_number, updated_at) VALUES (?, ?)", ("N000LH", time.time()))
    # internal (Tier 1): unfiltered operational display
    _, internal = _positions(seeded, tier=Tier.T1)
    assert {p["callsign"] for p in internal["positions"]} == {"TEST123", "TAIS01"}
    # public (Tier 0): listed by registration (TEST123) and by hex->registry (TAIS01) both removed
    status, public = _positions(seeded, tier=Tier.T0)
    assert status == 200 and public["positions"] == [] and public["count"] == 0
    assert "N000AA" not in json.dumps(public) and "abc127" not in json.dumps(public)
    # filter error -> 503, never an unfiltered list
    monkeypatch.setattr(db, "faa_ladd_listed_batch", lambda idents: (_ for _ in ()).throw(RuntimeError("down")))
    with pytest.raises(HTTPException) as ei:
        _positions(seeded, tier=Tier.T0)
    assert ei.value.status_code == 503 and "N000AA" not in str(ei.value.detail)
    _, still = _positions(seeded, tier=Tier.T1)                # internal path does not consult the filter
    assert still["count"] == 2


def test_scope_from_geo_filter_constants(seeded, monkeypatch):
    from ingest.parsers import geo_filter
    expected = geo_filter.storage_scope()
    monkeypatch.setattr(db, "get_feed_states", lambda: [{"feed_name": "fdps", "fetched_at": time.time() - 10}])
    internal = json.loads(seeded.swim_scope(tier=Tier.T1).body)
    assert internal["center"] == expected["center"] and internal["radius_nm"] == 250.0
    assert internal["airports"] == expected["airports"] and all(len(a) == 4 for a in internal["airports"])
    assert "KDCA" in internal["airports"] and internal["swim_enabled"] is True and internal["label"]
    public = json.loads(seeded.swim_scope(tier=Tier.T0).body)
    assert public["center"] is None and public["radius_nm"] == 250.0
    monkeypatch.setattr(db, "get_feed_states", lambda: [{"feed_name": "fdps", "fetched_at": time.time() - 9000}])
    assert json.loads(seeded.swim_scope(tier=Tier.T1).body)["swim_enabled"] is False


def test_scope_regional_payload_shape(seeded, monkeypatch):
    # 2026-10-24: the payload now names its scope and carries bounds (null when regional).
    monkeypatch.delenv("SWIM_STORAGE_SCOPE", raising=False)
    internal = json.loads(seeded.swim_scope(tier=Tier.T1).body)
    assert internal["scope"] == "regional" and internal["bounds"] is None
    assert internal["label"].startswith("SWIM storage filter: 250 nm around KDCA")


def test_scope_national_is_nas_box_for_every_tier(seeded, monkeypatch):
    """2026-10-24: SWIM_STORAGE_SCOPE=national -> no circle for anyone, the NAS
    box for everyone (public geography), airports still listed, untrusted
    centre still null."""
    monkeypatch.setenv("SWIM_STORAGE_SCOPE", "national")
    monkeypatch.setattr(db, "get_feed_states", lambda: [{"feed_name": "fdps", "fetched_at": time.time() - 10}])
    nas = [[17.5, -179.5], [72.0, -60.0]]
    internal = json.loads(seeded.swim_scope(tier=Tier.T1).body)
    assert internal["scope"] == "national" and internal["swim_enabled"] is True
    assert internal["center"] is None and internal["radius_nm"] is None
    assert internal["bounds"] == nas and internal["label"] == "NAS-wide (FAA SWIM)"
    assert "KDCA" in internal["airports"] and all(len(a) == 4 for a in internal["airports"])
    public = json.loads(seeded.swim_scope(tier=Tier.T0).body)
    assert public["center"] is None and public["radius_nm"] is None and public["bounds"] == nas


def test_last_leg_by_callsign_and_hex_with_airport_coords(seeded):
    with db.conn() as c:
        c.execute("INSERT INTO cifp_fixes (ident, icao_region, type, lat, lon, name, parent_airport, cycle) "
                  "VALUES (?,?,?,?,?,?,?,?)", ("KBOS", "K6", "A", 42.36, -71.01, "BOS", None, "2610"))
    body = json.loads(seeded.swim_last_leg(ident="test123", tier=Tier.T1).body)
    assert body["callsign"] == "TEST123" and body["hex"] == "abc123" and body["status"] == "active"
    assert body["origin_coords"] == [42.36, -71.01] and body["destination_coords"] is None
    assert body["lat"] == 0.5 and body["source"] == "fdps"
    by_hex = json.loads(seeded.swim_last_leg(ident="ABC124", tier=Tier.T1).body)
    assert by_hex["callsign"] == "TEST456"                      # stale for the map, but within 24 h
    with pytest.raises(HTTPException) as ei:
        seeded.swim_last_leg(ident="TEST999", tier=Tier.T1)
    assert ei.value.status_code == 404
    with db.conn() as c:
        c.execute("UPDATE flight_events SET updated_at = ? WHERE flight_id = 'g-in-fresh'", (time.time() - 90000,))
    with pytest.raises(HTTPException):
        seeded.swim_last_leg(ident="TEST123", tier=Tier.T1)    # older than 24 h -> 404


def test_last_leg_tier0_hides_ladd_listed(seeded):
    with db.conn() as c:
        c.execute("INSERT INTO faa_ladd_aircraft (n_number, updated_at) VALUES (?, ?)", ("N000AA", time.time()))
    assert seeded.swim_last_leg(ident="TEST123", tier=Tier.T1).status_code == 200
    with pytest.raises(HTTPException) as ei:
        seeded.swim_last_leg(ident="TEST123", tier=Tier.T0)
    assert ei.value.status_code == 404


def test_routes_registered_on_the_app(seeded):
    """Through the real app: a tunnel-stamped (X-CTDI-Public) caller is Tier 0
    -> null center; the bare in-process client (no token) is Tier 0 too."""
    from fastapi.testclient import TestClient
    import web.main as web_main
    c = TestClient(web_main.app)
    r = c.get("/api/v1/swim/scope", headers={"X-CTDI-Public": "1"})
    assert r.status_code == 200 and r.json()["center"] is None and r.json()["radius_nm"] == 250.0
    r = c.get("/api/v1/swim/positions", params={"south": 0, "west": 0, "north": 1, "east": 1})
    assert r.status_code == 200 and r.json()["count"] == 2
    assert c.get("/api/v1/swim/positions", params={"south": 0}).status_code == 422
    assert c.get("/api/v1/swim/last-leg", params={"ident": "TEST999"}).status_code == 404
