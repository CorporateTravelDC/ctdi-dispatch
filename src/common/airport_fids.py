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

2026-10-18 (operator, gate batch): FIDS supplies gate, terminal and baggage
ONLY. It never supplies a time, a status or a tail: this module no longer
returns status / scheduled / estimated / remaining / tail from any lookup, and
the published time is read in exactly one place, match_leg(), to pick the row
that belongs to a SWIM/TFMS-verified leg inside that leg's own window. It is
never stored, logged or returned. A cached payload older than
FIDS_MAX_STALE_S is never served (lookups read "unknown" instead of an old
gate), and a failed fetch backs off FIDS_FAIL_BACKOFF_S per airport.
"""

import hashlib
import logging
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Optional

import requests

# 2026-09-27: FIDS date bug. lookup_arrival/lookup_departure default date_str
# to the current date, then filter arrivals by publishedTime.startswith(date_str).
# publishedTime is AIRPORT-LOCAL (ET) but the container clock is UTC, so a bare
# datetime.now() rolls to the next day's date after ~20:00 ET (00:00Z). Live
# confirmed on EDV5134/DAL5134 (2026-09-27 20:10 EDT): the lookup matched
# TOMORROW's scheduled instance (2026-09-28 18:47) of the daily flight instead
# of today's delayed one, which also masked the delay (estimated==scheduled on
# the wrong instance). Compute the reference date in airport-local time. All
# current airports (DCA/IAD/BWI) are Eastern; add an airport->tz map here if a
# non-ET airport is ever onboarded.
_AIRPORT_TZ = ZoneInfo("America/New_York")

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

# 2026-10-18 (Phase A finding #10): a failed fetch used to fall back to the
# cache WHATEVER its age, and run_for() then stamped the feed healthy -- so an
# MWAA outage read as "ok" while pushes and the API carried an hours-old gate.
# Past this cap the cache is not served at all.
import os as _os  # noqa: E402

FIDS_MAX_STALE_S = int(_os.environ.get("FIDS_MAX_STALE_S", "900") or 900)
# After a failed fetch, non-forced callers (the poller's per-watch FIDS check)
# do not try that airport again for this long: one outage costs at most one
# 10 s timeout per airport per minute, not one per DC watch per sweep.
FIDS_FAIL_BACKOFF_S = 60

# ---------------------------------------------------------------------------
# In-process cache (per-container; not shared across poller and web)
# ---------------------------------------------------------------------------

_cache: dict[str, dict] = {}  # airport -> {ts, data, payload_hash}
_last_fail: dict[str, float] = {}   # airport -> monotonic time of the last failed fetch
_last_error: dict[str, str] = {}    # airport -> error of the LAST fetch attempt (cleared on success)


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
        if not isinstance(data, dict):
            raise ValueError("payload is not an object")
        log.info(
            "airport_fids: %s -- %d arrivals, %d departures",
            airport,
            len(data.get("arrivals", [])),
            len(data.get("departures", [])),
        )
        _last_error.pop(airport.upper(), None)
        return data
    except requests.RequestException as exc:
        log.warning("airport_fids: %s fetch failed -- %s", airport, exc)
        _last_error[airport.upper()] = f"fetch failed: {type(exc).__name__}"
        return None
    except (ValueError, KeyError) as exc:
        log.warning("airport_fids: %s parse error -- %s", airport, exc)
        _last_error[airport.upper()] = f"parse error: {type(exc).__name__}"
        return None


def _usable_cache(airport: str) -> Optional[dict]:
    cached = _cache.get(airport)
    if cached and (_now() - cached["ts"]) <= FIDS_MAX_STALE_S:
        return cached["data"]
    return None


def get_data(airport: str, force: bool = False) -> Optional[dict]:
    """
    Return the FIDS payload, refreshing when the cache is older than 60 s.
    On a failed fetch: the cache if it is at most FIDS_MAX_STALE_S old, else
    None (2026-10-18 -- never an older payload). Non-forced callers skip the
    fetch for FIDS_FAIL_BACKOFF_S after a failure.
    """
    airport = airport.upper()
    cached = _cache.get(airport)
    if not force and cached and (_now() - cached["ts"]) < _CACHE_TTL:
        return cached["data"]
    if not force and airport in _last_fail and (_now() - _last_fail[airport]) < FIDS_FAIL_BACKOFF_S:
        return _usable_cache(airport)

    fresh = _fetch_raw(airport)
    if fresh is not None:
        raw_bytes = str(fresh).encode()
        _cache[airport] = {
            "ts":   _now(),
            "data": fresh,
            "hash": hashlib.sha256(raw_bytes).hexdigest()[:16],
        }
        _last_fail.pop(airport, None)
        return fresh

    _last_fail[airport] = _now()
    usable = _usable_cache(airport)
    if usable is not None:
        log.warning("airport_fids: %s using cache (age %.0fs, cap %ds)",
                    airport, _now() - cached["ts"], FIDS_MAX_STALE_S)
        return usable
    if cached:
        log.warning("airport_fids: %s cache older than %ds -- not served", airport, FIDS_MAX_STALE_S)
    return None


def last_fetch_error(airport: str) -> Optional[str]:
    """The error of the most recent fetch attempt for this airport, or None if it succeeded."""
    return _last_error.get(airport.upper())


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


def _published_epoch(flight: dict) -> Optional[float]:
    """The row's published time (naive airport-local ET) as an epoch -- used ONLY
    by match_leg() / the date filter below to pick a row. Never returned."""
    pub = flight.get("publishedTime")
    if not pub:
        return None
    try:
        return datetime.strptime(pub, "%Y-%m-%d %H:%M:%S").replace(tzinfo=_AIRPORT_TZ).timestamp()
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _pick_nearest_now(matches: list[dict]) -> dict:
    now = time.time()
    return min(matches, key=lambda f: abs((_published_epoch(f) or 0) - now))


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

    Returns normalized dict or None (2026-10-18: no status, scheduled,
    estimated, remaining or tail -- FIDS is gate/terminal/baggage only):
        {
            "airport":        "DCA",
            "iata":           "AA",
            "flight_number":  "1557",
            "gate":           "D38",
            "terminal":       "2",
            "baggage":        "11",
            "dep_airport":    "BOS",
            "dep_gate":       "B15",
            "dep_terminal":   "B",
        }
    Several rows for the number (a daily flight, the next day's instance):
    the one published nearest now. For a watched leg use match_leg().
    """
    if date_str is None:
        date_str = datetime.now(_AIRPORT_TZ).strftime("%Y-%m-%d")  # airport-local ET, not container-UTC

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

    flight = _pick_nearest_now(matches)

    return {
        "airport":       airport,
        "iata":          flight.get("IATA", "").upper(),
        "flight_number": str(flight.get("flightnumber", "")),
        "gate":          _effective_gate(flight),
        "terminal":      flight.get("arr_terminal"),
        "baggage":       _effective_claim(flight, airport),
        "dep_airport":   flight.get("dep_airport_code"),
        "dep_gate":      flight.get("dep_gate"),
        "dep_terminal":  flight.get("dep_terminal"),
    }


def lookup_departure(
    airport: str,
    iata: str,
    flight_number: str,
    date_str: Optional[str] = None,
) -> Optional[dict]:
    """
    Look up a departure by IATA carrier + flight number. Mirrors
    lookup_arrival() but reads data["departures"] -- verified live
    (2026-07-28) that this is NOT a symmetric schema with arrivals[]:
    departure records' aircraftInfo is a LIST of one dict (arrivals[]: a
    bare dict), and "gate"/"mod_gate" already mean the departure gate
    directly (arrivals[] uses those for the arrival gate and a separate
    dep_gate/dep_terminal pair for the origin side).

    Args:
        airport:       "DCA" or "IAD" -- the flight's ORIGIN, not destination
        iata:          IATA carrier code, e.g. "UA"
        flight_number: Numeric string, e.g. "6203"
        date_str:      "YYYY-MM-DD" -- defaults to today

    Returns normalized dict or None (2026-10-18: no status, scheduled,
    estimated or tail):
        {
            "airport":        "IAD",
            "iata":           "UA",
            "flight_number":  "6203",
            "gate":           "C20",
            "terminal":       "A",
            "arr_airport":    "IAD",
            "arr_gate":       "1",
            "arr_terminal":   None,
        }
    """
    if date_str is None:
        date_str = datetime.now(_AIRPORT_TZ).strftime("%Y-%m-%d")  # airport-local ET, not container-UTC

    airport = airport.upper()
    data = get_data(airport)
    if data is None:
        return None

    departures = data.get("departures", [])
    matches = [
        f for f in departures
        if f.get("IATA", "").upper() == iata.upper()
        and str(f.get("flightnumber", "")) == str(flight_number)
        and f.get("publishedTime", "").startswith(date_str)
    ]
    if not matches:
        matches = [
            f for f in departures
            if f.get("IATA", "").upper() == iata.upper()
            and str(f.get("flightnumber", "")) == str(flight_number)
        ]
    if not matches:
        return None

    flight = _pick_nearest_now(matches)

    return {
        "airport":       airport,
        "iata":          flight.get("IATA", "").upper(),
        "flight_number": str(flight.get("flightnumber", "")),
        "gate":          _effective_gate(flight),
        "terminal":      flight.get("dep_terminal"),
        "arr_airport":   flight.get("airportcode"),
        "arr_gate":      flight.get("arr_gate"),
        "arr_terminal":  flight.get("arr_terminal"),
    }


def match_leg(
    airport: str,
    side: str,
    iata: str,
    flight_number: str,
    other_airport: Optional[str],
    window: Optional[tuple[float, float]],
) -> Optional[dict]:
    """The FIDS row for ONE verified flight leg, gate/terminal/baggage only.

    side="arrival":   the ARRIVALS board of `airport` (the leg's destination);
                      the row's origin must not contradict `other_airport`.
    side="departure": the DEPARTURES board of `airport` (the leg's origin) --
                      2026-10-18 fix: the origin side used to read the
                      arrivals board for the flight number, i.e. another
                      leg's gate on an out-and-back; only the departure gate
                      is returned. The row's destination must not contradict
                      `other_airport`.
    window: (start, end) epoch bounds from the leg's own SWIM/TFMS times. A row
    matches only when its published time falls inside -- the one permitted use
    of a FIDS time (operator 2026-10-18); it is never returned. With no window
    (no verified times), only a route that is unique on the board matches
    (leg_match 'route_only'). None when no single row matches."""
    from common.flight_instance import airports_agree

    airport = airport.upper()
    data = get_data(airport)
    if data is None:
        return None
    board = data.get("arrivals" if side == "arrival" else "departures", [])
    route_key = "dep_airport_code" if side == "arrival" else "airportcode"
    rows = [
        f for f in board
        if f.get("IATA", "").upper() == iata.upper()
        and str(f.get("flightnumber", "")).lstrip("0") == str(flight_number).lstrip("0")
        and airports_agree(f.get(route_key), other_airport) is not False
    ]
    if window is not None:
        lo, hi = window
        rows = [f for f in rows if (ep := _published_epoch(f)) is not None and lo <= ep <= hi]
        leg_match = "route_watch"
    else:
        leg_match = "route_only"
    if len(rows) != 1:
        return None
    f = rows[0]
    if side == "arrival":
        return {"gate": _effective_gate(f), "terminal": f.get("arr_terminal"),
                "baggage": _effective_claim(f, airport), "leg_match": leg_match}
    return {"gate": _effective_gate(f), "leg_match": leg_match}
