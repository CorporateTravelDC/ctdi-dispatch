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


# ── 2026-10-21: identity is filled only from TODAY's plan ────────────────────
# Seen live: a regional add with no tail filled hex+registration from a plan
# four days old (a different airframe). Synthetic identities only.

def _plan(callsign, origin, dest, *, age_s=0.0, departure_time=None):
    import time
    raw = ("<MessageCollection><message><flight>"
           f"<flightIdentification aircraftIdentification=\"{callsign}\"/>"
           "<aircraftDescription aircraftAddress=\"000001\" registration=\"N000TS\"/>"
           "</flight></message></MessageCollection>")
    db.upsert_flight_event(f"G-{callsign}", callsign[:3], callsign[3:], origin, dest, None,
                           departure_time, None, "active", None, None, None, None, raw)
    with db.conn() as c:
        c.execute("UPDATE flight_events SET updated_at = ? WHERE flight_id = ?",
                  (time.time() - age_s, f"G-{callsign}"))


def test_stale_plan_four_days_old_does_not_fill_identity(route):
    _plan("ZZQ4601", "KDCA", "KORD", age_s=4 * 86400)
    out = _add(route, identifier="ZZQ4601", origin="DCA", destination="ORD")
    stored = _stored(out["id"])
    assert (stored["hex_id"], stored["registration"]) == (None, None)   # stays hex-pending


def test_fresh_plan_fills_identity(route):
    _plan("ZZQ4602", "KDCA", "KORD", age_s=2 * 3600)
    out = _add(route, identifier="ZZQ4602", origin="DCA", destination="ORD")
    stored = _stored(out["id"])
    assert (stored["hex_id"], stored["registration"], stored["hex_source"]) == \
        ("000001", "N000TS", "fdps")


def test_plan_departure_time_far_from_scheduled_departure_does_not_fill(route):
    import time
    # plan freshly touched, but its filed departure is 4 days from the leg being added
    _plan("ZZQ4603", "KDCA", "KORD", departure_time=time.time() - 4 * 86400)
    out = _add(route, identifier="ZZQ4603", origin="DCA", destination="ORD",
               scheduled_departure=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    assert _stored(out["id"])["hex_id"] is None


def test_plan_departure_time_matching_scheduled_departure_fills(route):
    import time
    dep = time.time() + 3 * 3600
    _plan("ZZQ4604", "KDCA", "KORD", departure_time=dep)
    out = _add(route, identifier="ZZQ4604", origin="DCA", destination="ORD",
               scheduled_departure=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(dep)))
    assert _stored(out["id"])["registration"] == "N000TS"


def test_fresh_plan_with_contradicting_origin_still_rejected(route):
    _plan("ZZQ4605", "KEWR", "KORD", age_s=600)
    out = _add(route, identifier="ZZQ4605", origin="DCA", destination="ORD")
    stored = _stored(out["id"])
    assert (stored["hex_id"], stored["registration"]) == (None, None)
