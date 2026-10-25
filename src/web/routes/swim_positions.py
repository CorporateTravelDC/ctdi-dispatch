"""
web.routes.swim_positions -- SWIM positions for the runner's aircraft map.

2026-10-24 (map rework, wave 1, operator-approved spec): ONE aircraft map on
the runner draws the local receiver and SWIM together. The runner calls these
routes server-side (DISPATCH_BASE_URL) and merges the result with the local
receiver; the browser never calls dispatch-web directly.

  GET /api/v1/swim/positions?south=&west=&north=&east=&max_age_s=120&limit=2000
  GET /api/v1/swim/scope
  GET /api/v1/swim/last-leg?ident=<callsign|hex>
  GET /api/v1/swim/resolve?ident=<flight|hex|registration|serial>   (Tier 1+, 2026-10-24)
  GET /api/v1/swim/aggregator?hex=&callsigns=                        (Tier 1+, 2026-10-24)

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
import re
import time

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse

from auth.auth import Tier, resolve_tier
from common import db
from common.airline_codes import to_icao_callsign
from ingest.parsers.geo_filter import storage_scope
# 2026-10-24: the focus resolver below reuses the watchlist's own helpers
# (registry tail -> hex, airplanes.live fallback). dispatch-web already
# imports shared.watchlist (routes/watchlist.py, routes/webhooks.py); the
# runner image cannot (no `requests` in its lock), which is why these two
# routes exist here instead of inside runner/main.py's /api/map/focus.
from shared import watchlist as _watchlist

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/swim", tags=["swim"])

# 2026-10-24: ~~_SCOPE_LABEL = "SWIM storage filter: %d nm around KDCA + %d core airports"~~
# the label now comes from geo_filter.storage_scope() itself, which knows
# whether the deployment is regional (circle + airports) or national (NAS box).
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
    caller (spec: untrusted -> null).

    2026-10-24: SWIM_STORAGE_SCOPE=national (dispatch.env, loaded by this
    container too) makes storage_scope() return the NAS form: scope
    "national", center/radius_nm null for EVERY caller, bounds = the NAS box.
    The box is public geography (the FAA's own service area), so it is
    handed to untrusted callers unchanged; the untrusted rule only ever
    protected the regional circle's centre, and that stays null for them."""
    scope = storage_scope()
    return {
        "swim_enabled": _swim_enabled(),
        "scope": scope["scope"],
        "center": scope["center"] if internal else None,
        "radius_nm": scope["radius_nm"],
        "airports": scope["airports"],
        "bounds": scope.get("bounds"),
        "label": scope["label"],
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


# ── Focus identity resolution + aggregator fallback (2026-10-24) ─────────────
# Operator-reported map search bugs: "UA919" 404'd while "UAL919" hit (focus
# compared the raw input against ICAO callsigns), a tail / serial never
# resolved at all, and an aircraft outside receiver + SWIM coverage was
# invisible even when airplanes.live had it. The runner cannot import
# shared.watchlist / common.airplanes_live (its hash-locked image has no
# `requests`), so the pieces that need them live here and the runner calls
# them over the same token-injected path as /positions and /last-leg.
# Both routes are Tier 1+ only: a Tier-0 (public) caller gets a 404, which
# keeps every existing LADD rule for untrusted callers exactly as it was --
# nothing new is exposed publicly, not even a "listed" vs "unknown" signal.

_FOCUS_HEX_RE = re.compile(r"^[0-9A-F]{6}$")
_FOCUS_CALLSIGN_RE = re.compile(r"^[A-Z0-9]{2,8}$")
_FOCUS_AGG_MAX_CALLSIGNS = 2


def resolve_focus_ident(ident: str) -> dict:
    """Every identity the platform can derive for a free-text map search:
      hex          -- the ident itself when it is a Mode S hex, else the
                      FAA / OpenSky registry hit for it as a registration or
                      serial (shared.watchlist._local_registry_hex_lookup,
                      tried as typed and with dashes removed: "25-33000" and
                      "2533000", "Q8991S").
      callsigns    -- ordered candidates: raw, the ICAO operating form of an
                      IATA flight number (UA919 -> UAL919, common.airline_codes),
                      then the codeshare operating callsign
                      (db.resolve_operating_callsign, the watchlist's path).
      registration -- the registration/serial form that resolved, else None.
    Pure lookups, never raises: every source is best-effort."""
    raw = (ident or "").strip().upper()
    out: dict = {"ident": raw, "hex": None, "callsigns": [], "registration": None}
    if not raw:
        return out
    if _FOCUS_HEX_RE.match(raw):
        out["hex"] = raw.lower()
    callsigns: list[str] = []
    compact = raw.replace(" ", "").replace("-", "")
    if _FOCUS_CALLSIGN_RE.match(compact):
        callsigns.append(compact)
    try:
        icao = to_icao_callsign(compact)
    except Exception:  # noqa: BLE001
        icao = None
    if icao and icao not in callsigns:
        callsigns.append(icao)
    try:
        operating = db.resolve_operating_callsign(compact)
    except Exception as e:  # noqa: BLE001
        log.debug("swim resolve: codeshare lookup failed (non-fatal): %s", type(e).__name__)
        operating = None
    if operating and operating.upper() not in callsigns:
        callsigns.append(operating.upper())
    out["callsigns"] = callsigns
    if out["hex"] is None:
        for cand in dict.fromkeys((raw, compact)):
            try:
                h = _watchlist._local_registry_hex_lookup(cand)
            except Exception as e:  # noqa: BLE001
                log.debug("swim resolve: registry lookup failed (non-fatal): %s", type(e).__name__)
                h = None
            if h:
                out["hex"], out["registration"] = h.lower(), cand
                break
    return out


@router.get("/resolve")
def swim_resolve(
    ident: str = Query(..., min_length=2, max_length=16),
    tier: Tier = Depends(resolve_tier),
) -> JSONResponse:
    """Identity candidates for the runner's /api/map/focus (see
    resolve_focus_ident). Tier 1+ only; a Tier-0 caller sees a 404."""
    if not _is_internal(tier):
        raise HTTPException(status_code=404, detail="not found")
    return JSONResponse(resolve_focus_ident(ident))


def aggregator_lookup(hex_code: str | None, callsigns: list[str]) -> dict | None:
    """airplanes.live through the watchlist's fallback helpers ONLY
    (_remote_ac_by_hex / _remote_ac_by_callsign -> common.airplanes_live):
    they already honour AIRPLANES_LIVE_FALLBACK, the refusal backoff, the
    30 s cache and never send a LADD-listed identifier. Hex first (the
    stronger key), then at most _FOCUS_AGG_MAX_CALLSIGNS callsigns -- each
    miss costs one rate-limited call (MIN_INTERVAL_S), so the caller's
    timeout stays bounded. Returns the normalized contact or None."""
    ac = None
    if hex_code and _FOCUS_HEX_RE.match(hex_code.strip().upper()):
        ac = _watchlist._remote_ac_by_hex(hex_code.strip().lower())
    for cs in callsigns[:_FOCUS_AGG_MAX_CALLSIGNS]:
        if ac:
            break
        cs = (cs or "").strip().upper()
        if cs and _FOCUS_CALLSIGN_RE.match(cs):
            ac = _watchlist._remote_ac_by_callsign(cs)
    if not ac or ac.get("lat") is None or ac.get("lon") is None:
        return None
    return {k: ac.get(k) for k in ("hex", "r", "t", "flight", "lat", "lon", "alt_baro", "gs", "track", "seen_pos")}


@router.get("/aggregator")
def swim_aggregator(
    hex: str | None = Query(None, min_length=6, max_length=6),
    callsigns: str | None = Query(None, min_length=2, max_length=40),
    tier: Tier = Depends(resolve_tier),
) -> JSONResponse:
    """airplanes.live FALLBACK contact for the map focus -- the runner calls
    this only after the receiver and SWIM both had nothing live (operator
    standing rule: airplanes.live is fallback / cross-check, never a primary
    source). `callsigns` is a comma list, ICAO form, tried in order after the
    hex. 404 when the fallback is off, backed off, LADD-blocked or empty --
    one answer for all four, so nothing about the LADD list leaks. Tier 1+
    only. The demo runner never reaches this route (synthetic only)."""
    if not _is_internal(tier):
        raise HTTPException(status_code=404, detail="not found")
    cs_list = [c for c in (callsigns or "").upper().split(",") if c.strip()]
    if not hex and not cs_list:
        raise HTTPException(status_code=422, detail="hex or callsigns required")
    ac = aggregator_lookup(hex, cs_list)
    if ac is None:
        raise HTTPException(status_code=404, detail="no aggregator contact")
    return JSONResponse({"aggregator": "airplanes.live", "aircraft": ac})
