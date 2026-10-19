"""
common/flight_resolver.py
--------------------------
Layered arrivals resolver for DCA / IAD / BWI:
  1. FAA SWIM (flight_events table) -- primary, all three hubs
  2. MWAA airport-website FIDS scrape -- fallback, DCA/IAD only, gate /
     terminal / baggage ONLY (2026-10-18)
  ~~3. FlightAware AeroAPI -- fallback, all three hubs (funded 2026-07-20)~~
     (removed 2026-10-18, operator: AeroAPI is called only as the budgeted,
     ledgered per-watched-leg gate/baggage fallback in common.gate_aeroapi --
     never automatically from a board lookup)

2026-10-18 (operator, gate batch): the website tier no longer serves the FIDS
`status` or `scheduled` (FIDS never supplies a time or a status), no longer
filters on the FIDS status, and the forward window is computed in airport-local
time: it compared a naive container-UTC datetime.now() with the naive ET
publishedTime, so the window sat 4-5 h off. The published time is used only to
pick rows inside the window; it is never returned.

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
from common.airport_fids import (
    AIRPORTS as WEBSITE_AIRPORTS,
    get_data as website_get_data,
    _effective_gate,
    _effective_claim,
    _published_epoch,
)

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


def _query_website(airport: str, carriers: Optional[set], within_minutes: int) -> list[dict]:
    if airport not in WEBSITE_AIRPORTS:
        return []

    data = website_get_data(airport)
    if data is None:
        return []

    now_ep = datetime.now(timezone.utc).timestamp()
    cutoff_ep = now_ep + within_minutes * 60

    picked = []
    for f in data.get("arrivals", []):
        iata = f.get("IATA")
        if carriers and iata not in carriers:
            continue
        pub_ep = _published_epoch(f)          # ET-aware; selection only, never returned
        if pub_ep is None or not (now_ep <= pub_ep <= cutoff_ep):
            continue
        picked.append((pub_ep, {
            "source":        "website",
            "airport":       airport,
            "flight_id":     None,
            "airline":       f.get("airline"),
            "flight_num":    f.get("flightnumber"),
            "carrier":       iata,
            "origin":        f.get("dep_airport_code"),
            "gate":          _effective_gate(f),
            "terminal":      f.get("arr_terminal"),
            "baggage_claim": _effective_claim(f, airport),
        }))
    picked.sort(key=lambda t: t[0])
    return [r for _ep, r in picked]


def resolve_arrivals(airport: str, carriers: Optional[list], within_minutes: int = 90) -> dict:
    """
    Layered resolver: SWIM (flight_events) -> MWAA website (DCA/IAD only,
    gate/terminal/baggage only). ~~-> FlightAware AeroAPI (all three hubs)~~
    (removed 2026-10-18: AeroAPI is the per-watched-leg budgeted fallback in
    common.gate_aeroapi, never called from here).

    Returns:
        {
          "airport":     "DCA",
          "source_used": "swim" | "website" | "none",
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
                "MWAA website fallback: gate, terminal and baggage only (FIDS "
                "never supplies a time or a status, 2026-10-18). Root-caused 2026-08-07 (this note was "
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

    note = (
        "No matching flights from SWIM or the website fallback. AeroAPI is not "
        "queried from board lookups (2026-10-18: per-watched-leg gate/baggage "
        "fallback only, budgeted -- common.gate_aeroapi)."
    )
    return {
        "airport":     airport,
        "source_used": "none",
        "results":     [],
        "note":        note,
    }
