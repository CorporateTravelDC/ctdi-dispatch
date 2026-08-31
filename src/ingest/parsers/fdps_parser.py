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
4.2 parser never had; since 2026-08-30 it IS persisted (flight_events
gained squawk/registration/controlling_facility columns via
common.db_swim's SCHEMA_SWIM_V41 -- previously all three were parsed
here and dropped at write time).

BATCH FIX 2026-08-30 (SWIM audit): production traffic now goes through
parse_fdps_messages() (one dict per batched MessageCollection message
child) instead of parse_fdps_message() (first message only) -- real
documents batch up to 100 flights and everything past message[0] was
silently dropped for the life of the 3.0 parser. Full evidence trail in
parse_fdps_messages' docstring. write_flight_event() also now logs
same-GUFI destination changes to fdps_destination_changes (diversion /
re-file history; storage only, no new alert -- the poller's corroborated
watchlist diversion alert is unchanged).

Marine One / POTUS detection: fires swim_alert and ntfy for POTUS callsigns
within 50nm of DCA. Version-agnostic -- operates on the normalized dict
either parser produces.
"""
from __future__ import annotations

import logging
import re
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
    # 2026-08-30 late-night: HU confirmed in a fresh capture batch
    # (fdps_debug_fixm30/sample_4.xml, JIA5230 KDCA->KBTR) and until now
    # silently dropped by this allowlist -- same discovery path as HF/RH.
    # It matters more than its volume suggests: the HU sample carries a
    # FULL agreed-route amendment (agreed > route/@nasRouteText with the
    # named SID), i.e. exactly the route-version material Detector D
    # needs, on a message the parser was throwing away.
    "HU",
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


def parse_fdps_messages(xml_bytes: bytes) -> list[dict]:
    """
    Batch-aware entry point -- returns ONE normalized dict per <message>
    child in the document. THE production entry point since 2026-08-30;
    parse_fdps_message() above remains as the single-message (first-match)
    variant for existing tests/tools.

    --- BATCH BUG, found + fixed 2026-08-30 (SWIM ingest audit) ---
    Real FIXM 3.0 MessageCollection documents BATCH many message children:
    in the 25 real captures under fdps_debug_fixm30/, 8 documents carry
    11/100/63/16/52/34/16/12 flights each (grep 'source=' per file) --
    296 of the 321 flight records in that capture set live in position 2+
    of a batch. _parse_fdps_message_fixm30 only ever unwrapped the FIRST
    message per document, so every other flight in every batched document
    was silently dropped before any filter ran: no flight_events write, no
    Marine One check, no watchlist check. This is the same first-match
    batching bug class already found and fixed twice in this ingest layer
    (smes asdexMsg batches 2026-08-03, tfms fltdMessage batches
    2026-07-20) -- and db.py's _find_flight_element() had even documented
    the batching on the READ side on 2026-08-27 ("a single stored row for
    UAL1240 also contained MXY1019, ... and 16 others") without anyone
    noticing the parser side only kept message[0]. The FDPS topic names in
    swim_bad_message_captures/ ("BATCH_TH_FIXM") say the same thing.

    Practical effect before the fix: flights still acquired rows over time
    (each flight is first-in-batch often enough across its many updates),
    which masked the loss -- but the majority of individual updates were
    discarded, and a low-frequency one-shot message (a CL cancellation, an
    FH filing, a Marine-One-relevant TH) sitting at batch position 2+ was
    simply gone.

    raw_xml note: for a multi-message batch, each dict's raw_xml is the
    per-flight element serialization, NOT the whole document -- storing a
    348KB batch document once per flight would multiply raw_json bloat,
    and db._find_flight_element() (which parses raw_json back out)
    explicitly handles either shape by scanning for the matching <flight>.
    """
    _capture_debug_sample(xml_bytes, None)
    version = _detect_fixm_version(xml_bytes)

    if version == "3.0":
        _capture_fixm30_debug_sample(xml_bytes)
        return _parse_fdps_message_fixm30_all(xml_bytes)
    elif version == "4.2":
        parsed = _parse_fdps_message_fixm42_legacy(xml_bytes)
        return [parsed] if parsed else []
    else:
        log.debug("fdps: could not determine FIXM version (no 3.0/4.2 "
                  "namespace marker found); skipping message")
        return []


def _parse_fdps_message_fixm30_all(xml_bytes: bytes) -> list[dict]:
    """Walk EVERY MessageCollection > message > flight element (see
    parse_fdps_messages' docstring for the batch bug this exists to fix)
    and run the shared per-flight extractor on each."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        log.warning("fdps: FIXM 3.0 XML parse error: %s", e)
        return []

    root_local = _local30(root.tag)
    flights: list[ET.Element] = []
    if root_local == "MessageCollection":
        for message in root:
            if _local30(message.tag) != "message":
                continue
            fl = _find_local30(message, "flight")
            if fl is not None:
                flights.append(fl)
    elif root_local in ("NasFlight", "flight"):
        flights = [root]
    else:
        fl = _find_local30(root, "flight")
        if fl is not None:
            flights = [fl]

    if not flights:
        log.debug("fdps: FIXM 3.0 -- no flight elements found (root=%s)", root_local)
        return []

    results: list[dict] = []
    single = len(flights) == 1
    for fl in flights:
        if single:
            # Single-flight document: keep the historical whole-document
            # raw_xml shape (byte-identical to pre-fix behavior).
            raw = xml_bytes.decode("utf-8", errors="replace")
        else:
            try:
                raw = ET.tostring(fl, encoding="unicode")
            except Exception:
                raw = ""
        parsed = _parse_fixm30_flight_element(fl, raw)
        if parsed is not None:
            results.append(parsed)
    return results


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
    field -- since 2026-08-30 (SCHEMA_SWIM_V41) it IS persisted, via
    common.db_swim.update_flight_event_extras() in write_flight_event
    below, alongside squawk and controlling_facility (all three were
    previously parsed here and then dropped at write time).

    2026-08-30 REFACTOR NOTE: this function now only unwraps the FIRST
    message child (its historical behavior, kept for existing tests/tools)
    and delegates the per-flight field extraction to
    _parse_fixm30_flight_element(). Production traffic goes through
    parse_fdps_messages() / _parse_fdps_message_fixm30_all(), which walk
    EVERY message child -- see parse_fdps_messages' docstring for the
    silent batch-drop bug that motivated the split.
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

    return _parse_fixm30_flight_element(
        flight, xml_bytes.decode("utf-8", errors="replace"))


def _parse_fixm30_flight_element(flight: ET.Element, raw_xml: str) -> dict | None:
    """Extract the normalized dict from ONE already-unwrapped FIXM 3.0
    <flight> element. Shared by the single-message compatibility path
    (_parse_fdps_message_fixm30) and the batch-aware production path
    (_parse_fdps_message_fixm30_all) -- the extraction logic itself is
    unchanged from the 2026-07-20 implementation, only hoisted out so the
    batch fix couldn't fork two copies of it."""
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

    # 2026-08-30 late-night (Detector D groundwork): the agreed NAS route
    # string and the arrival runway estimate, both confirmed in real
    # captures (fdps_debug_fixm30/sample_4.xml + sample_6.xml -- the SAME
    # GUFI carrying "KDCA.CLTCH3.MAULS..." then "KDCA./.MAULS...", a real
    # re-expression pair) and never extracted before. Cheap attribute
    # reads; both absent on TH position pings by design.
    #     route_text <- agreed > route/@nasRouteText
    #     eta_estimated <- arrival > runwayPositionAndTime > runwayTime
    #                          > estimated/@time
    route_text: str | None = None
    agreed = _find_local30(flight, "agreed")
    if agreed is not None:
        route_elem = _find_local30(agreed, "route")
        if route_elem is not None:
            route_text = (route_elem.get("nasRouteText") or "").strip() or None

    eta_estimated: str | None = None
    if arr_elem is not None:
        eta_el = _find_path_local30(arr_elem, "runwayPositionAndTime",
                                    "runwayTime", "estimated")
        if eta_el is not None:
            eta_estimated = (eta_el.get("time") or "").strip() or None

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
        "route_text": route_text,
        "eta_estimated": eta_estimated,
        "raw_xml": raw_xml,
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


def _norm_airport(code: str | None) -> str | None:
    """Normalize an airport spelling for equality: FAA 3-letter and ICAO
    K-prefixed 4-letter forms of the same field are the same airport.

    2026-08-30 evening pass (audit backlog #3): the destination-change
    detector below went live this morning comparing RAW spellings, and
    its first ~3.5 hours produced 7 rows -- every single one the same
    airport flapping between spellings across FDPS message sources
    (E25->KE25/O22->KO22/STS->KSTS via TH, KMFR->MFR via FH then
    MFR->KMFR via TH, KACK->ACK via CL), zero real diversions. Different
    FIXM source types spell the same destination field differently, so
    normalized comparison is a correctness requirement, not polish: the
    flap noise would otherwise both pollute the diversion history and
    hand the alternate-saturation detector false convergence (many
    unrelated flights 'changing' to the same busy airport's other
    spelling). Non-K 4-letter codes (international) pass through."""
    if not code:
        return None
    c = code.strip().upper()
    if len(c) == 4 and c.startswith("K"):
        return c[1:]
    return c or None


# ── Operator-class gate (2026-08-30 late pass) ───────────────────────────────
#
# Per the external SWIM diversion-detection document (measured over 7 days
# of nationwide split-leg candidates): fractional/charter operators flying
# airline-SHAPED three-letter callsigns (EJA 2,297 + LXJ 1,142 + others =
# 3,655 candidates) plus tail-number GA (3,337) together account for
# ~6,992 of 11,170 (~85%) of raw continuation-shaped candidates -- and for
# an on-demand operator an O->X->D shape "means Tuesday", not a diversion.
# The document's sharpest warning: a tail-number filter FEELS like it
# removes GA but fractional fleets pass straight through it, so BOTH
# checks are required. Gated pairs are still STORED (operator_class column
# on fdps_diversion_continuations, db_swim v45) -- the document says
# recording a tech stop as a fact about an airframe is fine; letting it
# into a diversion count or a notification is not.
#
# The prefix set is a STARTER ALLOWLIST of well-established US
# fractional/charter ICAO designators (NetJets EJA, Executive Jet
# Management EJM, NetJets Europe NJE, Flexjet LXJ, Flight Options OPT,
# Delta Private Jets DPJ, XOJET XOJ, VistaJet VJT, Wheels Up/Gama GAJ) --
# only EJA/LXJ come from the document itself; the rest are common
# knowledge, not measured on this box (zero real continuation candidates
# exist locally yet). Extend from live stored operator_class rows once
# they accumulate; same closed-vocabulary discipline as
# tfms_parser._DIVERSION_INDICATOR_VOCAB.
_FRACTIONAL_CHARTER_PREFIXES = frozenset({
    "EJA", "EJM", "NJE", "LXJ", "OPT", "DPJ", "XOJ", "VJT", "GAJ",
})

# US registration flown as a callsign: N + digit (N-numbers never start
# with 0) + up to 4 more alphanumerics. Deliberately loose on the
# trailing-letters detail (real N-numbers end in 0-2 letters) -- a false
# "ga_tail" here only downgrades an alert to storage, never loses the row.
_TAIL_NUMBER_RE = re.compile(r"^N[1-9][0-9A-Z]{0,4}$")


def _operator_class(callsign: str | None) -> str:
    """Classify a callsign for the continuation gate: 'ga_tail'
    (registration-as-callsign), 'fractional' (known fractional/charter
    three-letter designator), or 'scheduled' (everything else -- the only
    class allowed to fire a continuation alert)."""
    cs = (callsign or "").strip().upper()
    if not cs:
        return "scheduled"  # unknown identity: fail open on class, the
        # airport-pair + relationship match is still required
    if _TAIL_NUMBER_RE.match(cs):
        return "ga_tail"
    if len(cs) >= 4 and cs[:3] in _FRACTIONAL_CHARTER_PREFIXES and cs[3].isdigit():
        return "fractional"
    return "scheduled"


def _net_destination_changes(rows: list[dict]) -> dict[str, dict]:
    """Collapse a window of fdps_destination_changes rows to ONE NET
    change per flight_id: filed = the earliest row's old value, current =
    the latest row's new value (both normalized). Flights whose net filed
    == net current (a flap that returned to its original destination) are
    EXCLUDED entirely.

    2026-08-30 late pass, per the external SWIM document's Trap 2: the
    feed oscillates a destination within seconds (its measured example:
    KPHL->KPIT->KPHL->KPIT over 8 minutes), and a detector reacting
    per-row both records an arbitrary intermediate flap as "the" change
    and keeps counting a flight that already flapped back. The evening
    pass's _norm_airport fixed the SPELLING flavor of this (all 7 first-
    day rows); this fixes the genuine-oscillation flavor, which spelling
    normalization cannot touch. Both consumers (_check_alternate_saturation,
    _check_diversion_continuation) read through this collapse; the
    append-only table itself still stores every observed change row.

    Returned dict values carry: filed (net-old, normalized), current
    (net-new, normalized), plus the latest row's fields (id, callsign,
    origin, source, detected_at) for attribution."""
    by_flight: dict[str, list[dict]] = {}
    for r in rows:
        fid = r.get("flight_id")
        if fid:
            by_flight.setdefault(fid, []).append(r)
    net: dict[str, dict] = {}
    for fid, frows in by_flight.items():
        frows.sort(key=lambda r: (r.get("detected_at") or "", r.get("id") or 0))
        filed = _norm_airport(frows[0].get("old_destination"))
        current = _norm_airport(frows[-1].get("new_destination"))
        if not filed or not current or filed == current:
            continue  # net no-op: flap returned home, or unusable row
        last = frows[-1]
        net[fid] = {
            "flight_id": fid,
            "filed": filed,
            "current": current,
            "id": last.get("id"),
            "callsign": last.get("callsign"),
            "origin": last.get("origin"),
            "source": last.get("source"),
            "detected_at": last.get("detected_at"),
            "old_destination": frows[0].get("old_destination"),
            "new_destination": last.get("new_destination"),
        }
    return net


# ── Alternate-saturation detection (2026-08-30 evening pass) ─────────────────
#
# "Many flights re-filing their destination TO the same alternate inside a
# short window" -- the aggregate signal fdps_destination_changes was built
# to enable. Runs inline on each REAL (normalized) destination change; the
# window query is a single indexed SELECT on a table whose real-change
# rate is near zero (see threshold provenance below), so per-change cost
# is negligible.
#
# Threshold provenance (2026-08-30): the intended derivation -- backfill
# historical destination-change events out of flight_events and tune from
# that distribution -- turned out to be IMPOSSIBLE: flight_events keys
# flight_id as its PRIMARY KEY (verified live: 910,138 rows == 910,138
# distinct flight_ids), i.e. it is an upsert current-state table holding
# exactly one row per GUFI, so no per-flight destination history survives
# to reconstruct. The only real observation available is this table's own
# first live hours: 7 rows in ~3.5 h, all 7 spelling flaps (zero real
# changes once normalized) -- a real-change base rate of ~0/hour on a
# fair-weather Saturday afternoon. Against that baseline, 3+ DISTINCT
# flights re-filing to the SAME airport inside one hour is far outside
# anything observed, so 3-in-3600s is a deliberately conservative
# cold-start value, NOT a tuned one. RETUNE once a few weeks of
# normalized live rows exist -- the same maturation path TBFM's
# _MIN_SEQ_FOR_ALERT=5 followed (operator directive + observed live
# queue counts).
_ALT_SAT_WINDOW_SECS = 3600
_ALT_SAT_MIN_FLIGHTS = 3
_ALT_SAT_DEDUP = PushDedup("fdps_alt_saturation", dedup_secs=3600)

# 2026-08-30 late pass: last-alerted flight set per (normalized) target
# airport, per the external SWIM document's episode-identity invariant --
# a rolling-window detector re-keys as rows age out of the window, so a
# SHRUNKEN set (a flight aging out, or flapping back home under the new
# net-change collapse) would look like a new content-key to PushDedup and
# re-fire an alert that reports FEWER flights than the one already sent.
# Growth is the signal; only a set containing a flight NOT already
# alerted may fire. GUFIs are unique per leg, so a genuinely new later
# episode always carries new flight_ids and passes this gate naturally.
# Value is (fired_at_epoch, frozenset_of_flight_ids); stale entries
# (older than 2 windows) are dropped on touch to bound memory.
_ALT_SAT_LAST_ALERTED: dict[str, tuple[float, frozenset]] = {}


def _check_alternate_saturation(new_destination: str) -> None:
    """After a real destination change TO new_destination, count the
    distinct flights whose NET destination change in the window (see
    _net_destination_changes -- earliest old vs latest new, so a flight
    that flapped back to its original destination never counts) landed on
    the same (normalized) airport, and fire one aggregate fdps-family
    alert at threshold. Flights whose origin == the target airport are
    excluded -- return-to-field/positioning shapes, not flights
    converging on an alternate (matching insert_fdps_destination_change's
    own docstring). Each ADDITIONAL convergent flight re-fires
    immediately (growth is the signal); rebroadcasts, flap-shrinkage and
    age-out shrinkage stay quiet (subset gate above). Non-fatal
    everywhere -- this must never break the ingest write path."""
    try:
        from common import db_swim
        rows = db_swim.get_recent_destination_changes(_ALT_SAT_WINDOW_SECS)
    except Exception as e:
        log.debug("fdps: alternate-saturation query failed (non-fatal): %s", e)
        return
    target = _norm_airport(new_destination)
    if not target:
        return
    flights: dict[str, dict] = {}
    for fid, net in _net_destination_changes(rows).items():
        if net["current"] != target:
            continue
        if _norm_airport(net.get("origin")) == target:
            continue
        flights[fid] = net
    if len(flights) < _ALT_SAT_MIN_FLIGHTS:
        return
    # Subset gate (see _ALT_SAT_LAST_ALERTED comment above).
    prev = _ALT_SAT_LAST_ALERTED.get(target)
    now_mono = time.time()
    if prev is not None:
        fired_at, prev_set = prev
        if now_mono - fired_at > 2 * _ALT_SAT_WINDOW_SECS:
            _ALT_SAT_LAST_ALERTED.pop(target, None)
        elif set(flights) <= prev_set:
            return
    try:
        dedup_key = content_hash(f"fdps:altsat:{target}")
        content_key = content_hash(",".join(sorted(flights)))
        if not _ALT_SAT_DEDUP.should_push(dedup_key, content_key):
            return
        calls = ", ".join(sorted(
            (r.get("callsign") or fid) for fid, r in flights.items()))[:300]
        n = len(flights)
        title = f"Alternate saturation: {n} flights re-filed to {new_destination}"
        detail = (f"{n} distinct flights changed filed destination to "
                  f"{new_destination} within {_ALT_SAT_WINDOW_SECS // 60} min: {calls}")
        dispatch = f"ALT SATURATION {new_destination}: {n} flights re-filed in"
        from shared.sector_coalesce import fire_family_alert
        # escalating_only=False: crossing this threshold is itself the
        # alert-worthy event (the threshold IS the burst gate) -- waiting
        # for a further 3x burst pattern on top would defeat it.
        # isolate=True: shares family="fdps" with the proximity/track
        # events fired by _fire_fdps_nas_alert and must not
        # sympathetically trigger, or be triggered by, that sibling's
        # burst pattern (same reasoning as aim_parser's NOTAM sibling).
        fire_family_alert("fdps", "fdps_alt_saturation", None, title, detail,
                          dispatch, base_priority=4, escalating_only=False,
                          isolate=True)
        _ALT_SAT_DEDUP.record(dedup_key, content_key)
        # Union with any prior alerted set so a later shrink-then-regrow
        # to a previously-alerted membership stays quiet (only genuinely
        # NEW flight_ids can re-fire).
        prev_set = _ALT_SAT_LAST_ALERTED.get(target, (0.0, frozenset()))[1]
        _ALT_SAT_LAST_ALERTED[target] = (time.time(),
                                         prev_set | frozenset(flights))
        log.info("fdps: alternate-saturation alert for %s (%d flights)",
                 new_destination, n)
    except Exception as e:
        log.error("fdps: alternate-saturation alert failed (non-fatal): %s", e)


# ── Diversion-continuation detection (2026-08-30 night pass) ─────────────────
#
# Backlog item deferred by the morning/afternoon passes: a flight in
# fdps_destination_changes changed destination B -> C (a diversion), and a
# LATER filing -- a DIFFERENT GUFI, since each leg gets its own -- by the
# same callsign (or same registration, for the common case where the
# recovery leg files under a changed callsign) files C -> B: the leg that
# only exists because of the diversion. Specific-pair match, not a
# statistical detector, so no volume threshold (unlike alternate
# saturation above): storage row + ONE alert per pair, gated by the
# table's own UNIQUE constraint rather than a PushDedup window (a pair is
# once-ever, not once-per-window).
#
# Relationship matching: exact callsign, else registration equality via
# flight_events' COALESCE-kept registration column. NO related-callsign /
# continuation-suffix convention is applied because none exists anywhere
# in this repo (verified this pass) and inventing one (e.g. "same airline
# + digit-suffix" heuristics) would manufacture false pairs; if the
# operator later documents a real convention, extend the match here.
#
# ACARS corroboration (operator's own refinement): when acars_messages
# has a message from the same registration in the window whose text is
# consistent with the continuation (mentions either airport, or a
# divert-family keyword), attach it and mark confidence "fdps+acars".
# Strictly a bonus -- the FDPS follow-on filing ALONE is a valid signal
# (most aircraft aren't ACARS-equipped, and this box's local ACARS feed
# has never produced a row -- see ingest/README.md), so absence of
# corroboration downgrades nothing.
#
# Window: 6 h. A continuation normally files within a couple of hours of
# the diversion landing; a much longer window starts matching the next
# day's normally-scheduled reflight of the same city pair (same callsign,
# same C->B shape after a B->C return-to-origin row) as a "continuation".
# Cold-start value, same retune-from-live-rows path as _ALT_SAT_* above
# (fdps_destination_changes has zero real diversions so far -- 7/7 rows
# were the spelling flaps _norm_airport now suppresses).
_CONTINUATION_WINDOW_SECS = 6 * 3600


def _norm_reg(reg: str | None) -> str | None:
    if not reg:
        return None
    r = reg.strip().upper().replace("-", "")
    return r or None


def _check_diversion_continuation(parsed: dict, flight_id: str,
                                  callsign: str | None) -> None:
    """On the FIRST sighting of a new GUFI that carries both origin and
    destination, check whether it is the C->B continuation of a recent
    B->C diversion by a related flight. Non-fatal everywhere -- must never
    break the ingest write path.

    2026-08-30 late pass (external SWIM document): matching now runs on
    each candidate diverted flight's NET destination change (earliest
    filed vs latest current -- see _net_destination_changes), so (a) a
    diverted flight that flapped back to its original destination can
    never seed a pair from a stale intermediate row, and (b) "originally
    filed destination" genuinely means the EARLIEST known value, not the
    old side of whichever change row happened to match (the document's
    chaining rule condition 3). Also gated: diverted legs whose own filed
    origin == filed destination (O==D, the document's Trap 5 --
    maintenance/positioning shapes, "115 of 127 at airports where
    confirmation was impossible") never pair."""
    cont_origin = _norm_airport(parsed.get("origin"))        # C
    cont_dest = _norm_airport(parsed.get("destination"))     # B
    if not cont_origin or not cont_dest or cont_origin == cont_dest:
        return
    try:
        from common import db_swim
        rows = db_swim.get_recent_destination_changes(_CONTINUATION_WINDOW_SECS)
    except Exception as e:
        log.debug("fdps: continuation query failed (non-fatal): %s", e)
        return
    cont_reg = _norm_reg(parsed.get("registration"))
    for r in _net_destination_changes(rows).values():
        try:
            if r.get("flight_id") == flight_id:
                continue  # same GUFI = same leg re-broadcast, not a new leg
            old_b = r["filed"]      # net: earliest known filed destination
            new_c = r["current"]    # net: latest current destination
            if old_b != cont_dest or new_c != cont_origin:
                continue
            # Trap 5: a diverted leg FILED origin==destination is a
            # maintenance/positioning shape, not a divertible trip; its
            # "continuation" back to old_b would just be the return hop.
            # (old_b == r.origin means the plan was filed A->A.)
            if _norm_airport(r.get("origin")) == old_b:
                continue
            # Relationship: exact callsign first, registration second.
            div_reg = None
            if callsign and r.get("callsign") and callsign == r["callsign"]:
                match_basis = "callsign"
                try:
                    from common import db_swim
                    div_reg = db_swim.get_flight_event_registration(r["flight_id"])
                except Exception:
                    div_reg = None
            else:
                if not cont_reg:
                    continue
                try:
                    from common import db_swim
                    div_reg = db_swim.get_flight_event_registration(r["flight_id"])
                except Exception:
                    continue
                if _norm_reg(div_reg) != cont_reg:
                    continue
                match_basis = "registration"
            # ACARS corroboration -- bonus only, never required.
            acars = None
            reg_for_acars = cont_reg or _norm_reg(div_reg)
            if reg_for_acars:
                try:
                    from common import db_swim
                    acars = db_swim.find_acars_corroboration(
                        reg_for_acars, [cont_origin, cont_dest])
                except Exception:
                    acars = None
            confidence = "fdps+acars" if acars else "fdps"
            # Operator-class gate (2026-08-30 late pass, external SWIM
            # document's continuation GUARDS section -- flagged there as
            # "the one most likely missed"): fractional/charter fleets
            # under airline-shaped callsigns (EJA/LXJ/...) plus
            # tail-number GA together measured ~85% of raw continuation-
            # shaped candidates nationwide, and for them an O->X->D shape
            # is a normal multi-leg trip. If EITHER side of the pair
            # classifies as fractional/ga_tail, the pair is STORED (a
            # tech stop is a real fact about an airframe) but never
            # alerted. Classify the continuation callsign first; a
            # registration-matched pair with no continuation callsign
            # falls back to the diverted leg's callsign.
            op_class = _operator_class(callsign or r.get("callsign"))
            if op_class == "scheduled":
                div_class = _operator_class(r.get("callsign"))
                if div_class != "scheduled":
                    op_class = div_class
            from common import db_swim
            is_new = db_swim.insert_diversion_continuation(
                change_id=r.get("id"),
                diverted_flight_id=r["flight_id"],
                continuation_flight_id=flight_id,
                callsign=r.get("callsign"),
                continuation_callsign=callsign,
                match_basis=match_basis,
                registration=cont_reg or _norm_reg(div_reg),
                origin=_norm_airport(r.get("origin")),
                original_destination=cont_dest,
                diversion_airport=cont_origin,
                acars_msg_id=acars["id"] if acars else None,
                confidence=confidence,
                diversion_detected_at=r.get("detected_at"),
                operator_class=op_class,
            )
            if not is_new:
                continue
            if op_class != "scheduled":
                log.info("fdps: diversion-continuation pair %s -> %s stored "
                         "but NOT alerted (operator_class=%s)",
                         r["flight_id"], flight_id, op_class)
                continue
            _fire_continuation_alert(r, flight_id, callsign, cont_origin,
                                     cont_dest, match_basis, confidence)
        except Exception as e:
            log.error("fdps: continuation check failed for %s (non-fatal): %s",
                      flight_id, e)


def _fire_continuation_alert(change_row: dict, continuation_flight_id: str,
                             continuation_callsign: str | None,
                             diversion_airport: str, original_destination: str,
                             match_basis: str, confidence: str) -> None:
    """One alert per newly-recorded pair (the UNIQUE insert already gated
    once-only). Same fire_family_alert shape as _check_alternate_saturation:
    escalating_only=False (finding the pair IS the event), isolate=True
    (shares family='fdps' with proximity/track events and must not couple
    to their burst pattern)."""
    try:
        who = (change_row.get("callsign") or change_row["flight_id"])
        cont_who = continuation_callsign or continuation_flight_id
        corr = " +ACARS corroborated" if confidence == "fdps+acars" else ""
        title = (f"Diversion continuation: {cont_who} "
                 f"{diversion_airport}->{original_destination}")
        detail = (f"{who} diverted {change_row.get('old_destination')} -> "
                  f"{change_row.get('new_destination')} at "
                  f"{change_row.get('detected_at')}; {cont_who} has now filed "
                  f"{diversion_airport} -> {original_destination} "
                  f"(matched by {match_basis}){corr}.")
        dispatch = (f"DIVERSION CONTINUATION {cont_who}: "
                    f"{diversion_airport}->{original_destination}{corr}")
        from shared.sector_coalesce import fire_family_alert
        fire_family_alert("fdps", "fdps_diversion_continuation", None, title,
                          detail, dispatch, base_priority=4,
                          escalating_only=False, isolate=True)
        log.info("fdps: diversion-continuation pair %s -> %s (%s, %s)",
                 change_row["flight_id"], continuation_flight_id, match_basis,
                 confidence)
    except Exception as e:
        log.error("fdps: continuation alert failed (non-fatal): %s", e)


# ── Detector D groundwork: route-version capture + genuine-reroute
#    classifier (2026-08-30 late-night pass) ────────────────────────────────
#
# Per the external SWIM document, most raw route "changes" are
# re-expressions of the same clearance (filed-to-activated notation
# change, progressive suffix trim as the flight proceeds, arrival
# entry-fix reassignment with the deep route unchanged) -- genuine
# reroutes were ~23% of raw changes in its reference system, and the
# MEDIAN genuine reroute cost nothing (-1 min); the signal lives in the
# p90 tail of schedule-estimate movement. So this pass classifies and
# stores -- it never alerts on a route change, and the weather-attribution
# half of Detector D is deliberately NOT built: it requires an ARCHIVED,
# timestamped convective-SIGMET polygon history ("was there weather when
# THIS reroute happened", not "is there weather now") and no such archive
# exists anywhere on this box (web/main.py's /airsigmet endpoint is a
# live-snapshot proxy that stores nothing; NWWS is WFO-filtered to
# LWX/AKQ/CTP/PHI so AWC's KKCI convective SIGMETs never arrive).
# Building attribution against the live snapshot would silently answer
# the wrong question -- flagged as real legwork for a future pass
# instead.
#
# NAS route grammar handled ("KDCA.CLTCH3.MAULS.Q40.NIOLA..MCB..KBTR/0319",
# both real capture values): dot-separated elements, '..' = direct,
# 'ORIG./.' = route-from-present-position (the activated re-expression
# marker -- second real capture), trailing '/HHMM' = ETE suffix on the
# destination. A named procedure (SID/STAR) is letters ending in a single
# digit revision (CLTCH3, FOLZZ3); a one-letter prefix + digits (Q40,
# J121, V44) is an airway and stays in the enroute body.

_ROUTE_PROC_RE = re.compile(r"^[A-Z]{3,}[0-9]$")


def _parse_nas_route(text: str | None) -> dict | None:
    """Split a NAS route string into
    {origin, dep_proc, body, arr_entry_fix, arr_proc, dest, from_position}.
    body is the enroute element list (procedures excluded). Returns None
    when the string has no recognizable origin..destination frame."""
    t = (text or "").strip().upper()
    if not t:
        return None
    parts = t.split(".")
    tokens = [p for p in parts if p]
    if len(tokens) < 2:
        return None
    origin = tokens[0].split("/")[0]
    dest = tokens[-1].split("/")[0]
    interior = tokens[1:-1]
    from_position = False
    if interior and interior[0] == "/":
        from_position = True
        interior = interior[1:]
    dep_proc = None
    if not from_position and interior and _ROUTE_PROC_RE.match(interior[0]):
        dep_proc = interior[0]
        interior = interior[1:]
    arr_proc = None
    if interior and _ROUTE_PROC_RE.match(interior[-1]):
        arr_proc = interior[-1]
        interior = interior[:-1]
    arr_entry_fix = interior[-1] if (arr_proc and interior) else None
    return {
        "origin": origin, "dep_proc": dep_proc, "body": interior,
        "arr_entry_fix": arr_entry_fix, "arr_proc": arr_proc,
        "dest": dest, "from_position": from_position,
    }


def _is_route_suffix(shorter: list, longer: list) -> bool:
    """True when `shorter` is a contiguous tail of `longer` -- the shape a
    route takes as already-flown elements are trimmed off the front."""
    n = len(shorter)
    return n <= len(longer) and (n == 0 or longer[-n:] == shorter)


def _classify_route_change(old_text: str | None, new_text: str | None) -> str:
    """Classify a new distinct route version against the previous one.
    'genuine' per the document's rules -- arrival procedure name changed,
    departure procedure changed with both non-null, or the enroute body
    diverged in a way that is neither a suffix trim nor the
    unactivated->activated transition. Everything else is noise:
    're_expression' (filed -> from-present-position, deep route intact),
    'suffix_trim' (front elements consumed), 'entry_fix_only' (same STAR,
    reassigned entry fix, deep route unchanged), 'notation_only'
    (components identical, string differs), 'identical', 'unclassified'
    (unparseable -- stored, never guessed at)."""
    if not old_text or not new_text:
        return "unclassified"
    if old_text.strip().upper() == new_text.strip().upper():
        return "identical"
    po = _parse_nas_route(old_text)
    pn = _parse_nas_route(new_text)
    if po is None or pn is None:
        return "unclassified"

    ob, nb = po["body"], pn["body"]

    if po["arr_proc"] != pn["arr_proc"]:
        # Arrival procedure NAME change is always genuine -- unless one
        # side merely lost the procedure to a suffix trim of the whole
        # arrival structure (rare; treated as genuine per the document's
        # conservative default for arrival-side change).
        return "genuine"
    if po["dep_proc"] and pn["dep_proc"] and po["dep_proc"] != pn["dep_proc"]:
        return "genuine"

    if ob == nb:
        if pn["from_position"] and not po["from_position"]:
            return "re_expression"
        return "notation_only"

    # Same STAR, deep route unchanged, only the entry fix reassigned.
    if (po["arr_proc"] and ob and nb
            and ob[:-1] == nb[:-1] and ob[-1] != nb[-1]):
        return "entry_fix_only"

    if pn["from_position"] and not po["from_position"] and _is_route_suffix(nb, ob):
        return "re_expression"
    if _is_route_suffix(nb, ob) or _is_route_suffix(ob, nb):
        return "suffix_trim"
    return "genuine"


def _eta_delta_minutes(prev_eta: str | None, new_eta: str | None) -> float | None:
    """Arrival-estimate movement across a route change, minutes (positive
    = the new route arrives LATER -- the cost tail Detector D ranks by)."""
    if not prev_eta or not new_eta:
        return None
    try:
        p = datetime.fromisoformat(prev_eta.replace("Z", "+00:00"))
        n = datetime.fromisoformat(new_eta.replace("Z", "+00:00"))
        return round((n - p).total_seconds() / 60.0, 1)
    except ValueError:
        return None


def _record_route_version(parsed: dict, flight_id: str) -> None:
    """Store/refresh this message's route string in fdps_route_versions
    and classify a NEW version against the previous latest. Runs inside
    write_flight_event's DC-area gate (bounded growth) and only for
    messages that actually carry nasRouteText (FH/AH/HU-family; TH pings
    never reach here). Storage + classification only -- deliberately no
    alert (see the module-comment above: the median reroute costs
    nothing; alerting on occurrence would page a non-event constantly).
    Non-fatal everywhere."""
    route_text = parsed.get("route_text")
    if not route_text or not flight_id:
        return
    try:
        from common import db_swim
        is_new, prev = db_swim.upsert_fdps_route_version(
            flight_id=flight_id,
            callsign=parsed.get("callsign"),
            origin=parsed.get("origin"),
            destination=parsed.get("destination"),
            route_text=route_text,
            source=parsed.get("source"),
            eta=parsed.get("eta_estimated"),
        )
        if not is_new or prev is None:
            return
        change_class = _classify_route_change(prev.get("route_text"), route_text)
        eta_delta = _eta_delta_minutes(prev.get("eta_last") or prev.get("eta_first"),
                                       parsed.get("eta_estimated"))
        db_swim.set_route_version_class(flight_id, route_text,
                                        change_class, eta_delta)
        log.info("fdps: route version %s for %s classified %s%s",
                 route_text[:60], parsed.get("callsign") or flight_id,
                 change_class,
                 f" (eta {eta_delta:+.0f} min)" if eta_delta is not None else "")
    except Exception as e:
        log.debug("fdps: route-version capture failed for %s (non-fatal): %s",
                  flight_id, e)


def write_flight_event(parsed: dict) -> bool:
    """Upsert a parsed FDPS message into flight_events (DC-area only).
    Returns True if written, False if filtered out (outside DC area)."""
    if not _in_dc_area(parsed):
        return False  # outside DC area and not POTUS/Marine One — skip

    callsign = parsed.get("callsign") or ""
    airline = callsign[:3] if len(callsign) >= 3 else None
    flight_num = callsign[3:] if len(callsign) > 3 else callsign

    # 2026-08-30 (SWIM audit): destination-change detection, BEFORE the
    # upsert overwrites the stored value. Only runs when THIS message
    # carries a destination (FH/AH-family filings -- a small fraction of
    # FDPS volume; TH position pings carry none and skip the extra
    # SELECT). Keyed on GUFI, so a reused flight number on a different leg
    # can never masquerade as a change (each leg has its own GUFI).
    # Storage only -- no alert fired here: watched flights already get a
    # corroborated diversion alert via poller/main.py's ADS-B + FDPS
    # cross-check, and an unwatched flight's destination change is
    # analytics material (diversion history, alternate saturation), not an
    # operator page. Failure is non-fatal by design.
    flight_id = parsed.get("gufi") or callsign
    new_dest = parsed.get("destination")
    if new_dest and flight_id:
        try:
            from common import db_swim
            prev_dest = db_swim.get_flight_event_destination(flight_id)
            # 2026-08-30 evening: compare NORMALIZED spellings -- the raw
            # comparison's entire first-day output (7/7 rows) was FAA
            # 3-letter vs ICAO 4-letter flapping of the same airport
            # across message sources, not diversions. See _norm_airport.
            if (prev_dest and prev_dest != new_dest
                    and _norm_airport(prev_dest) != _norm_airport(new_dest)):
                db_swim.insert_fdps_destination_change(
                    flight_id=flight_id, callsign=callsign or None,
                    origin=parsed.get("origin"), old_destination=prev_dest,
                    new_destination=new_dest, source=parsed.get("source"),
                )
                log.info("fdps: destination change for %s (%s): %s -> %s",
                         callsign or flight_id, parsed.get("source"),
                         prev_dest, new_dest)
                _check_alternate_saturation(new_dest)
            elif prev_dest is None and parsed.get("origin"):
                # 2026-08-30 night pass: FIRST sighting of a new GUFI with
                # a full origin/destination -- the only shape a diversion-
                # continuation filing (new leg, new GUFI) can arrive as.
                # Costs one indexed window SELECT on a near-empty table,
                # and only on first-sighting filings (TH pings carry no
                # origin/destination and never reach here).
                _check_diversion_continuation(parsed, flight_id, callsign or None)
        except Exception as e:
            log.debug("fdps: destination-change check failed for %s (non-fatal): %s",
                      flight_id, e)

    # 2026-08-30 late-night (Detector D groundwork): distinct route-version
    # capture + genuine-vs-noise classification. Inside the DC-area gate
    # above by design; no-op for the (dominant) messages carrying no
    # nasRouteText. See _record_route_version.
    _record_route_version(parsed, flight_id)

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

    # 2026-08-30 (SWIM audit, SCHEMA_SWIM_V41): persist the three fields
    # this parser has produced since 2026-07-20 that the upsert above has
    # no columns for (registration was explicitly flagged in the module
    # docstring as a wanted schema addition back then). Follow-up UPDATE
    # with COALESCE keep-last-known semantics, via common.db_swim -- see
    # that module's docstring for why db.upsert_flight_event itself was
    # not widened in this pass. Non-fatal on any failure.
    try:
        from common import db_swim
        db_swim.update_flight_event_extras(
            flight_id=flight_id,
            squawk=parsed.get("squawk"),
            registration=parsed.get("registration"),
            controlling_facility=parsed.get("controlling_facility"),
        )
    except Exception as e:
        log.debug("fdps: flight_event extras update failed for %s (non-fatal): %s",
                  flight_id, e)

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

    # 2026-08-26 fix (Opus blind review C-19): this used to also check
    # `gufi != entry.get("gufi_override", "")` as an OR-arm intended to
    # match a watchlist entry pinned by exact GUFI -- but no such column
    # or write path exists anywhere in the codebase ("gufi_override" was
    # never set on any entry). With the key always missing, the default
    # ("") meant this arm did nothing when a message's GUFI was populated
    # (the normal case) but became a live landmine the moment a message's
    # GUFI parsed empty: `gufi != ""` is then False, collapsing the
    # `and`-guarded skip to False for every entry, so an unrelated flight
    # with an unparsed GUFI would match -- and fire a hit for -- every
    # active watchlist entry regardless of callsign. Callsign matching is
    # the only real mechanism here; removed the dead arm entirely instead
    # of pointing it at a real column, since no such per-entry GUFI-pin
    # feature exists to restore.
    for entry in entries:
        ident = entry["identifier"].upper()
        if callsign != ident:
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
