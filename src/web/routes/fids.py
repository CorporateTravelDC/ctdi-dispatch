"""
web/routes/fids.py
------------------
Airport FIDS routes -- gate, baggage carousel, and arrival status.

GET /api/v1/fids/{airport}           -- snapshot (arrivals/departures counts)
GET /api/v1/fids/{airport}/arrivals  -- layered SWIM+website arrivals lookup
GET /api/v1/fids/{airport}/{flight}  -- single flight lookup, e.g. AA1557

Snapshot + single-flight routes: DCA, IAD only (website scrape).
Layered arrivals route: DCA, IAD, BWI (SWIM primary, website fallback
for DCA/IAD -- see common/flight_resolver.py for why BWI is SWIM-only).
Tier 0 -- no auth required (same as /api/v1/weather).

NOTE ON ROUTE ORDER: /{airport}/arrivals must stay registered before
/{airport}/{flight} below -- FastAPI/Starlette matches path routes in
registration order, and "/DCA/arrivals" would otherwise be swallowed by
the single-flight route (treating "arrivals" as a flight number) since
both patterns match the same path shape.
"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

from common.airport_fids import AIRPORTS, get_data, lookup_arrival
from common.flight_resolver import resolve_arrivals, SUPPORTED_HUB_AIRPORTS

router = APIRouter(prefix="/api/v1/fids", tags=["fids"])


def _validate(airport: str) -> str:
    a = airport.upper()
    if a not in AIRPORTS:
        raise HTTPException(
            status_code=400,
            detail=f"airport must be one of: {', '.join(sorted(AIRPORTS))}",
        )
    return a


@router.get("/{airport}")
def fids_snapshot(airport: str) -> JSONResponse:
    """
    Feed health snapshot for an airport -- Tier 0.
    Returns arrival/departure counts and cache freshness.
    """
    airport = _validate(airport)
    data = get_data(airport)
    if data is None:
        raise HTTPException(status_code=503, detail=f"{airport} FIDS unavailable")
    return JSONResponse({
        "airport":          airport,
        "arrivals_count":   len(data.get("arrivals", [])),
        "departures_count": len(data.get("departures", [])),
        "ts":               datetime.utcnow().isoformat() + "Z",
    })


@router.get("/{airport}/arrivals")
def fids_arrivals(
    airport: str,
    carriers: Optional[str] = Query(
        default=None,
        description="Comma-separated IATA carrier codes, e.g. AA,UA (default: all carriers)",
    ),
    within_minutes: int = Query(
        default=90,
        ge=1,
        le=720,
        description="Forward-looking window in minutes",
    ),
) -> JSONResponse:
    """
    Layered arrivals lookup -- Tier 0.

    Primary source: FAA SWIM (flight_events table, ingest-populated).
    Fallback: MWAA airport-website FIDS scrape (DCA/IAD only -- BWI is
    not MWAA-operated and has no equivalent free feed; see
    common/flight_resolver.py for the reasoning and the AeroAPI option).

    Response includes "source_used" (swim/website/none) and a "note"
    explaining why, so callers can tell a genuinely-empty window apart
    from a missing-source situation.
    """
    a = airport.upper()
    if a not in SUPPORTED_HUB_AIRPORTS:
        raise HTTPException(
            status_code=400,
            detail=f"airport must be one of: {', '.join(SUPPORTED_HUB_AIRPORTS)}",
        )
    carrier_list = [c.strip() for c in carriers.split(",") if c.strip()] if carriers else None
    result = resolve_arrivals(a, carrier_list, within_minutes)
    return JSONResponse(result)


@router.get("/{airport}/{flight}")
def fids_flight(
    airport: str,
    flight: str,
    date: Optional[str] = Query(
        default=None,
        description="Date filter YYYY-MM-DD (default: today)",
        pattern=r"^\d{4}-\d{2}-\d{2}$",
    ),
) -> JSONResponse:
    """
    Gate + baggage carousel + status for a specific arrival -- Tier 0.

    flight = IATA carrier code + flight number, e.g. AA1557, UA928, DL404.

    Returns:
        airport, iata, flight_number, status, gate, terminal,
        baggage (carousel), scheduled, estimated, remaining,
        tail, dep_airport, dep_gate, dep_terminal
    """
    airport = _validate(airport)
    flight = flight.upper().strip()

    # Split alpha prefix from numeric suffix
    iata = ""
    number = ""
    for i, ch in enumerate(flight):
        if ch.isdigit():
            iata = flight[:i]
            number = flight[i:]
            break

    if not iata or not number:
        raise HTTPException(
            status_code=400,
            detail="flight must be carrier + number, e.g. AA1557 or UA928",
        )

    result = lookup_arrival(
        airport=airport,
        iata=iata,
        flight_number=number,
        date_str=date,
    )

    if result is None:
        raise HTTPException(
            status_code=404,
            detail=f"{flight} not found in {airport} FIDS"
                   + (f" for {date}" if date else ""),
        )

    return JSONResponse(result)
