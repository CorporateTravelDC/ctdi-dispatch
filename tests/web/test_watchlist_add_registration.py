"""
2026-10-13 -- POST /api/v1/watchlist/flights stored registration=None
(seen on wl-flight-ual4520-20261011).

Only the caller's own `registration` field ever set it, so three ordinary
adds lost it: a hex-only add (never reverse-resolved), a body that called
it "tail" (FIDS's name; pydantic dropped it -- 2026-10-18: now deliberately
ignored, a FIDS tail is never stored), and an add with no identity
while the FAA plan found through codeshare_map (GJS4520) carried both hex
and tail. Identities here are synthetic (hex 000001, tail N000TS).
"""
import asyncio
import json

import pytest

import common.db as db
from common import flight_instance as fi


@pytest.fixture
def route(monkeypatch):
    db.init_db_all()
    fi.clear_alias_cache()
    import web.routes.watchlist as wl_route
    monkeypatch.setattr(wl_route, "_fire_ntfy_dual", lambda **k: None)
    import shared.watchlist as wl
    monkeypatch.setattr(wl, "seed_departure_phase_from_tfms", lambda entry: None, raising=False)
    with db.conn() as c:
        c.execute("INSERT INTO faa_aircraft_registry (n_number, mode_s_hex, updated_at) VALUES (?, ?, ?)",
                  ("000TS", "000001", 0))
    yield wl_route
    fi.clear_alias_cache()


def _add(wl_route, **body):
    from auth.auth import Tier
    resp = asyncio.run(wl_route.add_flight_watchlist(
        wl_route.FlightWatchlistRequest(**body), tier=Tier.ADMIN))
    return json.loads(resp.body)


def _stored(entry_id):
    return next(e for e in db.get_watchlist_entries(entry_type="flight") if e["id"] == entry_id)


def test_hex_only_add_reverse_resolves_the_tail(route):
    out = _add(route, identifier="ZZZ4520", hex_id="000001", origin="DCA", destination="ORD")
    assert out["registration"] == "N000TS"
    assert _stored(out["id"])["registration"] == "N000TS"


def test_tail_alias_is_never_stored(route):
    # 2026-10-18 (operator): "tail" is FIDS's name for the airframe; a FIDS tail must
    # never become a stored registration (tails are SWIM-only). Was: accepted as an alias.
    out = _add(route, identifier="ZZZ4521", tail="n000ts", origin="DCA", destination="ORD")
    stored = _stored(out["id"])
    assert stored["registration"] is None
    assert stored["hex_id"] is None
    assert out["ignored_fields"] == ["tail"]


def test_codeshare_fdps_plan_supplies_hex_and_tail_with_provenance(route):
    db.upsert_codeshare_mapping("UAL", "4520", "GJS", "4520", "KDCA", "KORD", "test")
    raw = ("<MessageCollection><message><flight>"
           "<flightIdentification aircraftIdentification=\"GJS4520\"/>"
           "<aircraftDescription aircraftAddress=\"000001\" registration=\"N000TS\"/>"
           "</flight></message></MessageCollection>")
    db.upsert_flight_event("G-T", "GJS", "4520", "KDCA", "KORD", None, None, None, "active",
                           None, None, None, None, raw)
    out = _add(route, identifier="UAL4520", origin="DCA", destination="ORD")
    stored = _stored(out["id"])
    assert out["operating_callsign"] == "GJS4520"
    assert (stored["hex_id"], stored["registration"], stored["hex_source"]) == \
        ("000001", "N000TS", "fdps")


def test_caller_values_are_never_overwritten(route):
    out = _add(route, identifier="ZZZ4522", hex_id="000002", registration="N001TS")
    stored = _stored(out["id"])
    assert (stored["hex_id"], stored["registration"]) == ("000002", "N001TS")


def test_batch_add_resolves_tail_too(route):
    from auth.auth import Tier
    body = route.FlightBatchRequest(entries=[{"identifier": "ZZZ4523", "hex_id": "000001"},
                                             {"identifier": "ZZZ4524", "tail": "N000TS"}])
    asyncio.run(route.add_flight_watchlist_batch(body, tier=Tier.ADMIN))
    regs = {e["identifier"]: e["registration"] for e in db.get_watchlist_entries(entry_type="flight")}
    # hex-only add still reverse-resolves; a `tail` is never stored (2026-10-18)
    assert regs["ZZZ4523"] == "N000TS" and regs["ZZZ4524"] is None
