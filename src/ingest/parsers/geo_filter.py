"""
ingest.parsers.geo_filter — shared geographic relevance filter for all SWIM and NWWS parsers.

Pass logic (any match = accept):
  1. Within 250NM of DCA (38.8522°N, 77.0373°W)
  2. Airport ICAO/IATA in CORE_AIRPORTS (30 major US airports)
  3. NWS WFO in CORE_WFOS (for NWWS/alert feeds)

2026-10-24 (operator-approved): the SWIM half of this filter is now a
deployment SETTING, ``SWIM_STORAGE_SCOPE`` (dispatch.env, which every ingest,
web and runner container loads):

  regional  (default) -- exactly the three rules above; current behaviour.
  national            -- accept every SWIM record. The FAA SWIM feeds are
                         already NAS-scoped, so "national" means "store what
                         the subscription delivers"; nothing is widened on the
                         wire, only on disk (roughly 2-4x the FDPS write rate
                         of the regional filter on this hardware).

Only the SWIM rules (1 and 2: ``in_range``, ``passes_geo_filter`` and the
``storage_scope()`` the map reads) follow the setting. Rule 3 (``is_core_wfo``,
NWWS weather alerts) stays regional regardless: it gates NWS weather products,
not SWIM flight data, and NWWS has its own knob (``NWWS_WFO_FILTER``). The
pure membership predicates ``is_core_airport`` / ``is_core_wfo`` never change
meaning; parsers that want the scope-aware storage decision call
``passes_geo_filter``.

Usage::

    from ingest.parsers.geo_filter import passes_geo_filter, is_core_airport, is_core_wfo

    # In a parser write function:
    if not passes_geo_filter(lat=lat, lon=lon, airport=origin):
        return  # discard out-of-region record
"""
from __future__ import annotations

import logging
import math
import os

log = logging.getLogger(__name__)

# ── Storage scope setting (2026-10-24) ────────────────────────────────────────
SCOPE_REGIONAL = "regional"
SCOPE_NATIONAL = "national"
_SCOPE_ENV = "SWIM_STORAGE_SCOPE"
_VALID_SCOPES = frozenset({SCOPE_REGIONAL, SCOPE_NATIONAL})
_warned_bad_scope: set[str] = set()

# 2026-10-24: the NAS box the map uses under "national" -- CONUS plus Alaska,
# Hawaii, Puerto Rico / USVI and the near-shore oceanic sectors FAA SWIM
# actually carries. [[south, west], [north, east]]. 17.5 N clears PR/USVI
# (TJSJ 18.4 N); 72 N clears the North Slope (PABR 71.3 N); -179.5 keeps the
# box on one side of the antimeridian (the far Aleutians west of 180 are the
# only US territory left out, a deliberate trade so the map's pan limit stays
# a single rectangle); -60 E covers ZNY/ZBW oceanic entry fixes east of
# Nantucket and the PR sector. Guam (ZUA) is out of the box on purpose: a
# single rectangle spanning 144 E..60 W is the whole Pacific and the map's
# minimum zoom would collapse to a world view.
NAS_BOUNDS: tuple[tuple[float, float], tuple[float, float]] = ((17.5, -179.5), (72.0, -60.0))
NAS_LABEL = "NAS-wide (FAA SWIM)"


def swim_storage_scope() -> str:
    """The configured SWIM storage scope: "regional" (default) or "national".

    2026-10-24: read from the environment on every call (a dict lookup; the
    ingest containers get dispatch.env as env and there is no reload path, so
    a cached module constant would only add a restart-ordering trap and
    defeat test monkeypatching). An unknown value falls back to regional
    (fail toward LESS storage) and is logged once per distinct bad value.
    """
    raw = (os.environ.get(_SCOPE_ENV) or SCOPE_REGIONAL).strip().lower()
    if raw in _VALID_SCOPES:
        return raw
    if raw not in _warned_bad_scope:
        _warned_bad_scope.add(raw)
        log.warning("%s=%r is not one of %s; using %s",
                    _SCOPE_ENV, raw, sorted(_VALID_SCOPES), SCOPE_REGIONAL)
    return SCOPE_REGIONAL


def is_national_scope() -> bool:
    """True when SWIM_STORAGE_SCOPE=national (every SWIM record is stored)."""
    return swim_storage_scope() == SCOPE_NATIONAL

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


def distance_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between any two points, in nautical miles.

    2026-09-02: public wrapper around _haversine_nm added so callers outside
    this module (fdps_parser's meter-fix proximity alert) get one shared
    distance primitive instead of a second private copy -- distance_to_dca_nm
    is DCA-specific and doesn't cover the general two-point case.
    """
    return _haversine_nm(lat1, lon1, lat2, lon2)


# ── Individual predicate functions ────────────────────────────────────────────

def in_range(lat: float | None, lon: float | None) -> bool:
    """True if the lat/lon is within 250 NM of DCA.

    2026-10-24: under SWIM_STORAGE_SCOPE=national a KNOWN position anywhere is
    in range (the whole NAS is the storage area). A missing position is still
    False -- "national" widens the area, it does not invent a position, and
    the airport rule in passes_geo_filter is what carries position-less
    records (unchanged from regional)."""
    if lat is None or lon is None:
        return False
    if is_national_scope():
        return True
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

    2026-10-24: deliberately NOT scope-aware. SWIM_STORAGE_SCOPE widens SWIM
    flight-data storage; NWWS carries NWS weather alerts for the operator's
    region, has its own NWWS_WFO_FILTER setting, and a nationwide weather
    firehose would drown the alert channel. nwws.py calls this directly, so
    it stays regional under both scopes.
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

    2026-10-24: under SWIM_STORAGE_SCOPE=national this returns True for every
    record -- the SWIM feeds are already NAS-scoped, so "national" is "store
    what the subscription delivers". This is the ONE scope-aware gate; the
    TFMS and AIM write paths call it (airport-only form) instead of the bare
    is_core_airport membership test so they follow the setting too.
    """
    if is_national_scope():
        return True
    return in_range(lat, lon) or is_core_airport(airport) or is_core_wfo(wfo)


def storage_scope() -> dict:
    """2026-10-24 (map rework): the storage area this filter accepts, as data,
    so the dispatch API (/api/v1/swim/scope) and the runner map can derive
    their extent from the ONE constant set above instead of a second copy.

    Regional (default):
      {"scope": "regional", "center": [lat, lon], "radius_nm": 250.0,
       "airports": [ICAO...], "bounds": None, "label": "..."}
    National (SWIM_STORAGE_SCOPE=national, 2026-10-24): no circle -- center and
    radius_nm are None, bounds is the NAS box (NAS_BOUNDS) the map should
    span, and airports is still the core list (the map labels them):
      {"scope": "national", "center": None, "radius_nm": None,
       "airports": [ICAO...], "bounds": [[17.5,-179.5],[72.0,-60.0]],
       "label": "NAS-wide (FAA SWIM)"}
    """
    airports = sorted(a for a in CORE_AIRPORTS if len(a) == 4)
    if is_national_scope():
        return {
            "scope": SCOPE_NATIONAL,
            "center": None,
            "radius_nm": None,
            "airports": airports,
            "bounds": [list(NAS_BOUNDS[0]), list(NAS_BOUNDS[1])],
            "label": NAS_LABEL,
        }
    return {
        "scope": SCOPE_REGIONAL,
        "center": [_DCA_LAT, _DCA_LON],
        "radius_nm": float(_RADIUS_NM),
        "airports": airports,
        "bounds": None,
        "label": "SWIM storage filter: %d nm around KDCA + %d core airports" % (int(_RADIUS_NM), len(airports)),
    }
