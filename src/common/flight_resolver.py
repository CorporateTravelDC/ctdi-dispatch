"""
common/flight_resolver.py
--------------------------
Layered arrivals resolver for DCA / IAD / BWI:
  1. FAA SWIM (flight_events table) -- primary, all three hubs
  2. MWAA airport-website FIDS scrape -- fallback, DCA/IAD only
  3. FlightAware AeroAPI -- fallback, all three hubs (funded 2026-07-20)

Why AeroAPI is tier 3 for ALL hubs, not just BWI: SWIM has been 0 rows
platform-wide all session (see below), so DCA/IAD were silently running on
tier 2 (website) alone. AeroAPI now backstops all three regardless of
which upstream free source is having a bad day -- it doesn't matter
*why* SWIM/website came up empty, only that they did.

Background on the SWIM gap (2026-07-20): the ingest container has been
pulling live FAA SWIM data (stdds/fdps/itws/tfms/tbfm/fns) for days, but
flight_events, surface_tracks, terminal_tracks, and tbfm_sequences were
all still 0 rows as of this writing. Two suspected contributing causes,
partially addressed same day:
  - tbfm_parser.py's container-tag and leaf-tag matching were exact-match
    only and never validated against a real captured message -- broadened
    to case-insensitive/substring matching (still unconfirmed against a
    real raw sample; raw-prefix logging bumped DEBUG->INFO so the next
    ingest restart will show real tag names if still 0 sequences).
  - The TBFM Solace session logged 16 "keep-alive detected session down"
    warnings in one day with no confirmed reconnect -- may mean TBFM
    specifically just isn't receiving traffic most of the time,
    independent of parsing logic.
Both fdps_parser.py and smes_parser.py (stdds) looked more mature (recent
namespace-aware regression fixes), so flight_events/surface_tracks/
terminal_tracks being 0 may be legitimate geo-filter rejection at low
DC-area traffic volume rather than a parsing bug -- not yet distinguishable
without a restart + DEBUG capture.

BWI has no website fallback -- Baltimore/Washington Intl is operated by
the Maryland Aviation Administration, not MWAA. But KBWI is already in
ingest/parsers/geo_filter.py's CORE_AIRPORTS allowlist, so once SWIM is
flowing, BWI is covered for free there too -- AeroAPI mainly matters for
BWI *today*, and as a safety net for DCA/IAD on days SWIM+website both
have a bad day.

Union Station / Amtrak is NOT this module's job -- different data shape
entirely, already served by common/train tooling and GET /api/v1/amtrak.
"""

import logging
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

from common.airport_fids import (
    AIRPORTS as WEBSITE_AIRPORTS,
    get_data as website_get_data,
    _effective_gate,
    _effective_claim,
)

log = logging.getLogger("common.flight_resolver")

DB_PATH = "/var/lib/corporatetraveldc/corporatetraveldc.db"

# ICAO airport code per hub -- matches flight_events.destination (SWIM uses
# ICAO, not IATA) and AeroAPI's airport-code path parameter.
HUB_ICAO = {
    "DCA": "KDCA",
    "IAD": "KIAD",
    "BWI": "KBWI",
}

SUPPORTED_HUB_AIRPORTS = tuple(HUB_ICAO.keys())

# IATA carrier code -> ICAO 3-letter callsign prefix (FAA ACID format,
# e.g. flight_events.flight_id = "AAL123"). Covers US majors + regional
# feeders commonly seen at DCA/IAD/BWI. Extend as needed -- if a carrier
# isn't in this dict, the raw code is tried as-is against the ICAO prefix
# (works for a few 3-letter carrier codes but will usually just not match).
IATA_TO_ICAO_CARRIER = {
    "AA": "AAL", "UA": "UAL", "DL": "DAL", "WN": "SWA", "AS": "ASA",
    "B6": "JBU", "NK": "NKS", "F9": "FFT", "G4": "AAY", "HA": "HAL",
    "OO": "SKW", "MQ": "ENY", "9E": "EDV", "YX": "RPA", "OH": "JIA",
    "YV": "ASH", "QX": "QXE", "C5": "UCA",
}

# SWIM flight_events.status values considered still-inbound (schema
# comment says "active", "landed", "cancelled", etc. -- exact vocabulary
# not yet observed live since the table is empty; kept permissive).
FORWARD_LOOKING_SWIM_STATUSES = {"active", "scheduled", "airborne", "enroute", "en_route"}

FORWARD_LOOKING_WEBSITE_STATUSES = {"Scheduled", "InAir", "Delayed"}

# AeroAPI's free-text status field -- forward-looking values seen in
# practice (2026-07-20 live test): "Scheduled", "En Route", "En Route /
# Delayed", "Scheduled / Delayed". Excludes "Landed", "Cancelled",
# "Diverted" and similar terminal states. Substring-matched, not exact,
# since AeroAPI appends qualifiers like "/ Delayed" to the base state.
FORWARD_LOOKING_AEROAPI_STATUS_HINTS = ("scheduled", "en route", "en-route", "airborne")

FLIGHTAWARE_API_BASE = "https://aeroapi.flightaware.com/aeroapi"
FLIGHTAWARE_TIMEOUT = 15


def _aeroapi_key() -> Optional[str]:
    """
    Read FLIGHTAWARE_API_KEY from the environment (podman --env-file loads
    dispatch-secrets.env into the container's environment; see CLAUDE.md).
    Funded and wired in 2026-07-20 -- key lives at
    ~/.secrets/flightaware_aeroapi.key on the host, copied into
    dispatch-secrets.env by an operator action, never hardcoded here.
    """
    return os.environ.get("FLIGHTAWARE_API_KEY") or None


def _query_swim(airport: str, carriers: Optional[set], within_minutes: int) -> list[dict]:
    icao = HUB_ICAO.get(airport)
    if not icao:
        return []

    now = datetime.now(timezone.utc).timestamp()
    cutoff = now + within_minutes * 60

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT flight_id, airline, flight_num, origin, destination,
                   aircraft_type, arrival_time, status
            FROM flight_events
            WHERE destination = ?
              AND arrival_time BETWEEN ? AND ?
            ORDER BY arrival_time ASC
            """,
            (icao, now, cutoff),
        ).fetchall()
    finally:
        conn.close()

    icao_prefixes = None
    if carriers:
        icao_prefixes = {IATA_TO_ICAO_CARRIER.get(c, c) for c in carriers}

    results = []
    for r in rows:
        status = (r["status"] or "").lower()
        if status not in FORWARD_LOOKING_SWIM_STATUSES:
            continue
        flight_id = r["flight_id"] or ""
        if icao_prefixes and not any(flight_id.startswith(p) for p in icao_prefixes):
            continue
        results.append({
            "source":        "swim",
            "airport":       airport,
            "flight_id":     flight_id,
            "airline":       r["airline"],
            "flight_num":    r["flight_num"],
            "origin":        r["origin"],
            "status":        r["status"],
            "scheduled": (
                datetime.fromtimestamp(r["arrival_time"], tz=timezone.utc)
                .strftime("%Y-%m-%d %H:%M:%S")
                if r["arrival_time"] else None
            ),
            "gate":          None,  # flight_events carries no gate/terminal data
            "terminal":      None,
            "baggage_claim": None,
        })
    return results


def _query_website(airport: str, carriers: Optional[set], within_minutes: int) -> list[dict]:
    if airport not in WEBSITE_AIRPORTS:
        return []

    data = website_get_data(airport)
    if data is None:
        return []

    now = datetime.now()
    cutoff = now + timedelta(minutes=within_minutes)

    results = []
    for f in data.get("arrivals", []):
        iata = f.get("IATA")
        if carriers and iata not in carriers:
            continue
        status = f.get("status")
        if status not in FORWARD_LOOKING_WEBSITE_STATUSES:
            continue
        pub = f.get("publishedTime")
        if not pub:
            continue
        try:
            pub_dt = datetime.strptime(pub, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        if not (now <= pub_dt <= cutoff):
            continue
        results.append({
            "source":        "website",
            "airport":       airport,
            "flight_id":     None,
            "airline":       f.get("airline"),
            "flight_num":    f.get("flightnumber"),
            "carrier":       iata,
            "origin":        f.get("dep_airport_code"),
            "status":        status,
            "scheduled":     pub,
            "gate":          _effective_gate(f),
            "terminal":      f.get("arr_terminal"),
            "baggage_claim": _effective_claim(f, airport),
        })
    return results


def _query_aeroapi(airport: str, carriers: Optional[set], within_minutes: int) -> list[dict]:
    """
    FlightAware AeroAPI scheduled_arrivals for one hub. Paid/metered --
    only called when SWIM and the website fallback both came up empty, per
    the waterfall in resolve_arrivals(). Response schema confirmed live
    2026-07-20 against KDCA: {"scheduled_arrivals": [{ident, ident_icao,
    ident_iata, operator, operator_iata, flight_number, codeshares_iata,
    cancelled, origin: {code_iata,...}, destination: {...}, scheduled_in,
    estimated_in, actual_in, status, ...}]}.
    """
    api_key = _aeroapi_key()
    if not api_key:
        return []

    icao = HUB_ICAO.get(airport)
    if not icao:
        return []

    try:
        resp = requests.get(
            f"{FLIGHTAWARE_API_BASE}/airports/{icao}/flights/scheduled_arrivals",
            headers={"x-apikey": api_key, "Accept": "application/json"},
            params={"max_pages": 1},
            timeout=FLIGHTAWARE_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        log.warning("flight_resolver: aeroapi fetch failed for %s: %s", airport, exc)
        return []
    except ValueError as exc:
        log.warning("flight_resolver: aeroapi parse error for %s: %s", airport, exc)
        return []

    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(minutes=within_minutes)

    results = []
    for f in data.get("scheduled_arrivals", []):
        if f.get("cancelled") or f.get("diverted"):
            continue

        iata = (f.get("operator_iata") or "").upper() or None
        codeshare_iatas = {
            (c or "")[:2].upper() for c in (f.get("codeshares_iata") or [])
        }
        if carriers:
            match_set = ({iata} if iata else set()) | codeshare_iatas
            if not (match_set & carriers):
                continue

        sched_raw = f.get("estimated_in") or f.get("scheduled_in")
        if not sched_raw:
            continue
        try:
            sched_dt = datetime.strptime(sched_raw, "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            continue
        if not (now <= sched_dt <= cutoff):
            continue

        status = f.get("status") or ""
        if not any(hint in status.lower() for hint in FORWARD_LOOKING_AEROAPI_STATUS_HINTS):
            continue

        origin_info = f.get("origin") or {}
        results.append({
            "source":        "aeroapi",
            "airport":       airport,
            "flight_id":     f.get("ident_icao"),
            "airline":       f.get("operator"),
            "flight_num":    f.get("flight_number"),
            "carrier":       iata,
            "origin":        origin_info.get("code_iata") or origin_info.get("code"),
            "status":        status,
            "scheduled":     sched_dt.strftime("%Y-%m-%d %H:%M:%S"),
            "gate":          None,   # AeroAPI's free tier doesn't return gate/terminal
            "terminal":      None,
            "baggage_claim": None,
        })
    return results


def resolve_arrivals(airport: str, carriers: Optional[list], within_minutes: int = 90) -> dict:
    """
    Layered resolver: SWIM (flight_events) -> MWAA website (DCA/IAD only)
    -> FlightAware AeroAPI (all three hubs). Each tier is only queried if
    the previous one returned nothing, so a healthy SWIM feed means
    AeroAPI (metered/paid) never gets called.

    Returns:
        {
          "airport":     "DCA",
          "source_used": "swim" | "website" | "aeroapi" | "none",
          "results":     [...],
          "note":        str or None,
        }
    """
    airport = airport.upper()
    carriers_set = {c.upper() for c in carriers} if carriers else None

    swim_results = _query_swim(airport, carriers_set, within_minutes)
    if swim_results:
        return {
            "airport":     airport,
            "source_used": "swim",
            "results":     swim_results,
            "note":        None,
        }

    website_results = _query_website(airport, carriers_set, within_minutes)
    if website_results:
        return {
            "airport":     airport,
            "source_used": "website",
            "results":     website_results,
            "note": (
                "SWIM (flight_events) had no matching rows -- served from the "
                "MWAA website fallback. Root-caused 2026-08-07 (this note was "
                "previously wrong -- corrected here rather than left stale): "
                "flight_events IS actively receiving fresh FDPS writes (confirmed "
                "live, ~6,400 rows/hour) -- ingest connectivity itself is fine. "
                "The real cause is that ingest/parsers/fdps_parser.py's "
                "write_flight_event() hardcodes arrival_time=None on every write "
                "(confirmed: 0 of 579k+ rows have ever had a non-null "
                "arrival_time, going back to the earliest data on 2026-07-20) -- "
                "FDPS position/status messages don't carry an arrival-time "
                "estimate natively, and no enrichment step (e.g. joining "
                "tbfm_sequences.eta by flight_id, which IS populated) was ever "
                "built to fill it in. Since this function's SWIM query filters "
                "on arrival_time BETWEEN now AND cutoff, it can structurally "
                "never match a row -- this is the expected/permanent path for "
                "DCA/IAD until arrival_time enrichment is built, not a transient "
                "connectivity issue."
            ),
        }

    aeroapi_results = _query_aeroapi(airport, carriers_set, within_minutes)
    if aeroapi_results:
        return {
            "airport":     airport,
            "source_used": "aeroapi",
            "results":     aeroapi_results,
            "note": (
                "Neither SWIM nor the MWAA website fallback had matching rows "
                "-- served from FlightAware AeroAPI (paid/metered; wired "
                f"2026-07-20). No gate/terminal/baggage data at this tier."
                + ("" if airport in WEBSITE_AIRPORTS else
                   " This is BWI's only data path -- MWAA doesn't operate it.")
            ),
        }

    if not _aeroapi_key():
        note = (
            "No matching flights from SWIM or the website fallback, and "
            "FlightAware AeroAPI is not queryable right now (no key loaded)."
        )
    else:
        note = "No matching flights from SWIM, the website fallback, or AeroAPI."
    return {
        "airport":     airport,
        "source_used": "none",
        "results":     [],
        "note":        note,
    }
