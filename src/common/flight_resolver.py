"""
common/flight_resolver.py
--------------------------
Forward arrivals resolver for DCA / IAD / BWI:
  1. FAA SWIM (flight_events table) -- the only tier, all three hubs
  ~~2. MWAA airport-website FIDS scrape -- fallback, DCA/IAD only, gate /
     terminal / baggage ONLY (2026-10-18)~~ (removed 2026-10-19, operator,
     Gate-1b: the FIDS published time no longer selects anything for the
     forward window, so a FIDS-only arrival has no time to be placed in a
     window by and drops out of the forward list. SWIM-timed arrivals stay,
     with their SWIM times. For one flight's gate / terminal / baggage from
     every source use GET /api/v1/flights/{ident}/assignments.)
  ~~3. FlightAware AeroAPI -- fallback, all three hubs (funded 2026-07-20)~~
     (removed 2026-10-18, operator: AeroAPI is called only as the budgeted,
     ledgered per-watched-leg gate/baggage fallback in common.gate_aeroapi --
     never automatically from a board lookup)

~~2026-10-18 (operator, gate batch): the website tier no longer serves the FIDS
`status` or `scheduled` (FIDS never supplies a time or a status), no longer
filters on the FIDS status, and the forward window is computed in airport-local
time: it compared a naive container-UTC datetime.now() with the naive ET
publishedTime, so the window sat 4-5 h off. The published time is used only to
pick rows inside the window; it is never returned.~~ (superseded 2026-10-19:
the website tier is gone from the forward list; the published time is not read
here at all.)

The history below is kept as written.

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
from datetime import datetime, timezone
from typing import Optional

from common import db as _db

# 2026-10-19 (operator, Gate-1b): no common.airport_fids import -- the FIDS
# published time is not used for forward-window selection, and the website
# tier (_query_website) that read it is removed.

log = logging.getLogger("common.flight_resolver")

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
# 2026-10-03: table moved to common.airline_codes (single source; three
# divergent copies existed). Name kept for callers.
from common.airline_codes import IATA_TO_ICAO as IATA_TO_ICAO_CARRIER  # noqa: E402

# SWIM flight_events.status values considered still-inbound (schema
# comment says "active", "landed", "cancelled", etc. -- exact vocabulary
# not yet observed live since the table is empty; kept permissive).
FORWARD_LOOKING_SWIM_STATUSES = {"active", "scheduled", "airborne", "enroute", "en_route"}

# 2026-10-18: FORWARD_LOOKING_WEBSITE_STATUSES (a filter on the FIDS status) and
# the AeroAPI tier's constants/_aeroapi_key() were removed with that tier; the
# AeroAPI key is read only by common.gate_aeroapi.


def _query_swim(airport: str, carriers: Optional[set], within_minutes: int) -> list[dict]:
    icao = HUB_ICAO.get(airport)
    if not icao:
        return []

    now = datetime.now(timezone.utc).timestamp()
    cutoff = now + within_minutes * 60

    # Routed through common.db.conn() (Postgres migration Phase 1, see
    # docs/POSTGRES_MIGRATION.md §4) instead of its own raw sqlite3.connect()
    # -- this way it automatically follows DISPATCH_DB_BACKEND like every
    # other flight_events reader instead of always hitting SQLite.
    with _db.conn() as conn:
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


def resolve_arrivals(airport: str, carriers: Optional[list], within_minutes: int = 90) -> dict:
    """
    Forward arrivals from SWIM (flight_events), timed by SWIM only.
    ~~-> MWAA website (DCA/IAD only, gate/terminal/baggage only)~~ (removed
    2026-10-19, operator: the FIDS published time never selects a forward-window
    row; FIDS-only arrivals drop out.) ~~-> FlightAware AeroAPI (all three
    hubs)~~ (removed 2026-10-18: AeroAPI is the per-watched-leg budgeted
    fallback in common.gate_aeroapi, never called from here).

    Returns:
        {
          "airport":     "DCA",
          "source_used": "swim" | "none",
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

    note = (
        "No SWIM-timed arrivals in the window. The MWAA FIDS board is not a "
        "time source and does not place flights in a forward window "
        "(2026-10-19), and AeroAPI is not queried from board lookups "
        "(2026-10-18: per-watched-leg gate/baggage fallback only, budgeted -- "
        "common.gate_aeroapi). For one flight's gate / terminal / baggage use "
        "GET /api/v1/flights/{ident}/assignments."
    )
    return {
        "airport":     airport,
        "source_used": "none",
        "results":     [],
        "note":        note,
    }
