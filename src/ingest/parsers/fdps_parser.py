"""
ingest.parsers.fdps_parser — SFDPS FIXM XML parser for FAA NMS/FDPS feed.

--- SCHEMA VERSION SPLIT, 2026-07-20, IMPLEMENTED SAME DAY (3pm session) ---
The parser was originally written against FIXM 4.2 namespaces, but the
LIVE feed is actually FIXM 3.0 -- a different major schema version with a
different message model (MessageCollection > message[FlightMessageType] >
flight[...] > departure/arrival/enRoute/..., not the FH/TH/CL/HP/OH
source-code model the 4.2 parser used). 25 real FIXM 3.0 samples were
captured and used to derive the real field mapping; see fdps_debug_fixm30/
for the raw captures, and _parse_fdps_message_fixm30's own docstring below
for the full derivation notes (root wrapper shape, airport code paths,
squawk path, aircraft type/registration block).

The working 4.2 logic is kept intact as _parse_fdps_message_fixm42_legacy
(in case FAA ever reverts or another feed still emits 4.2); a cheap
namespace-string sniff (_detect_fixm_version) routes each message to the
matching parser, 3.0 checked first since it's what's actually live. Both
parsers return the same normalized dict shape, so nothing downstream
needs version awareness.

Parses, on the live FIXM 3.0 path: FH (full flight plan), TH (track
position), CL (cancellation), HP/OH (handoff), HZ (heartbeat), plus
AH/BA/LH/HX (accepted into the source allowlist, no special handling
beyond generic field extraction -- seen live, multiple times each, not
originally anticipated). registration (tail number) is a bonus field the
4.2 parser never had; write_flight_event/db.upsert_flight_event don't
have a column for it yet, so it's currently write-then-ignored downstream
-- flagged as a possible schema addition, not required for parity.

Marine One / POTUS detection: fires swim_alert and ntfy for POTUS callsigns
within 50nm of DCA. Version-agnostic -- operates on the normalized dict
either parser produces.
"""
from __future__ import annotations

import logging
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from typing import Any

from common import db
from common.push_dedup import PushDedup, content_hash
from ingest.parsers.geo_filter import (
    passes_geo_filter,
    is_core_airport,
    _haversine_nm,
    distance_to_dca_nm,
)

_FDPS_PROX_DEDUP = PushDedup("fdps_prox", dedup_secs=600)

log = logging.getLogger("ingest.parsers.fdps")

# One-shot full-message debug capture -- 2026-07-20, same technique that
# confirmed tbfm_parser.py's and smes_parser.py's real schemas (both were
# 0-rows-all-session with hand-guessed tag names that turned out wrong).
# flight_events is still 0 rows after those two fixes landed -- this parser
# looks more mature already (real FIXM namespace matching, a documented
# 2026-07-19 regression fix for Element-truthiness on ssrCode/modeACode),
# so the 0-rows outcome here might be legitimate low DC-area match volume
# on a nationwide feed rather than a tag-name bug -- but that's a guess
# until a sample is actually captured and _in_dc_area's pass rate is
# checked against it. Self-limited to _DEBUG_SAMPLE_MAX, then stops.
_DEBUG_SAMPLE_DIR = "/var/lib/corporatetraveldc/fdps_debug"
_DEBUG_SAMPLE_MAX = 8
_fdps_debug_count = 0


def _capture_debug_sample(xml_bytes: bytes, source: str | None) -> None:
    global _fdps_debug_count
    if _fdps_debug_count >= _DEBUG_SAMPLE_MAX:
        return
    try:
        import os
        os.makedirs(_DEBUG_SAMPLE_DIR, exist_ok=True)
        path = f"{_DEBUG_SAMPLE_DIR}/sample_{_fdps_debug_count}_{source or 'unk'}.xml"
        with open(path, "wb") as f:
            f.write(xml_bytes)
        _fdps_debug_count += 1
        log.info("fdps: wrote debug sample %s (%d bytes, source=%s)",
                  path, len(xml_bytes), source)
    except Exception as e:
        log.warning("fdps: debug sample capture failed: %s", e)


# Dedicated FIXM-3.0 debug capture -- 2026-07-20. Separate from the generic
# capture above (which is unversioned and already maxed at 8 for the life
# of the process) so the 3pm rewrite session can pull a bigger, fresher
# batch specifically confirmed as FIXM 3.0, including a look for a ZDC-
# facility sample and flightType values beyond SCHEDULED (only ZBW/ZNY/ZHU
# and flightType="SCHEDULED" were seen in the original 8-sample batch).
_DEBUG_SAMPLE_DIR_FIXM30 = "/var/lib/corporatetraveldc/fdps_debug_fixm30"
_DEBUG_SAMPLE_MAX_FIXM30 = 25
_fdps_fixm30_debug_count = 0


def _capture_fixm30_debug_sample(xml_bytes: bytes) -> None:
    global _fdps_fixm30_debug_count
    if _fdps_fixm30_debug_count >= _DEBUG_SAMPLE_MAX_FIXM30:
        return
    try:
        import os
        os.makedirs(_DEBUG_SAMPLE_DIR_FIXM30, exist_ok=True)
        path = f"{_DEBUG_SAMPLE_DIR_FIXM30}/sample_{_fdps_fixm30_debug_count}.xml"
        with open(path, "wb") as f:
            f.write(xml_bytes)
        _fdps_fixm30_debug_count += 1
        log.info("fdps: wrote FIXM-3.0 debug sample %s (%d bytes)",
                  path, len(xml_bytes))
    except Exception as e:
        log.warning("fdps: FIXM-3.0 debug sample capture failed: %s", e)


# Primary FIXM 4.x namespaces seen in SFDPS -- LEGACY, not what's live.
# Kept as NSMAP (unchanged name) so _parse_fdps_message_fixm42_legacy below
# needs zero internal changes; only the entry point around it changed.
NSMAP = {
    "fx":  "http://www.fixm.aero/flight/4.2",
    "fb":  "http://www.fixm.aero/base/4.2",
    "msg": "http://www.fixm.aero/messaging/4.2",
    "nas": "http://www.fixm.aero/nas/4.2",
    "xsi": "http://www.w3.org/2001/XMLSchema-instance",
}

# Real FIXM 3.0 namespaces, confirmed live 2026-07-20 against 8 captured
# samples (ZBW/ZNY/ZHU). Not yet used for parsing -- _parse_fdps_message_fixm30
# is a stub -- but recorded here so the 3pm rewrite starts from confirmed
# values instead of re-deriving them from the captured XML.
NSMAP_FIXM30 = {
    "base":       "http://www.fixm.aero/base/3.0",
    "flight":     "http://www.fixm.aero/flight/3.0",
    "foundation": "http://www.fixm.aero/foundation/3.0",
    "nas":        "http://www.faa.aero/nas/3.0",
}

# Real source= values seen live 2026-07-20 across a 25-sample capture batch.
# Wider than the legacy FH/TH/CL/HP/OH/HZ set -- AH/BA/LH/HX appeared
# multiple times each and are accepted (generic field extraction only;
# check_marine_one/check_fdps_watchlist already no-op gracefully on anything
# outside FH/TH/CL, so this is a safe default rather than an error case).
_KNOWN_SOURCES_FIXM30 = frozenset({
    "FH", "TH", "CL", "HP", "OH", "HZ",
    "AH", "BA", "LH", "HX",
    # 2026-08-17: HF and RH confirmed present in a fresh 25-sample capture
    # batch (5/25 = 20% of that batch) while writing real-data-backed
    # smoke tests -- were silently dropped (return None, log.debug only,
    # no warning) by every prior version of this allowlist since the
    # 2026-07-20 derivation, which never saw them. Same generic
    # field-extraction path as the AH/BA/LH/HX addition above -- no new
    # source-specific branching needed, both real samples confirmed to
    # yield sane callsign/gufi/etc through the existing generic path
    # before this line was added. See tests/ingest/test_fdps_fixm30_real_samples.py.
    "HF", "RH",
})


def _detect_fixm_version(xml_bytes: bytes) -> str:
    """
    Cheap pre-parse version sniff -- string containment on the namespace
    URI, no XML parsing needed. Returns "3.0", "4.2", or "unknown".

    Checked in a fixed order (3.0 first) since 3.0 is what's actually live;
    if a message somehow carries both markers (shouldn't happen), the 3.0
    branch wins.
    """
    if b"/nas/3.0" in xml_bytes or b"/fixm.aero/base/3.0" in xml_bytes:
        return "3.0"
    if b"/nas/4.2" in xml_bytes or b"/fixm.aero/base/4.2" in xml_bytes:
        return "4.2"
    return "unknown"

# ── Marine One / POTUS detection ──────────────────────────────────────────────

MARINE_ONE_CALLSIGNS = frozenset({
    "MARINE1", "MARINE2", "SAM", "AF1", "AF2", "EXEC1F",
    "VENUS", "MUSEL", "AZAZ01", "AZAZ09",
})
MARINE_ONE_SQUAWKS = frozenset({"7700", "5000", "5001"})
DC_LAT, DC_LON = 38.8522, -77.0376
MARINE_ONE_RADIUS_NM = 50.0

# ── Geographic filter — store only relevant traffic ───────────────────────────
# Reduces DB size; does NOT reduce FAA wire bandwidth.
# Keep events if: within 250 NM of DCA, OR origin/dest in CORE_AIRPORTS (30
# major US airports), OR flagged as POTUS/Marine One callsign.
# Filter logic lives in ingest.parsers.geo_filter (passes_geo_filter).
#
# DC-area GA/reliever/military airports are within the 250 NM radius and will
# pass the in_range() check even though they aren't in CORE_AIRPORTS.



# _haversine_nm and distance_to_dca_nm are imported from geo_filter above.


def is_marine_one(callsign: str | None, squawk: str | None) -> bool:
    cs = (callsign or "").upper().strip()
    sq = (squawk or "").strip()
    return cs in MARINE_ONE_CALLSIGNS or sq in MARINE_ONE_SQUAWKS


# ── XML helpers ───────────────────────────────────────────────────────────────

def _ns(tag: str, prefix: str) -> str:
    """Expand a namespace prefix to Clark notation: {uri}tag."""
    return f"{{{NSMAP[prefix]}}}{tag}"


def _text(elem: ET.Element | None, *path: str) -> str | None:
    """Follow a chain of child tags (no namespace) and return .text, or None."""
    cur = elem
    for step in path:
        if cur is None:
            return None
        # Try each registered namespace prefix.
        found = None
        for prefix in NSMAP:
            child = cur.find(_ns(step, prefix))
            if child is not None:
                found = child
                break
        if found is None:
            # Fall back to unqualified tag search.
            found = cur.find(step)
        cur = found
    return (cur.text or "").strip() or None if cur is not None else None


def _find(elem: ET.Element | None, *path: str) -> ET.Element | None:
    """Find a nested element across any of the registered namespaces."""
    cur = elem
    for step in path:
        if cur is None:
            return None
        found = None
        for prefix in NSMAP:
            child = cur.find(_ns(step, prefix))
            if child is not None:
                found = child
                break
        if found is None:
            found = cur.find(step)
        cur = found
    return cur


def _attr(elem: ET.Element | None, attr_name: str) -> str | None:
    if elem is None:
        return None
    v = elem.get(attr_name)
    return v.strip() if v else None


# ── FIXM 3.0 helpers ──────────────────────────────────────────────────────────
# Real captures show message/flight/arrival/departure/etc. mostly UNPREFIXED
# (no default xmlns, no explicit prefix) even though the wrapping
# MessageCollection root carries the ns5 prefix -- xsi:type annotates intent
# without changing actual namespace resolution. Rather than guess which
# nested elements are prefixed vs bare (confirmed inconsistent across the
# tree), match on local tag name only, same proven approach used for
# smes_parser.py's real-schema rewrite earlier today.

def _local30(tag: str) -> str:
    return tag.split("}")[-1] if "}" in tag else tag


def _find_local30(elem: ET.Element | None, tag: str) -> ET.Element | None:
    """First direct child matching local tag name, ignoring namespace."""
    if elem is None:
        return None
    for child in elem:
        if _local30(child.tag) == tag:
            return child
    return None


def _find_path_local30(elem: ET.Element | None, *tags: str) -> ET.Element | None:
    cur = elem
    for t in tags:
        cur = _find_local30(cur, t)
        if cur is None:
            return None
    return cur


def _text_local30(elem: ET.Element | None) -> str | None:
    return (elem.text or "").strip() or None if elem is not None else None


# ── Core parser ───────────────────────────────────────────────────────────────

def parse_fdps_message(xml_bytes: bytes) -> dict | None:
    """
    Entry point / version dispatcher for a single SFDPS FIXM message.

    Detects FIXM 3.0 (what's actually live) vs FIXM 4.2 (legacy, stashed
    below) via a cheap namespace-string sniff, then routes to the matching
    parser. Both parsers return the same normalized dict shape (or None),
    so everything downstream (write_flight_event, check_marine_one,
    check_fdps_watchlist) needs no version awareness at all.
    """
    _capture_debug_sample(xml_bytes, None)
    version = _detect_fixm_version(xml_bytes)

    if version == "3.0":
        _capture_fixm30_debug_sample(xml_bytes)
        return _parse_fdps_message_fixm30(xml_bytes)
    elif version == "4.2":
        return _parse_fdps_message_fixm42_legacy(xml_bytes)
    else:
        log.debug("fdps: could not determine FIXM version (no 3.0/4.2 "
                  "namespace marker found); skipping message")
        return None


def _parse_fdps_message_fixm30(xml_bytes: bytes) -> dict | None:
    """
    FIXM 3.0 parser -- IMPLEMENTED 2026-07-20 (3pm session). Derivation
    history below kept intact as the record of how the real schema was
    confirmed (useful if FAA changes the schema again and this needs
    re-deriving) -- the parsing logic itself is at the bottom of this
    docstring, in the actual function body.

    --- PRE-READ UPDATE, 2026-07-20 (~30min before 3pm session) ---
    Found a real public sample (NASARace/race repo, sfdps-nasflight.xml,
    a test fixture -- structurally consistent with our own 8 captured
    samples). This CONFIRMS the good news: the FH/TH/CL/HP/OH/HZ source-code
    model from the 4.2 legacy parser is NOT gone in 3.0 -- it survives as
    the `source` attribute directly on the flight root element (e.g.
    source="TH" in the sample). So this is a structural/path rewrite, not
    a conceptual one: same message-type taxonomy, different XML shape.

    Confirmed root shape (root element IS the flight, at least for a single
    extracted message -- our own captures may show a MessageCollection/
    message wrapper around this same content for batched delivery, not yet
    cross-checked):

        {http://www.faa.aero/nas/3.0}NasFlight
            @centre               -- ARTCC, e.g. "ZDC"
            @source               -- "TH"/"FH"/"CL"/"HP"/"OH"/"HZ" (CONFIRMED, not flightType)
            @system               -- e.g. "FDPS1"

          > flightIdentification[xsi:type=NasFlightIdentificationType]
                @aircraftIdentification   -- callsign, e.g. "UAL1634"
                @computerId, @siteSpecificPlanId

          > gufi[@codeSpace="urn:uuid"]  -- text = UUID (also duplicated
                under supplementalData/additionalFlightInformation/
                nameValue[@name="FDPS_GUFI"] in a longer FAA-internal format)

          > flightStatus[xsi:type=NasFlightStatusType]
                @fdpsFlightStatus   -- e.g. "ACTIVE"

          > controllingUnit[xsi:type=IdentifiedUnitReferenceType]
                @unitIdentifier, @sectorIdentifier   -- e.g. ZDC / "07"

          > departure[xsi:type=NasDepartureType]
              > runwayPositionAndTime > runwayTime > actual|estimated[@time]
                -- NOTE: no aerodrome/ICAO identifier in this sample at all.
                Origin/destination airport codes are NOT visible on a TH
                (track) message -- likely only present on FH (full flight
                plan). Need an FH sample to confirm the airport field path;
                do not assume departure/arrival always carry aerodrome codes.

          > arrival[xsi:type=NasArrivalType]
              > runwayPositionAndTime > runwayTime > actual|estimated[@time]
                (same shape and same airport-code caveat as departure above)

          > enRoute[xsi:type=NasEnRouteType]                -- THE position report
              > position[xsi:type=NasAircraftPositionType, reportSource="SURVEILLANCE",
                          positionTime, targetPositionTime]
                  > actualSpeed > surveillance[@uom="KNOTS"]   -- ground speed, text=knots
                  > altitude[@uom="FEET"]                       -- text=feet
                  > position[xsi:type=LocationPointType]
                      > location > pos                          -- text="LAT LON" (space-sep, decimal deg)
                  > targetAltitude[@uom="FEET"]
                  > targetPosition > pos                         -- next predicted point
                  > trackVelocity > x[@uom="KNOTS"] / y[@uom="KNOTS"]  -- vector components

          > assignedAltitude > simple[@uom="FEET"]           -- text=feet
          > flightPlan[@identifier]                          -- NAS flight plan ID, e.g. "KH48144600"
          > supplementalData > additionalFlightInformation
              > nameValue[@name="FDPS_GUFI", @value=...]
              > nameValue[@name="FLIGHT_PLAN_SEQ_NO", @value=...]

    Mapping to the existing normalized dict (same shape legacy fixm42
    parser returns, so write_flight_event/check_marine_one/
    check_fdps_watchlist need zero changes):
        source        <- root @source (direct, no more nasFlightInfo lookup)
        gufi          <- gufi element text
        callsign      <- flightIdentification/@aircraftIdentification
        origin/dest   <- UNCONFIRMED, likely absent on TH, needs an FH sample
        aircraft_type <- UNCONFIRMED, likely under aircraftDescription
                          (not present in this TH sample; the earlier 4.2-era
                          guess of an "aircraftDescription" block may still
                          be roughly right in 3.0 too, needs a real sample)
        latitude/lon  <- enRoute/position/position/location/pos, split on space,
                          parts[0]=lat, parts[1]=lon (SAME order as legacy pos_text parse)
        altitude_ft   <- enRoute/position/altitude (skip for HZ, same as legacy)
        ground_speed  <- enRoute/position/actualSpeed/surveillance
        squawk        <- UNCONFIRMED, not present in this sample, needs a
                          real sample (legacy used ssrCode/modeACode)
        controlling_facility <- controllingUnit/@unitIdentifier
        flight_status <- flightStatus/@fdpsFlightStatus (attr, not child text
                          like the legacy parser assumed)
        raw_xml       <- unchanged, full decoded xml_bytes

    --- ALL 4 OPEN ITEMS RESOLVED, 2026-07-20 (final pre-3pm pass) ---
    Grepped the full 25-sample fdps_debug_fixm30/ batch (cap was hit --
    25/25 used). Real source= distribution across the batch: TH (dominant,
    bulk position traffic), FH, OH, HP, HZ, AH, BA, LH, HX -- so the legacy
    source allowlist ("FH","TH","CL","HP","OH","HZ") is INCOMPLETE for 3.0;
    AH/BA/LH/HX appeared multiple times each and need a decision at 3pm on
    whether to handle, map to an existing bucket, or explicitly skip-and-log.
    centre values confirmed across many ARTCCs (ZDC, ZNY, ZJX, ZMA, ZMP,
    ZSE, ZAU, ZDV, ZOB, ZLA, ZFW, ZTL, ZME) -- not just ZBW/ZNY/ZHU as the
    original 8-sample batch suggested.

      1. ROOT WRAPPER -- CONFIRMED PRESENT. Every one of the 25 real captures
         (not just the FH one) is wrapped: ns5:MessageCollection > message
         [xsi:type=FlightMessageType] > flight[...]. The bare NasFlight root
         in the GitHub sample was an artifact of that repo's test fixture
         extraction, not a real wire shape. Parser must unwrap two levels
         before reaching the flight element (same shape the legacy 4.2
         parser's `_find(root, "flight")` already expects, so the existing
         _find/_text helpers should mostly just work once NSMAP_FIXM30
         prefixes replace NSMAP).

      2. AIRPORT CODES -- CONFIRMED. NOT nested aerodrome/icaoIdentifier
         elements as originally guessed -- they're plain attributes directly
         on the arrival/departure elements:
             departure[@departurePoint="KBWI"]
             arrival[@arrivalPoint="KBUF"]
         (confirmed on 2 independent FH/AH samples, both airports 3-letter
         K-prefixed ICAO). This means origin/destination extraction on 3.0
         is actually SIMPLER than the legacy 4.2 nested-lookup version.

      3. SQUAWK -- CONFIRMED, but a different path than legacy (which read
         a per-position-report ssrCode/modeACode). On 3.0 it's a single
         current assignment, not per-report:
             enRoute > beaconCodeAssignment > currentBeaconCode  (text, e.g. "7076")
         Present on FH/AH/BA source types in the sample batch; not checked
         yet whether TH (position) messages also carry it (worth a quick
         grep at 3pm, but low priority -- FH-type coverage is enough for
         watchlist matching).

      4. AIRCRAFT TYPE -- CONFIRMED. Full block, richer than legacy:
             aircraftDescription[@aircraftAddress, @registration, @wakeTurbulence]
               > aircraftType > icaoModelIdentifier   (text, e.g. "B38M")
         @registration is a bonus field the legacy parser never had (tail
         number, e.g. "N8792Q") -- worth adding to the normalized dict even
         though write_flight_event's current schema has no column for it yet
         (flag as a possible db.py schema addition, not required for parity).

    Updated mapping (supersedes the partial one above):
        origin        <- departure/@departurePoint
        destination   <- arrival/@arrivalPoint
        aircraft_type <- aircraftDescription/aircraftType/icaoModelIdentifier (text)
        registration  <- aircraftDescription/@registration (NEW, no legacy equivalent)
        squawk        <- enRoute/beaconCodeAssignment/currentBeaconCode (text)
        (all other fields unchanged from the mapping above)

    --- IMPLEMENTED, 2026-07-20 (3pm session) ---
    AH/BA/LH/HX are accepted into the source allowlist (seen live, multiple
    times each) but not given special handling beyond the generic field
    extraction below -- check_marine_one/check_fdps_watchlist already treat
    any non-FH/TH/CL source as "no special action" rather than erroring, so
    this is a safe default. Revisit if a specific need for AH/BA/LH/HX
    handling surfaces (AH looks handoff/amendment-adjacent given it carries
    a full flightPlan+aircraftDescription like FH).

    registration (tail number) is included in the returned dict as a bonus
    field with no legacy equivalent -- write_flight_event/db.upsert_flight_event
    don't have a column for it yet, so it's currently write-then-ignored
    downstream. Flagging as a possible schema addition, not required for
    parity with the 4.2 parser's guaranteed-fields contract.
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        log.warning("fdps: FIXM 3.0 XML parse error: %s", e)
        return None

    root_local = _local30(root.tag)
    if root_local == "MessageCollection":
        message = _find_local30(root, "message")
        flight = _find_local30(message, "flight") if message is not None else None
    elif root_local in ("NasFlight", "flight"):
        # Bare root -- matches the GitHub reference fixture shape, not seen
        # in our own captures (all 25 were MessageCollection-wrapped), but
        # handled defensively in case a future feed variant sends it bare.
        flight = root
    else:
        flight = _find_local30(root, "flight")

    if flight is None:
        log.debug("fdps: FIXM 3.0 -- no flight element found (root=%s)", root_local)
        return None

    source = (flight.get("source") or "").upper().strip()
    if source not in _KNOWN_SOURCES_FIXM30:
        log.debug("fdps: FIXM 3.0 -- unhandled source type %r", source)
        return None

    centre = flight.get("centre")

    gufi = _text_local30(_find_local30(flight, "gufi")) or ""

    flight_id_elem = _find_local30(flight, "flightIdentification")
    callsign = flight_id_elem.get("aircraftIdentification") if flight_id_elem is not None else None

    dep_elem = _find_local30(flight, "departure")
    arr_elem = _find_local30(flight, "arrival")
    # departurePoint/arrivalPoint are only present on FH/AH (full flight
    # plan) messages -- absent on TH (track) messages by design, confirmed
    # against real samples of both. .get() returns None gracefully either way.
    origin = dep_elem.get("departurePoint") if dep_elem is not None else None
    destination = arr_elem.get("arrivalPoint") if arr_elem is not None else None

    aircraft_type: str | None = None
    registration: str | None = None
    ac_elem = _find_local30(flight, "aircraftDescription")
    if ac_elem is not None:
        registration = ac_elem.get("registration")
        model_elem = _find_path_local30(ac_elem, "aircraftType", "icaoModelIdentifier")
        aircraft_type = _text_local30(model_elem)

    latitude: float | None = None
    longitude: float | None = None
    altitude_ft: float | None = None
    ground_speed: int | None = None
    squawk: str | None = None

    en_route = _find_local30(flight, "enRoute")
    if en_route is not None:
        pos_report = _find_local30(en_route, "position")
        if pos_report is not None:
            pos_text = _text_local30(
                _find_path_local30(pos_report, "position", "location", "pos")
            )
            if pos_text:
                parts = pos_text.split()
                if len(parts) >= 2:
                    try:
                        latitude = float(parts[0])
                        longitude = float(parts[1])
                    except ValueError:
                        pass

            if source != "HZ":
                alt_text = _text_local30(_find_local30(pos_report, "altitude"))
                if alt_text:
                    try:
                        altitude_ft = float(alt_text)
                    except ValueError:
                        pass

            speed_text = _text_local30(
                _find_path_local30(pos_report, "actualSpeed", "surveillance")
            )
            if speed_text:
                try:
                    ground_speed = int(float(speed_text))
                except ValueError:
                    pass

        # Single current-assignment beacon code, not per-report like legacy 4.2.
        squawk = _text_local30(
            _find_path_local30(en_route, "beaconCodeAssignment", "currentBeaconCode")
        )

    ctl_elem = _find_local30(flight, "controllingUnit")
    controlling_facility = ctl_elem.get("unitIdentifier") if ctl_elem is not None else centre

    status_elem = _find_local30(flight, "flightStatus")
    flight_status = status_elem.get("fdpsFlightStatus") if status_elem is not None else None
    if source == "CL":
        flight_status = "CANCELLED"
    elif flight_status is None and source in ("FH", "TH", "AH"):
        flight_status = "ACTIVE"

    return {
        "source": source,
        "gufi": gufi,
        "callsign": callsign,
        "squawk": squawk,
        "origin": origin,
        "destination": destination,
        "aircraft_type": aircraft_type,
        "registration": registration,
        "latitude": latitude,
        "longitude": longitude,
        "altitude_ft": altitude_ft,
        "ground_speed": ground_speed,
        "controlling_facility": controlling_facility,
        "flight_status": flight_status,
        "raw_xml": xml_bytes.decode("utf-8", errors="replace"),
    }


def _parse_fdps_message_fixm42_legacy(xml_bytes: bytes) -> dict | None:
    """
    Parse a single SFDPS FIXM 4.2 message. LEGACY -- this is not the schema
    actually live on the feed (see module docstring); kept intact in case
    FAA reverts or another feed still emits 4.2.

    Returns a normalized dict or None if the message type is unhandled.

    Guaranteed fields on success:
        source, gufi, callsign, origin, destination, aircraft_type,
        latitude, longitude, altitude_ft, ground_speed, controlling_facility,
        flight_status, raw_xml
    """
    try:
        raw_xml = xml_bytes.decode("utf-8", errors="replace")
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        log.warning("fdps: XML parse error: %s", e)
        return None

    # Top-level message → flight element
    flight = _find(root, "flight")
    if flight is None:
        # Some messages have the flight as the root itself.
        if root.tag.endswith("}flight") or root.tag == "flight":
            flight = root
        else:
            log.debug("fdps: no flight element in message")
            return None

    # NAS-specific info block carries source type and ACID.
    nas_info = _find(flight, "nasFlightInfo")
    source = (_attr(nas_info, "source")
              or _text(nas_info, "source")
              or _attr(flight, "source")
              or "")
    source = source.upper().strip()

    if source not in ("FH", "TH", "CL", "HP", "OH", "HZ"):
        log.debug("fdps: unhandled source type %r", source)
        return None

    gufi = (_text(flight, "gufi")
            or _attr(flight, "gufi")
            or "")

    callsign = (_text(nas_info, "acid")
                or _text(flight, "flightIdentification", "aircraftIdentification")
                or _attr(flight, "acid"))

    # Departure / arrival airports.
    dep_elem = _find(flight, "departure")
    arr_elem = _find(flight, "arrival")
    origin = (_text(dep_elem, "aerodrome", "icaoIdentifier")
              or _text(dep_elem, "departureAerodrome", "icaoIdentifier"))
    destination = (_text(arr_elem, "aerodrome", "icaoIdentifier")
                   or _text(arr_elem, "arrivalAerodrome", "icaoIdentifier"))

    # Aircraft type.
    ac_elem = _find(flight, "aircraft")
    aircraft_type = (_text(ac_elem, "aircraftType", "icaoAircraftTypeDesignator")
                     or _text(ac_elem, "aircraftAddress"))

    # Position (en-route position report).
    latitude: float | None = None
    longitude: float | None = None
    altitude_ft: float | None = None
    ground_speed: int | None = None
    squawk: str | None = None

    en_route = _find(flight, "enRoute")
    pos_report = _find(en_route, "positionReport") if en_route is not None else None
    if pos_report is None:
        pos_report = _find(flight, "positionReport")

    if pos_report is not None:
        pos_elem = _find(pos_report, "position")
        if pos_elem is not None:
            pos_text = _text(pos_elem, "pos") or _attr(pos_elem, "pos")
            if pos_text:
                parts = pos_text.strip().split()
                if len(parts) >= 2:
                    try:
                        latitude = float(parts[0])
                        longitude = float(parts[1])
                    except ValueError:
                        pass

        # Altitude — skip from HZ (Mode C, not controller-assigned).
        if source != "HZ":
            alt_elem = _find(pos_report, "altitude")
            if alt_elem is not None:
                alt_val = _text(alt_elem, "value") or alt_elem.text
                try:
                    altitude_ft = float(alt_val) if alt_val else None
                except ValueError:
                    pass

        # Ground speed.
        spd_elem = _find(pos_report, "speed")
        if spd_elem is not None:
            spd_val = _text(spd_elem, "value") or spd_elem.text
            try:
                ground_speed = int(float(spd_val)) if spd_val else None
            except ValueError:
                pass

        # SSR (squawk) code.
        # NOTE: ssrCode/modeACode are leaf elements (text content, no children),
        # so plain `or` on Elements is unreliable -- an Element with zero
        # children is falsy under current ElementTree truthiness rules, which
        # silently discarded a found-but-childless ssrCode element and fell
        # through to modeACode (usually absent) -> squawk always None.
        # Use explicit `is not None` checks instead (regression, 2026-07-19).
        ssr_elem = _find(pos_report, "ssrCode")
        if ssr_elem is None:
            ssr_elem = _find(pos_report, "modeACode")
        if ssr_elem is not None:
            squawk = (ssr_elem.text or "").strip() or None

    # Controlling facility.
    # Same Element-truthiness pitfall as squawk above -- use is not None.
    ctl_elem = _find(flight, "controllingUnit")
    if ctl_elem is None:
        ctl_elem = _find(nas_info, "controllingUnit")
    controlling_facility = _text(ctl_elem, "unitIdentifier") if ctl_elem is not None else None

    # Flight status from NAS info or top-level.
    flight_status = (_text(nas_info, "flightStatus")
                     or _attr(flight, "flightStatus"))
    if source == "CL":
        flight_status = "CANCELLED"
    elif flight_status is None and source in ("FH", "TH"):
        flight_status = "ACTIVE"

    return {
        "source": source,
        "gufi": gufi,
        "callsign": callsign,
        "squawk": squawk,
        "origin": origin,
        "destination": destination,
        "aircraft_type": aircraft_type,
        "latitude": latitude,
        "longitude": longitude,
        "altitude_ft": altitude_ft,
        "ground_speed": ground_speed,
        "controlling_facility": controlling_facility,
        "flight_status": flight_status,
        "raw_xml": raw_xml,
    }


# ── DB writer ─────────────────────────────────────────────────────────────────

def _in_dc_area(parsed: dict) -> bool:
    """
    Return True if this flight event is relevant to dispatch operations.

    Pass conditions (any):
      • Callsign is a known POTUS/Marine One callsign (always pass)
      • Origin airport is in CORE_AIRPORTS or within 250 NM of DCA
      • Destination airport is in CORE_AIRPORTS
      • Current position is within 250 NM of DCA
    """
    # VIP/POTUS always passes regardless of position or airports.
    cs = (parsed.get("callsign") or "").upper()
    if cs in MARINE_ONE_CALLSIGNS:
        return True

    # Check position + origin together, then destination separately.
    lat = parsed.get("latitude")
    lon = parsed.get("longitude")
    return (
        passes_geo_filter(lat=lat, lon=lon, airport=parsed.get("origin"))
        or is_core_airport(parsed.get("destination"))
    )


def write_flight_event(parsed: dict) -> bool:
    """Upsert a parsed FDPS message into flight_events (DC-area only).
    Returns True if written, False if filtered out (outside DC area)."""
    if not _in_dc_area(parsed):
        return False  # outside DC area and not POTUS/Marine One — skip

    callsign = parsed.get("callsign") or ""
    airline = callsign[:3] if len(callsign) >= 3 else None
    flight_num = callsign[3:] if len(callsign) > 3 else callsign

    db.upsert_flight_event(
        flight_id=parsed.get("gufi") or callsign,
        airline=airline,
        flight_num=flight_num,
        origin=parsed.get("origin"),
        destination=parsed.get("destination"),
        aircraft_type=parsed.get("aircraft_type"),
        departure_time=None,
        arrival_time=None,
        status=(parsed.get("flight_status") or "").lower() or None,
        position_lat=parsed.get("latitude"),
        position_lon=parsed.get("longitude"),
        altitude_ft=int(parsed["altitude_ft"]) if parsed.get("altitude_ft") else None,
        ground_speed_kt=parsed.get("ground_speed"),
        raw_json=parsed.get("raw_xml", ""),
    )
    log.info("fdps: wrote flight_event for %s (%s -> %s, source=%s)",
             parsed.get("callsign") or parsed.get("gufi"),
             parsed.get("origin"), parsed.get("destination"), parsed.get("source"))
    return True


# ── Marine One detection ──────────────────────────────────────────────────────

def check_marine_one(parsed: dict) -> bool:
    """
    Check parsed FDPS event for POTUS/Marine One indicators.
    If detected within MARINE_ONE_RADIUS_NM of DCA, writes a swim_alert and fires ntfy.
    Returns True if a Marine One alert was fired.
    """
    source = parsed.get("source", "")
    if source not in ("FH", "TH"):
        return False

    callsign = parsed.get("callsign")
    squawk = parsed.get("squawk")
    lat = parsed.get("latitude")
    lon = parsed.get("longitude")

    if not is_marine_one(callsign, squawk):
        return False

    # Require a position for TH; FH match on callsign alone is enough to alert.
    if source == "TH" and (lat is None or lon is None):
        return False

    if source == "TH":
        dist = distance_to_dca_nm(lat, lon)  # type: ignore[arg-type]
        if dist > MARINE_ONE_RADIUS_NM:
            return False

    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=30)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    payload = {
        "callsign": callsign,
        "squawk": squawk,
        "lat": lat,
        "lon": lon,
        "altitude_ft": parsed.get("altitude_ft"),
        "gufi": parsed.get("gufi"),
        "source": source,
        "detected_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    db.upsert_swim_alert("marine_one_fdps", payload, expires_at)

    _fire_marine_one_ntfy(callsign, lat, lon, parsed.get("altitude_ft"))
    log.warning("MARINE ONE DETECTED: callsign=%s squawk=%s lat=%s lon=%s",
                callsign, squawk, lat, lon)
    return True


def _fire_marine_one_ntfy(callsign: str | None, lat: float | None,
                           lon: float | None, alt: float | None) -> None:
    """Fire priority-5 ntfy alert for Marine One detection. Non-blocking."""
    try:
        from shared.watchlist import _fire_ntfy_dual
        cs = callsign or "UNKNOWN"
        pos = f"{lat:.4f},{lon:.4f}" if lat is not None and lon is not None else "position unknown"
        alt_str = f" FL{int(alt/100):03d}" if alt else ""
        detail = f"Callsign: {cs}{alt_str}\nPosition: {pos}\nWithin {MARINE_ONE_RADIUS_NM}nm of DCA"
        dispatch = f"MARINE ONE: {cs} near DCA{alt_str}"
        _fire_ntfy_dual("tfr-alert", f"POTUS MOVEMENT: {cs}", detail, dispatch, priority=5)
    except Exception as e:
        log.error("marine one ntfy fire failed: %s", e)


# ── Watchlist integration ─────────────────────────────────────────────────────

def _fire_fdps_nas_alert(callsign: str, hex_id: str, parsed: dict,
                          dist_nm: float | None) -> None:
    """
    Fire fdps-alerts / fdps-<zone> ntfy for a non-Marine-One watchlist or
    proximity event, via shared.sector_coalesce.fire_family_alert -- gives
    this its own family (escalation threshold, per-topic throttle,
    enable/sanitize), separate from tbfm.

    CHANGED 2026-08-03 per operator direction ("everything should be
    following the same... logic... family-wide alerts and then a
    per-sector alert"): this used to fire directly to "tbfm-alerts" +
    tbfm's sector_ntfy_topic (2026-07-21 decision to fold FDPS proximity
    into the TBFM/metering concern, bypassing sector_coalesce entirely --
    no escalation gating, no throttle). Moved onto its own "fdps-alerts"/
    "fdps-<zone>" topics and the standard escalating-only family gate so
    it gets the same throttle protection just built for tbfm/tfms.
    **New topic names -- fdps-alerts / fdps-<zone> did not exist as
    subscriptions before this change; nothing was being lost by moving
    since the old tbfm-alerts/tbfm-<zone> pushes for this event type were
    never actually a distinct, subscribable signal from real TBFM
    metering events.**
    """
    try:
        from shared.sector_coalesce import fire_family_alert
        cs = callsign or "UNKNOWN"
        reg = parsed.get("aircraft_type") or ""
        alt_baro = parsed.get("altitude_ft") or 0
        gs = parsed.get("ground_speed") or 0
        hex_label = f"[{hex_id}]" if hex_id else ""
        title = f"FDPS Track — {cs} {hex_label}".strip()
        if dist_nm is not None:
            detail = (
                f"{cs} {reg}: {int(alt_baro)}ft {gs}kts"
                f" | dist {dist_nm:.1f}nm DCA"
            )
            dispatch = f"{cs} {hex_label} {int(alt_baro)}ft {gs}kts {dist_nm:.1f}nm DCA".strip()
        else:
            origin = parsed.get("origin") or "?"
            dest = parsed.get("destination") or "?"
            detail = f"{cs} {reg}: {origin}→{dest} | {int(alt_baro)}ft {gs}kts"
            dispatch = f"{cs} {hex_label} {origin}→{dest}".strip()
        facility = parsed.get("controlling_facility")
        fire_family_alert("fdps", "fdps", facility, title, detail, dispatch, base_priority=3)
    except Exception as e:
        log.error("fdps: family-alert fire failed for %s: %s", callsign, e)


def check_fdps_watchlist(parsed: dict) -> None:
    """
    Check a parsed FDPS event against active flight watchlist entries.
    Matches on callsign (case-insensitive). Fires watchlist_event_hit for FH,
    CL, and significant approach events for TH.
    """
    source = parsed.get("source", "")
    if source not in ("FH", "TH", "CL"):
        return

    try:
        from shared.watchlist import get_active_entries, watchlist_event_hit
        entries = get_active_entries(entry_type="flight")
    except Exception as e:
        log.error("fdps watchlist lookup failed: %s", e)
        return

    callsign = (parsed.get("callsign") or "").upper().strip()
    gufi = parsed.get("gufi", "")

    for entry in entries:
        ident = entry["identifier"].upper()
        if callsign != ident and gufi != entry.get("gufi_override", ""):
            continue

        try:
            if source == "FH":
                origin = parsed.get("origin") or "?"
                dest = parsed.get("destination") or "?"
                summary = f"{callsign} filed {origin}→{dest}"
                watchlist_event_hit(entry["id"], summary,
                                    {**parsed, "watchlist_trigger": "fdps_fh"},
                                    priority=3)
                _fire_fdps_nas_alert(callsign, entry.get("hex_id") or "", parsed,
                                     dist_nm=None)

            elif source == "TH":
                _maybe_alert_on_approach(entry, parsed)

            elif source == "CL":
                summary = f"{callsign} cancelled (FDPS CL)"
                watchlist_event_hit(entry["id"], summary,
                                    {**parsed, "watchlist_trigger": "fdps_cl"},
                                    priority=4)
                _fire_fdps_nas_alert(callsign, entry.get("hex_id") or "", parsed,
                                     dist_nm=None)
        except Exception as e:
            log.error("fdps watchlist event for %s: %s", ident, e)


def _maybe_alert_on_approach(entry: dict, parsed: dict) -> None:
    """Fire an alert when a watched flight's TH position is within 50nm of destination."""
    dest = entry.get("destination") or parsed.get("destination")
    lat = parsed.get("latitude")
    lon = parsed.get("longitude")
    if not dest or lat is None or lon is None:
        return

    dest_coords = _AIRPORT_COORDS.get(dest.upper())
    if dest_coords is None:
        return

    dist = _haversine_nm(lat, lon, dest_coords[0], dest_coords[1])
    if dist > 50.0:
        return

    try:
        from shared.watchlist import watchlist_event_hit
        callsign = (parsed.get("callsign") or "").upper()
        alt_str = f" FL{int(parsed['altitude_ft']/100):03d}" if parsed.get("altitude_ft") else ""
        summary = f"{callsign} on approach to {dest}{alt_str} ({dist:.0f}nm out)"
        watchlist_event_hit(
            entry["id"], summary,
            {**parsed, "watchlist_trigger": "fdps_th_approach",
             "dist_nm": round(dist, 1)},
            priority=3,
        )
        # 2026-08-16: shared-slot dedup bug, same class fixed the same night
        # in tfms_parser.py (see that file's _parse_single_program comment
        # for the full explanation) -- a literal "fdps" key collapsed every
        # distinct aircraft's proximity alert into one shared slot, so a
        # different aircraft's alert would make the next one look "new"
        # again regardless of the dedup window. Per-aircraft identity as
        # the key, constant content_key (one-shot "already alerted for this
        # aircraft's approach" gate, same pattern as tfms_track's fix).
        hex_id = entry.get("hex_id") or ""
        dedup_key = content_hash(f"fdps:prox:{hex_id or callsign}")
        if _FDPS_PROX_DEDUP.should_push(dedup_key, "prox"):
            _fire_fdps_nas_alert(callsign, hex_id, parsed, dist_nm=round(dist, 1))
            _FDPS_PROX_DEDUP.record(dedup_key, "prox")
    except Exception as e:
        log.error("approach alert for %s: %s", dest, e)


# DC-area airport coordinates (lat, lon) for approach detection.
_AIRPORT_COORDS: dict[str, tuple[float, float]] = {
    "KDCA": (38.8521, -77.0377),
    "KIAD": (38.9531, -77.4565),
    "KBWI": (39.1754, -76.6683),
    "KPHL": (39.8719, -75.2411),
    "KJFK": (40.6413, -73.7781),
    "KEWR": (40.6895, -74.1745),
    "KBOS": (42.3656, -71.0096),
    "KATL": (33.6407, -84.4277),
    "KORD": (41.9742, -87.9073),
    "KCVG": (39.0488, -84.6678),
    "KPIT": (40.4915, -80.2329),
    "KCLT": (35.2140, -80.9431),
    "KMIA": (25.7959, -80.2870),
    "KMCO": (28.4312, -81.3081),
    "KDFW": (32.8998, -97.0403),
    "KDEN": (39.8561, -104.6737),
    "KLAX": (33.9425, -118.4081),
}
