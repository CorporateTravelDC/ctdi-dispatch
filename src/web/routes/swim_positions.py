"""
web.routes.swim_positions -- SWIM positions for the runner's aircraft map.

2026-10-24 (map rework, wave 1, operator-approved spec): ONE aircraft map on
the runner draws the local receiver and SWIM together. The runner calls these
routes server-side (DISPATCH_BASE_URL) and merges the result with the local
receiver; the browser never calls dispatch-web directly.

  GET /api/v1/swim/positions?south=&west=&north=&east=&max_age_s=120&limit=2000
  GET /api/v1/swim/scope
  GET /api/v1/swim/last-leg?ident=<callsign|hex>

Tier / privacy decision (documented here because the spec asked for it):
  * The runner sends RUNNER_ENRICHED_TOKEN (cert tier = Tier.T1) for these
    paths (runner/main.py _TIER1_PATHS). A Tier-1 caller is the runner on
    behalf of a tailnet operator, or an operator token -- the internal
    operational display. That path gets every stored position unfiltered,
    exactly as the existing internal watchlist/ETA display does
    (runner/main.py _public_include: "public use is LADD-restricted,
    internal use is not", operator 2026-10-20).
  * Any caller that resolves to Tier 0 (no token, or X-CTDI-Public stamped
    by the tunnel) is a public exposure: LADD-listed aircraft are removed
    with the production filters (db.faa_ladd_hex_batch for the hex,
    db.faa_ladd_listed_batch for registration / callsign / flight id), and
    a filter error is a 503, never an unfiltered list (same fail-closed rule
    as /api/v1/watchlist/registrations). /scope hands a Tier-0 caller a null
    center. PIA (Privacy ICAO Address) aircraft carry a temporary hex that is
    not in the FAA registry, so the hex filter cannot recognise them; their
    third-party callsign is checked against the LADD list like any other
    identifier -- there is no separate PIA list in this platform today.
"""
from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse

from auth.auth import Tier, resolve_tier
from common import db
from ingest.parsers.geo_filter import storage_scope

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/swim", tags=["swim"])

_SCOPE_LABEL = "SWIM storage filter: %d nm around KDCA + %d core airports"
_SWIM_FEEDS = ("fdps", "stdds")
_SWIM_FRESH_S = 3600


def _is_internal(tier: Tier) -> bool:
    """Tier 1 and above = the runner's service token or an operator token
    (see the module docstring). Tier 0 = public exposure, LADD-filtered."""
    return tier in (Tier.T1, Tier.T2, Tier.ADMIN)


def _ladd_filter(positions: list[dict]) -> list[dict]:
    """Drop every position whose hex, registration, callsign or flight id is
    LADD-listed. Raises HTTPException(503) on any lookup error -- a public
    caller never receives an unfiltered list."""
    hexes = {p["hex"] for p in positions if p.get("hex")}
    idents: set[str] = set()
    for p in positions:
        for k in ("registration", "callsign", "flight_id"):
            if p.get(k):
                idents.add(str(p[k]).strip().upper())
    try:
        listed_hex = db.faa_ladd_hex_batch(hexes) if hexes else set()
        listed_ids = db.faa_ladd_listed_batch(idents) if idents else set()
    except Exception as e:  # noqa: BLE001 -- fail closed, never leak the list
        log.warning("swim positions: LADD filter unavailable (fail closed): %s", type(e).__name__)
        raise HTTPException(status_code=503, detail="privacy filter unavailable")
    out = []
    for p in positions:
        if p.get("hex") and p["hex"].lower() in listed_hex:
            continue
        if any(p.get(k) and str(p[k]).strip().upper() in listed_ids
               for k in ("registration", "callsign", "flight_id")):
            continue
        out.append(p)
    return out


def _public_position(p: dict) -> dict:
    """The contract shape (plus aircraft_type/status, additive)."""
    return {
        "flight_id": p.get("flight_id"),
        "callsign": p.get("callsign"),
        "hex": p.get("hex"),
        "registration": p.get("registration"),
        "lat": p.get("lat"),
        "lon": p.get("lon"),
        "altitude_ft": p.get("altitude_ft"),
        "ground_speed_kt": p.get("ground_speed_kt"),
        "track_deg": p.get("track_deg"),
        "updated_at": p.get("updated_at"),
        "origin": p.get("origin"),
        "destination": p.get("destination"),
        "source": p.get("source"),
        "aircraft_type": p.get("aircraft_type"),
        "status": p.get("status"),
    }


@router.get("/positions")
def swim_positions(
    south: float = Query(..., ge=-90, le=90),
    west: float = Query(..., ge=-180, le=180),
    north: float = Query(..., ge=-90, le=90),
    east: float = Query(..., ge=-180, le=180),
    max_age_s: int = Query(120, ge=1, le=3600),
    limit: int = Query(2000, ge=1, le=5000),
    tier: Tier = Depends(resolve_tier),
) -> JSONResponse:
    """Stored SWIM positions (FDPS flight_events, TAIS terminal_tracks) in
    the box, newer than max_age_s. bbox + age filter run in SQL."""
    try:
        res = db.get_swim_positions_bbox(south, west, north, east,
                                         max_age_s=max_age_s, limit=limit)
    except Exception as e:  # noqa: BLE001
        log.warning("swim positions query failed: %s", type(e).__name__)
        raise HTTPException(status_code=503, detail="swim positions unavailable")
    positions = res["positions"]
    if not _is_internal(tier):
        positions = _ladd_filter(positions)
    body = [_public_position(p) for p in positions]
    return JSONResponse({
        "generated_at": time.time(),
        "count": len(body),
        "truncated": bool(res["truncated"]),
        "positions": body,
    })


def _swim_enabled() -> bool:
    """True when a SWIM push feed (FDPS/STDDS) has reported within the last
    hour -- the storage filter exists regardless, this says whether anything
    is flowing into it. Never raises."""
    try:
        now = time.time()
        for f in db.get_feed_states():
            if f.get("feed_name") in _SWIM_FEEDS and (f.get("fetched_at") or 0) > now - _SWIM_FRESH_S:
                return True
    except Exception as e:  # noqa: BLE001
        log.debug("swim scope: feed state unavailable (non-fatal): %s", e)
    return False


def swim_scope_payload(internal: bool) -> dict:
    """The SWIM storage area, derived from ingest.parsers.geo_filter's
    constants (250 nm around KDCA + CORE_AIRPORTS) via its storage_scope()
    accessor -- never new config. The center is only handed to an internal
    caller (spec: untrusted -> null)."""
    scope = storage_scope()
    return {
        "swim_enabled": _swim_enabled(),
        "center": scope["center"] if internal else None,
        "radius_nm": scope["radius_nm"],
        "airports": scope["airports"],
        "label": _SCOPE_LABEL % (int(scope["radius_nm"]), len(scope["airports"])),
    }


@router.get("/scope")
def swim_scope(tier: Tier = Depends(resolve_tier)) -> JSONResponse:
    return JSONResponse(swim_scope_payload(_is_internal(tier)))


@router.get("/last-leg")
def swim_last_leg(
    ident: str = Query(..., min_length=2, max_length=16),
    tier: Tier = Depends(resolve_tier),
) -> JSONResponse:
    """Most recent flight_events row within 24 h for a callsign or hex, with
    origin/destination coordinates when the CIFP reference table resolves
    them. 404 when nothing is stored (and, for a Tier-0 caller, when the
    aircraft is LADD-listed -- a 404 does not confirm it exists)."""
    try:
        leg = db.get_swim_last_leg(ident)
    except Exception as e:  # noqa: BLE001
        log.warning("swim last-leg query failed: %s", type(e).__name__)
        raise HTTPException(status_code=503, detail="swim last-leg unavailable")
    if not leg:
        raise HTTPException(status_code=404, detail="no recent leg")
    if not _is_internal(tier) and not _ladd_filter([leg]):
        raise HTTPException(status_code=404, detail="no recent leg")
    coords: dict = {}
    try:
        coords = db.get_airport_coords([leg.get("origin"), leg.get("destination")])
    except Exception as e:  # noqa: BLE001
        log.debug("swim last-leg: airport coords unavailable (non-fatal): %s", e)
    body = _public_position(leg)
    body.update({
        "departure_time": leg.get("departure_time"),
        "arrival_time": leg.get("arrival_time"),
        "origin_coords": list(coords[leg["origin"]]) if leg.get("origin") in coords else None,
        "destination_coords": list(coords[leg["destination"]]) if leg.get("destination") in coords else None,
    })
    return JSONResponse(body)
