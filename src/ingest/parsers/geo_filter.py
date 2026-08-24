"""
ingest.parsers.geo_filter — shared geographic relevance filter for all SWIM and NWWS parsers.

Pass logic (any match = accept):
  1. Within 250NM of DCA (38.8522°N, 77.0373°W)
  2. Airport ICAO/IATA in CORE_AIRPORTS (30 major US airports)
  3. NWS WFO in CORE_WFOS (for NWWS/alert feeds)

Usage::

    from ingest.parsers.geo_filter import passes_geo_filter, is_core_airport, is_core_wfo

    # In a parser write function:
    if not passes_geo_filter(lat=lat, lon=lon, airport=origin):
        return  # discard out-of-region record
"""
from __future__ import annotations

import math

# ── DCA reference point ───────────────────────────────────────────────────────
_DCA_LAT = 38.8522
_DCA_LON = -77.0373
_RADIUS_NM = 250.0

# ── 30 core US airports — ICAO and IATA both accepted ────────────────────────
CORE_AIRPORTS: frozenset[str] = frozenset({
    # DC metro
    "KDCA", "DCA", "KIAD", "IAD", "KBWI", "BWI",
    # Northeast
    "KJFK", "JFK", "KLGA", "LGA", "KEWR", "EWR",
    "KBOS", "BOS", "KPHL", "PHL",
    # Midwest
    "KORD", "ORD", "KMDW", "MDW", "KMSP", "MSP",
    # South / Southeast
    "KATL", "ATL", "KMIA", "MIA", "KFLL", "FLL",
    "KMCO", "MCO", "KCLT", "CLT",
    # Texas
    "KDFW", "DFW", "KDAL", "DAL", "KIAH", "IAH", "KHOU", "HOU",
    # Mountain / West
    "KDEN", "DEN", "KSLC", "SLC", "KLAS", "LAS", "KPHX", "PHX",
    # West Coast
    "KLAX", "LAX", "KSFO", "SFO", "KSJC", "SJC",
    "KOAK", "OAK", "KSEA", "SEA", "KPDX", "PDX",
})

# ── NWS Weather Forecast Offices covering the core airport regions ─────────────
CORE_WFOS: frozenset[str] = frozenset({
    "LWX",   # DC / Baltimore / Philadelphia
    "OKX",   # New York City
    "BOX",   # Boston
    "PHI",   # Philadelphia  (correct code: PHI, not PHL)
    "LOT",   # Chicago
    "FWD",   # Dallas-Fort Worth
    "SJT",   # San Angelo / west Texas (DFW overflow region)
    "TAE",   # Tallahassee (Atlanta border region)
    "FFC",   # Atlanta
    "MFL",   # Miami
    "MLB",   # Melbourne FL (Orlando / MCO)
    "LIX",   # New Orleans / Houston border region
    "HGX",   # Houston
    "BOU",   # Denver
    "SLC",   # Salt Lake City
    "LOX",   # Los Angeles
    "MTR",   # San Francisco Bay Area
    "SEW",   # Seattle
    "PQR",   # Portland OR
    "VEF",   # Las Vegas
    "PSR",   # Phoenix
    "MPX",   # Minneapolis
    "GSP",   # Charlotte / Greenville
})


# ── Distance helpers ──────────────────────────────────────────────────────────

def _haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in nautical miles (Haversine formula)."""
    R = 3440.065  # Earth radius in NM
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


def distance_to_dca_nm(lat: float, lon: float) -> float:
    """Great-circle distance from the given point to DCA, in nautical miles."""
    return _haversine_nm(lat, lon, _DCA_LAT, _DCA_LON)


# ── Individual predicate functions ────────────────────────────────────────────

def in_range(lat: float | None, lon: float | None) -> bool:
    """True if the lat/lon is within 250 NM of DCA."""
    if lat is None or lon is None:
        return False
    return _haversine_nm(_DCA_LAT, _DCA_LON, lat, lon) <= _RADIUS_NM


def is_core_airport(code: str | None) -> bool:
    """True if the airport code (ICAO or IATA, with or without K prefix) is in the core 30 list."""
    if not code:
        return False
    return code.upper() in CORE_AIRPORTS


def is_core_wfo(wfo: str | None) -> bool:
    """
    True if the NWS WFO is in the core WFO list.
    Accepts both 3-letter (LWX) and 4-letter ICAO-style (KLWX) codes.
    """
    if not wfo:
        return False
    w = wfo.upper()
    # Strip leading K from 4-letter ICAO-style codes (e.g. KLWX → LWX)
    if len(w) == 4 and w.startswith("K"):
        w = w[1:]
    return w in CORE_WFOS


# ── Master gate ───────────────────────────────────────────────────────────────

def passes_geo_filter(
    lat: float | None = None,
    lon: float | None = None,
    airport: str | None = None,
    wfo: str | None = None,
) -> bool:
    """
    Master relevance gate.  Returns True if ANY condition is met:
      1. lat/lon within 250 NM of DCA
      2. airport code is in CORE_AIRPORTS
      3. WFO is in CORE_WFOS

    Call this from every parser's write/upsert function before committing to DB.
    VIP/POTUS overrides are the caller's responsibility and should be checked
    *before* calling this function.
    """
    return in_range(lat, lon) or is_core_airport(airport) or is_core_wfo(wfo)
