"""
common/airport_fids.py
----------------------
MWAA FIDS client for DCA (Reagan National) and IAD (Dulles).

Shared module -- imported by both the web container (on-demand route)
and the poller container (60s feed-health heartbeat).

Discovery notes (2026-06-24):
  DCA: https://www.flyreagan.com/arrivals-and-departures/json
  IAD: https://www.flydulles.com/arrivals-and-departures/json
       NOTE: NOT /flydulles/arrivals-and-departures/json.  The /flydulles/
       prefix in the JS only fires on shared-domain embeds; on flydulles.com
       the path stays plain.  Confirmed 2026-06-24.
  Auth: Cookie: flight-info=1  (JS-set; static value; no session required)
  DCA CDN TTL: max-age=60, public  -- do not poll faster than 60s
  IAD CDN TTL: no-cache, private   -- can poll freely; we self-limit to 60s
  Payload: {"arrivals": [...], "departures": [...]}  ~2.2 MB DCA / ~1.5 MB IAD

Key fields per arrival:
  IATA, flightnumber, airportcode, dep_airport_code,
  status, gate, mod_gate, arr_terminal, dep_terminal, dep_gate,
  baggage, claim, claim1, claim2, claim3,
  publishedTime, actualtime, mwaaTime,
  aircraftInfo.tail_number,
  arrivalInfo[0].remaining_time,
  arrivalInfo[0].position  (JSON str: Altitude/Speed/Heading)

Status values: Scheduled, InAir, InGate, Landed, OutGate, Delayed,
               Cancelled, Departed, In Customs
"""

import hashlib
import logging
import time
from datetime import datetime, timezone
from typing import Optional

import requests

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

AIRPORTS = {
    "DCA": {
        "url":     "https://www.flyreagan.com/arrivals-and-departures/json",
        "referer": "https://www.flyreagan.com/arrivals-and-departures",
        "icao":    "KDCA",
    },
    "IAD": {
        "url":     "https://www.flydulles.com/arrivals-and-departures/json",
        "referer": "https://www.flydulles.com/arrivals-and-departures",
        "icao":    "KIAD",
    },
}

# ICAO -> airport code reverse map for convenience
ICAO_TO_AIRPORT = {v["icao"]: k for k, v in AIRPORTS.items()}

_UA = "Mozilla/5.0 (Linux; Android 11) AppleWebKit/537.36"
_COOKIE = "flight-info=1"
_TIMEOUT = 10
_CACHE_TTL = 60  # seconds -- matches DCA CDN max-age

# ---------------------------------------------------------------------------
# In-process cache (per-container; not shared across poller and web)
# ---------------------------------------------------------------------------

_cache: dict[str, dict] = {}  # airport -> {ts, data, payload_hash}


def _now() -> float:
    return time.monotonic()


def _fetch_raw(airport: str) -> Optional[dict]:
    cfg = AIRPORTS.get(airport.upper())
    if not cfg:
        log.error("airport_fids: unknown airport %r", airport)
        return None
    headers = {
        "User-Agent": _UA,
        "Accept": "application/json",
        "Referer": cfg["referer"],
        "Cookie": _COOKIE,
    }
    try:
        resp = requests.get(cfg["url"], headers=headers, timeout=_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        log.info(
            "airport_fids: %s -- %d arrivals, %d departures",
            airport,
            len(data.get("arrivals", [])),
            len(data.get("departures", [])),
        )
        return data
    except requests.RequestException as exc:
        log.warning("airport_fids: %s fetch failed -- %s", airport, exc)
        return None
    except (ValueError, KeyError) as exc:
        log.warning("airport_fids: %s parse error -- %s", airport, exc)
        return None


def get_data(airport: str, force: bool = False) -> Optional[dict]:
    """
    Return cached FIDS payload, refreshing if stale (> 60s).
    Returns stale cache on failure rather than None.
    """
    airport = airport.upper()
    cached = _cache.get(airport)
    if not force and cached and (_now() - cached["ts"]) < _CACHE_TTL:
        return cached["data"]

    fresh = _fetch_raw(airport)
    if fresh is not None:
        raw_bytes = str(fresh).encode()
        _cache[airport] = {
            "ts":   _now(),
            "data": fresh,
            "hash": hashlib.sha256(raw_bytes).hexdigest()[:16],
        }
        return fresh

    if cached:
        log.warning(
            "airport_fids: %s using stale cache (age %.0fs)",
            airport,
            _now() - cached["ts"],
        )
        return cached["data"]

    return None


def get_payload_hash(airport: str) -> Optional[str]:
    return (_cache.get(airport.upper()) or {}).get("hash")


# ---------------------------------------------------------------------------
# IAD carousel remap (MWAA Twig transformBaggageClaim logic)
# IDs 16-21 and single letters -> remap to 15
# ---------------------------------------------------------------------------

def _iad_remap(value: Optional[str]) -> Optional[str]:
    if not value or value == "NULL":
        return None
    v = str(value).strip()
    if v.isalpha() and len(v) == 1:
        return "15"
    try:
        n = int(v)
        return "15" if 16 <= n <= 21 else v
    except ValueError:
        return v


def _effective_claim(flight: dict, airport: str) -> Optional[str]:
    remap = _iad_remap if airport.upper() == "IAD" else (lambda x: x)
    claims = [
        remap(flight.get("claim1")),
        remap(flight.get("claim2")),
        remap(flight.get("claim3")),
    ]
    multi = ", ".join(c for c in claims if c and c != "NULL")
    if multi:
        return multi
    raw = flight.get("baggage") or flight.get("claim")
    remapped = remap(raw)
    return remapped if remapped and remapped != "NULL" else None


def _effective_gate(flight: dict) -> Optional[str]:
    g = flight.get("mod_gate") or flight.get("gate")
    return str(g).strip().strip("-") if g and g != "NULL" else None


def _effective_status(flight: dict) -> Optional[str]:
    return flight.get("mod_status") or flight.get("status")


def _effective_time(flight: dict) -> Optional[str]:
    return (
        flight.get("mwaaTime")
        or flight.get("actualtime")
        or flight.get("publishedTime")
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

STATUS_PRIORITY = {
    "InGate": 0, "Landed": 1, "In Customs": 2, "InAir": 3,
    "OutGate": 4, "Delayed": 5, "Scheduled": 6, "Cancelled": 7,
}


def lookup_arrival(
    airport: str,
    iata: str,
    flight_number: str,
    date_str: Optional[str] = None,
) -> Optional[dict]:
    """
    Look up an arrival by IATA carrier + flight number.

    Args:
        airport:       "DCA" or "IAD"
        iata:          IATA carrier code, e.g. "AA"
        flight_number: Numeric string, e.g. "1557"
        date_str:      "YYYY-MM-DD" -- defaults to today

    Returns normalized dict or None:
        {
            "airport":        "DCA",
            "iata":           "AA",
            "flight_number":  "1557",
            "status":         "InAir",
            "gate":           "D38",
            "terminal":       "2",
            "baggage":        "11",
            "scheduled":      "2026-06-24 19:14:00",
            "estimated":      "2026-06-24 19:09:00",
            "remaining":      "00:38:46",
            "tail":           "N750UW",
            "dep_airport":    "BOS",
            "dep_gate":       "B15",
            "dep_terminal":   "B",
        }
    """
    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")

    airport = airport.upper()
    data = get_data(airport)
    if data is None:
        return None

    arrivals = data.get("arrivals", [])
    matches = [
        f for f in arrivals
        if f.get("IATA", "").upper() == iata.upper()
        and str(f.get("flightnumber", "")) == str(flight_number)
        and f.get("publishedTime", "").startswith(date_str)
    ]
    if not matches:
        # Fallback: no date filter (handles late-night crossovers)
        matches = [
            f for f in arrivals
            if f.get("IATA", "").upper() == iata.upper()
            and str(f.get("flightnumber", "")) == str(flight_number)
        ]
    if not matches:
        return None

    flight = sorted(
        matches,
        key=lambda f: STATUS_PRIORITY.get(_effective_status(f) or "", 99),
    )[0]

    arrival_info = (flight.get("arrivalInfo") or [{}])[0]

    return {
        "airport":       airport,
        "iata":          flight.get("IATA", "").upper(),
        "flight_number": str(flight.get("flightnumber", "")),
        "status":        _effective_status(flight),
        "gate":          _effective_gate(flight),
        "terminal":      flight.get("arr_terminal"),
        "baggage":       _effective_claim(flight, airport),
        "scheduled":     flight.get("publishedTime"),
        "estimated":     _effective_time(flight),
        "remaining":     arrival_info.get("remaining_time"),
        "tail":          (flight.get("aircraftInfo") or {}).get("tail_number"),
        "dep_airport":   flight.get("dep_airport_code"),
        "dep_gate":      flight.get("dep_gate"),
        "dep_terminal":  flight.get("dep_terminal"),
    }
