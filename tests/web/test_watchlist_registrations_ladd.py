"""
2026-10-16 (operator: keep the LADD list for Jumpseat). GET
/api/v1/watchlist/registrations is the only list acars-watcher's Jumpseat
thread may query a third party with: the flight-watch identifiers with every
LADD-listed one removed by the production filter (common.db.faa_is_ladd /
faa_lookup_by_hex), counted but never returned; a lookup error is a 503, never
an unfiltered list. Identifiers here are synthetic (N000xx, hex 00000x).
"""
from __future__ import annotations

import asyncio
import json
import time

import pytest
from fastapi import HTTPException

import common.db as db


@pytest.fixture
def route(monkeypatch):
    db.init_db_all()
    import web.routes.watchlist as wl_route
    monkeypatch.setattr(wl_route, "_fire_ntfy_dual", lambda **k: None)
    with db.conn() as c:
        c.execute("INSERT INTO faa_ladd_aircraft (n_number, updated_at) VALUES (?, ?)",
                  ("N000LD", time.time()))
        c.execute("INSERT INTO faa_ladd_aircraft (n_number, updated_at) VALUES (?, ?)",
                  ("N000HX", time.time()))
        c.execute("INSERT INTO faa_aircraft_registry (n_number, mode_s_hex, updated_at) VALUES (?, ?, ?)",
                  ("000HX", "000002", 0))
    return wl_route


def _entry(ident, **kw):
    e = {"id": f"wl-flight-{ident.lower()}", "entry_type": "flight", "tier": "transient",
         "identifier": ident, "auto_remove_at": "2099-01-01T00:00:00Z", "added_by": "test"}
    e.update(kw)
    db.upsert_watchlist_entry(e)


def _get(wl_route):
    from auth.auth import Tier
    resp = asyncio.run(wl_route.watchlist_registrations(tier=Tier.T1))
    return resp.status_code, json.loads(resp.body)


def test_ladd_listed_registration_is_removed_and_counted(route):
    _entry("ZZZ100", registration="N000OK")              # kept: callsign + tail
    _entry("ZZZ101", registration="n-000ld")             # LADD by registration (dash form)
    _entry("ZZZ102", hex_id="000002")                    # LADD by hex -> registry tail
    _entry("N000LD")                                     # LADD by identifier
    db.create_watchlist_session("s1", "flight", "N000SS", "2026-10-16")
    db.create_watchlist_session("s2", "train", "123", "2026-10-16")

    status, body = _get(route)
    assert status == 200
    assert body["registrations"] == ["N000OK", "N000SS", "ZZZ100"]
    assert body["count"] == 3
    # ZZZ101, N000LD, ZZZ102 (the hex entry's callsign) -- N000LD once
    assert body["ladd_filtered"] == 3
    assert body["checked_at"].endswith("Z")
    assert "N000LD" not in json.dumps(body) and "ZZZ101" not in json.dumps(body)


def test_identifier_shared_with_a_listed_entry_stays_out(route):
    _entry("ZZZ200", registration="N000OK")
    _entry("ZZZ201", registration="N000OK", hex_id="000002")   # listed via hex
    status, body = _get(route)
    assert status == 200
    assert body["registrations"] == ["ZZZ200"]
    assert body["ladd_filtered"] == 2


def test_lookup_error_is_503_never_an_unfiltered_list(route, monkeypatch):
    _entry("ZZZ300", registration="N000LD")

    def boom(_ident):
        raise RuntimeError("ladd table unavailable")

    monkeypatch.setattr(db, "faa_is_ladd", boom)
    from auth.auth import Tier
    with pytest.raises(HTTPException) as ei:
        asyncio.run(route.watchlist_registrations(tier=Tier.T1))
    assert ei.value.status_code == 503
    assert "N000LD" not in str(ei.value.detail)


def test_route_is_tier1_like_the_watchlist_list(route):
    from web.routes.watchlist import router
    regs = [r for r in router.routes if getattr(r, "path", "") == "/api/v1/watchlist/registrations"]
    assert len(regs) == 1 and regs[0].methods == {"GET"}

    def minimum_tier(route_obj):
        from auth.auth import Tier
        (dep,) = route_obj.dependant.dependencies
        cells = [c.cell_contents for c in dep.call.__closure__ or ()]
        return [c for c in cells if isinstance(c, Tier)]

    listing = [r for r in router.routes if getattr(r, "path", "") == "/api/v1/watchlist"][0]
    from auth.auth import Tier
    assert minimum_tier(regs[0]) == minimum_tier(listing) == [Tier.T1]
