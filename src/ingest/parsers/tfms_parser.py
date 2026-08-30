"""
ingest.parsers.tfms_parser — FAA TFMS (Traffic Flow Management System) NMS parser.

TFMS delivers NAS traffic management programs over the NMS TFMS VPN:
  - GDP  (Ground Delay Program)
  - GS   (Ground Stop)
  - AFP  (Airspace Flow Program)
  - AAR  (Airport Arrival Rate)
  - FCA  (Flow Constrained Area)

Parsed programs are written to the existing nas_programs table, which the
poller's REST nas.py normally populates. When push:tfms heartbeat is healthy,
the REST fetcher defers automatically.

--- REAL SCHEMA, confirmed 2026-07-20 against 70 real captured samples ---
Root is NOT a bag of gdpElement/GDP/nasProgram/trafficProgram/flowProgram
tags (those guesses never matched anything, why nas_programs was never
populated from this feed). Real structure:

  <ns5:tfmDataService xmlns:ns5="urn:us:gov:dot:faa:atm:tfm:tfmdataservice"
                       xmlns:ns12="urn:us:gov:dot:faa:atm:tfm:flowinformation" ...>
    <ns5:fiOutput>
      <ns12:fiMessage sensitivity="A" sourceFacility="TFMS"
                       sourceTimeStamp="..." msgType="TMI_FLIGHT_LIST">
        <!-- per-flight TMI/reroute/track/flight-plan data, shape varies by msgType -->
      </ns12:fiMessage>
    </ns5:fiOutput>
  </ns5:tfmDataService>

Confirmed real msgType values across 70 samples (2 capture rounds):
  TMI_FLIGHT_LIST       -- per-flight traffic management initiative
                           assignment, references an fcaId (e.g. one whose
                           id was "fca.zdc.lxstn21...", confirming ZDC-area
                           relevance is derivable from this field)
  FlightModify           -- flight plan amendment
  trackInformation       -- position/track data (dominant type in the
                            larger 60-sample batch, 45/60)
  flightPlanInformation  -- flight plan data

CORRECTED 2026-07-20 (later same-day pass): the "not observed" claim above
was wrong -- GDP and GS DO exist as their own genuine fiMessage msgTypes,
they were just falling into the unknown-msgType bypass capture the whole
session because neither was ever added to _KNOWN_MSG_TYPES (the capture
mechanism was working correctly; nothing was routing to it). Confirmed via
tfms_debug_unknown_msgtype/ real samples:
  GDP   -- fi:gdpCompression (program-level Ground Delay Program: airportId,
           center, compression/cumulative/advisory periods, currentDelays/
           anticipatedDelays, slotHoldOverrideList). Real sample: SFO/ZOA.
  GS    -- fi:gsAdvisory (Ground Stop: groundStopPeriod, departure
           FacilitiesIncludedList, aircraftTypesIncluded, impactingCondition).
           Real sample: BOS/ZBW, WIND.
  APTC  -- fi:airportConfigMessage (rolling runway config + arr/dep rate +
           weather category per airport). Real samples: TEB, MIA.
  GADV  -- fi:generalAdvisory (ATCSCC free-text bulletin: advisoryNumber,
           origin, facilities, effectivePeriod, advisoryTitle, advisoryText).
  FXA   -- fi:feaFca (Flow-Constrained-Area / FEA polygon DEFINITION: fcaId,
           fcaName, startTime/endTime, ceiling/floor, lat/lon boundary
           points) -- this is what TMI_FLIGHT_LIST's fcaId actually refers
           to; most abundant unknown fiMessage type (15/60 in one batch).
  TMI_UPDATE -- fi:eramAmendmentStatusUpdate > rrAmendment (per-flight
           reroute amendment APPLY status: tmiId, routeAmendment,
           amendmentStatus APLD/etc, eramStatus).
  AFP   -- fi:afpAdvisory OR fi:afpCancel (Airspace Flow Program
           declaration/cancellation for an FCA element: afpName, program
           rate list, departure scope, delays). ADDED 2026-08-22 -- despite
           being a documented target type at the top of this docstring, it
           was still falling into the same unknown-msgType bypass GDP/GS
           once did (15 real captured samples). See
           _handle_airspace_flow_program.
All of the above now have real handlers (see _handle_ground_delay_program,
_handle_ground_stop, _handle_airspace_flow_program, _handle_airport_config,
_handle_general_advisory,
_handle_fxa, _handle_tmi_update below). GDP/GS/AFP feed nas_programs directly
(this is literally the CPS "gdp" scoring factor's real declaration data --
previously that factor could only ever see REST-polled nas.py's snapshot,
now the push feed populates it live). APTC/GADV don't fit nas_programs'
program-id/start-end shape (rolling status / free text respectively) --
implemented as lightweight DC-area-filtered ntfy alerts instead of new
tables. FXA is cached in-memory (fcaId -> definition) for future sector/
corridor coalescing work, not alerted on its own (a definition update
isn't itself an event). TMI_UPDATE couples into the watchlist system like
TMI_FLIGHT_LIST does.

Also confirmed real (fltdMessage/fltdOutput family, discovered via the
same unknown-msgType capture): FlightRoute -- fdm:ncsmFlightRoute, carries
the actual named SID/STAR procedure per flight (nxcm:dp routeName/routeType,
dpTransitionFix, nxcm:star routeName/routeType, starTransitionFix) plus the
full ordered fix/elapsed-time sequence (flightTraversalData2). This
directly answers "what STAR/corridor is this arrival on" at the per-flight
level, more precisely than the ARTCC-level TBFM corridor inference done
earlier this session. Implemented as _handle_flight_route, watchlist-
coupled. FlightSectors and boundaryCrossingUpdate are lower-value subsets
of data already covered by FlightRoute/trackInformation respectively (see
_KNOWN_UNHANDLED_FLTD_TYPES) -- left unhandled to avoid redundant work.
RAPT (Route Availability Planning Tool convective-blockage timelines) only
observed for NYC metroplex in the small sample seen; DC has no RAPT product
today, so left unhandled as low-value for this platform's geo scope.

Per-session decision (2026-07-20): stash the non-functional tag-guess
logic below as _parse_tfms_message_legacy_guess (kept for reference/in
case some other TFMS product variant really does use that shape), and
scaffold a msgType dispatcher against the confirmed real structure.

--- FOLLOW-UP PASS, 2026-07-21 ---
A live-log check (15k-line window) found the parser's handled-vs-unhandled
mix was actually fine at the program-declaration level -- MIT/TBM/APREQ/
STOP restriction coalescing and GADV advisories were firing correctly the
whole time. The real gap was two more fltdMessage types not yet in scope:
  flightPlanCancellation -- confirmed via 10 real samples, now handled
    (_handle_flight_plan_cancellation): a cancelled flight plan for a
    watchlist entry is a high-priority signal a pickup may not be happening.
  FlightCreate -- confirmed via real samples, now handled
    (_handle_flight_create): earliest possible NAS sighting of a flight
    plan, informational priority.
Also fixed: oceanicReport (247/~470 unhandled-type log lines in the same
15k window -- transoceanic tracks, correctly out of DC scope) and RAPT
(documented as unhandled in the original docstring above but never
actually added to _KNOWN_UNHANDLED_FLTD_TYPES, so it kept spamming the
unknown-type capture/log path) are now properly quieted via that set.
"""
from __future__ import annotations

import json
import logging
import os
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from common import db
from common.push_dedup import PushDedup, content_hash
from shared.watchlist import _fire_ntfy_dual
from ingest.parsers.geo_filter import is_core_airport

log = logging.getLogger("ingest.parsers.tfms")

_DC_FACILITIES = frozenset({"ZDC", "PCT", "KDCA", "KIAD", "KBWI", "DCA", "IAD", "BWI"})
_TFMS_ALERT_DEDUP = PushDedup("tfms_alerts", dedup_secs=1800)

# One-shot full-message debug capture -- 2026-07-20, same technique that
# confirmed tbfm_parser.py's real schema (see that file's docstring). This
# parser's tag guesses (gdpElement/GDP/nasProgram/trafficProgram/flowProgram
# etc.) have never been validated against a real captured message. Capture
# is self-limited to _DEBUG_SAMPLE_MAX writes for the life of the process.
_DEBUG_SAMPLE_DIR = "/var/lib/corporatetraveldc/tfms_debug"
# Bumped 10->60 2026-07-20: first 10 samples were entirely TMI_FLIGHT_LIST /
# FlightModify (per-flight TMI assignments), zero program-declaration-shaped
# messages (GDP/GS/AFP/AAR) -- traffic is high-volume enough that a much
# larger sample still fills in well under a second, worth the extra msgType
# diversity before committing to a rewrite architecture.
_DEBUG_SAMPLE_MAX = 60
_debug_sample_count = 0


def _maybe_capture_debug_sample(xml_bytes: bytes) -> None:
    global _debug_sample_count
    if _debug_sample_count >= _DEBUG_SAMPLE_MAX:
        return
    try:
        os.makedirs(_DEBUG_SAMPLE_DIR, exist_ok=True)
        path = f"{_DEBUG_SAMPLE_DIR}/sample_{_debug_sample_count}.xml"
        with open(path, "wb") as f:
            f.write(xml_bytes)
        _debug_sample_count += 1
        log.info("tfms: wrote debug sample %s (%d bytes)", path, len(xml_bytes))
    except Exception as e:
        log.warning("tfms: debug sample capture failed: %s", e)

# TFMS XML namespace prefixes -- LEGACY guesses, kept only for
# _parse_tfms_message_legacy_guess below (never confirmed against real data).
_TFMS_NS = {
    "tfms": "http://tfms.faa.gov/tfms/v1",
    "nas":  "http://www.faa.aero/nas/4.2",
}

# Real confirmed namespaces, 2026-07-20 (see module docstring).
_TFMS_REAL_NS = {
    "svc":  "urn:us:gov:dot:faa:atm:tfm:tfmdataservice",
    "flow": "urn:us:gov:dot:faa:atm:tfm:flowinformation",
}

# Confirmed real msgType values (see module docstring). RSTR ("restriction
# message") turned out to be the actual program/restriction-declaration
# type -- found and fully implemented same session, see _handle_restriction
# below (real sample: sourceFacility=ZDC, restrictionCategory=MIT,
# restrictionType=DEPARTURE, airports=BWI -- exactly the kind of DC-area
# restriction nas_programs needs). APTC (airport configuration/arrival-
# rate) and GADV (general advisory text) were also found but are stubbed --
# APTC likely feeds CPS scoring rather than nas_programs (a schema/design
# question for the dedicated session), GADV is free-text advisory bulletins
# that would need a new table. Any msgType still not in this set gets
# routed to _handle_unknown_msg_type / captured for review.
# fiOutput/fiMessage family types ONLY -- FlightModify/trackInformation/
# flightPlanInformation turned out to belong to the separate fltdMessage
# family instead (see _FLTD_MSG_TYPE_HANDLERS / _KNOWN_UNHANDLED_FLTD_TYPES
# below), corrected 2026-07-20 3pm session. GDP/GS/FXA/TMI_UPDATE added
# later the same day once the unknown-msgType capture surfaced them (see
# corrected module docstring above). AFP added 2026-08-22, same discovery
# path (15 real captured samples in the bypass directory).
_KNOWN_MSG_TYPES = frozenset({
    "TMI_FLIGHT_LIST", "RSTR", "APTC", "GADV", "GDP", "GS", "FXA", "TMI_UPDATE",
    "AFP",
})

# Real namespace prefix used by RSTR/APTC/GADV-style messages (distinct
# from the ns5/ns12-style prefixes seen on TMI_FLIGHT_LIST samples -- FAA's
# XML uses arbitrary prefix names per message, so matching must be by
# namespace URI or local tag name, never by literal prefix string).
_FCM_NS = "urn:us:gov:dot:faa:atm:tfm:ficommonmessages"


def _fcm_text(elem: ET.Element, tag: str) -> str | None:
    """Find a direct child by local tag name, namespace-agnostic."""
    for child in elem:
        if child.tag.split("}")[-1] == tag:
            return (child.text or "").strip() or None
    return None


# ── Per-flight (fltdMessage) helpers, 2026-07-20 3pm session ─────────────────
# Confirmed real structure: a SEPARATE message family from fiOutput/fiMessage
# above -- root is ds:tfmDataService > fltdOutput > fdm:fltdMessage[acid,
# airline, arrArpt, cdmPart, depArpt, fdTrigger, flightRef, major, msgType,
# sensitivity, sourceFacility, sourceTimeStamp], and CRITICALLY a single
# document commonly batches 2-5 fltdMessage children (confirmed: up to 5 in
# real captures), unlike fiOutput which has shown exactly one fiMessage per
# document so far. The original dispatcher only ever looked for "fiMessage"
# via root.iter() with a break-on-first-match -- it never even found these
# fltdMessage-shaped documents, regardless of msgType, so trackInformation/
# FlightModify/flightPlanInformation were unreachable dead code paths even
# though they were already in _KNOWN_MSG_TYPES. Fixed below.

def _local(tag: str) -> str:
    return tag.split("}")[-1] if "}" in tag else tag


def _find_child(elem: ET.Element | None, tag: str) -> ET.Element | None:
    """First direct child matching local tag name, ignoring namespace prefix."""
    if elem is None:
        return None
    for child in elem:
        if _local(child.tag) == tag:
            return child
    return None


def _find_path(elem: ET.Element | None, *tags: str) -> ET.Element | None:
    cur = elem
    for t in tags:
        cur = _find_child(cur, t)
        if cur is None:
            return None
    return cur


def _text(elem: ET.Element | None) -> str | None:
    return (elem.text or "").strip() or None if elem is not None else None


def _first_present(*elems: ET.Element | None) -> ET.Element | None:
    """First non-None candidate, in order.

    2026-08-23: use this instead of `_find_child(a, t) or _find_child(b, t)`
    to pick between candidate elements. ElementTree elements are truthy
    based on child COUNT, not identity -- a genuinely-found but childless
    leaf element (e.g. a bare <fce:startTime>...</fce:startTime> with text
    but no child elements) evaluates `bool(elem) is False`, so an `or`
    chain silently discards it and falls through to the next candidate
    even though it was present. Same bug class already found once in
    fdps_parser.py (see tests/ingest/test_fdps_element_truthiness.py) and
    again in _handle_ground_delay_program/_handle_ground_stop below --
    both used the `or` form and both silently keyed off the wrong
    candidate element as a result. `is not None` is the only test that
    means what it looks like it means for an Element.
    """
    for e in elems:
        if e is not None:
            return e
    return None


def _dms_to_decimal(dms_elem: ET.Element | None) -> float | None:
    """Convert a *DMS element (degrees/minutes/seconds/direction attrs) to
    signed decimal degrees. Confirmed real shape:
        <nxce:latitudeDMS degrees="38" direction="NORTH" minutes="02" seconds="09"/>
        <nxce:longitudeDMS degrees="095" direction="WEST" minutes="07" seconds="02"/>
    """
    if dms_elem is None:
        return None
    try:
        deg = float(dms_elem.get("degrees", "0"))
        mins = float(dms_elem.get("minutes", "0"))
        secs = float(dms_elem.get("seconds", "0"))
    except ValueError:
        return None
    value = deg + mins / 60.0 + secs / 3600.0
    direction = (dms_elem.get("direction") or "").upper()
    if direction in ("SOUTH", "WEST"):
        value = -value
    return value


def _qualified_aircraft_id(fltd_msg: ET.Element) -> tuple[str | None, str | None, str | None, str | None]:
    """Extract (callsign, gufi, origin, destination) from the
    qualifiedAircraftId block common to every fltdMessage body type."""
    qid = None
    for child in fltd_msg:
        if _local(child.tag) == "qualifiedAircraftId":
            qid = child
            break
        # qualifiedAircraftId is usually one level down, inside the
        # msgType-named body element (e.g. ncsmFlightModify, trackInformation)
        inner = _find_child(child, "qualifiedAircraftId")
        if inner is not None:
            qid = inner
            break
    if qid is None:
        return None, None, None, None
    callsign = _text(_find_child(qid, "aircraftId"))
    gufi = _text(_find_child(qid, "gufi"))
    origin = _text(_find_path(qid, "departurePoint", "airport"))
    destination = _text(_find_path(qid, "arrivalPoint", "airport"))
    return callsign, gufi, origin, destination


def _match_watchlist_flight(identifier: str | None) -> dict | None:
    """Find an active flight watchlist entry matching this identifier
    (callsign), case-insensitive. Same matching convention as
    fdps_parser.py's check_fdps_watchlist."""
    if not identifier:
        return None
    try:
        from shared.watchlist import get_active_entries
        entries = get_active_entries(entry_type="flight")
    except Exception as e:
        log.error("tfms: watchlist lookup failed: %s", e)
        return None
    ident_upper = identifier.upper().strip()
    for entry in entries:
        if entry["identifier"].upper() == ident_upper:
            return entry
    return None


def _fire_tfms_watchlist_hit(entry: dict, summary: str, detail: dict, priority: int = 3) -> None:
    try:
        from shared.watchlist import watchlist_event_hit
        watchlist_event_hit(entry["id"], summary, detail, priority=priority)
    except Exception as e:
        log.error("tfms: watchlist_event_hit failed for %s: %s", entry.get("id"), e)

_UNKNOWN_MSGTYPE_DIR = "/var/lib/corporatetraveldc/tfms_debug_unknown_msgtype"
_UNKNOWN_MSGTYPE_MAX = 15
_unknown_msgtype_count = 0


def _capture_unknown_msgtype_sample(xml_bytes: bytes, msg_type: str) -> None:
    """Always-on (bypasses the generic cap) capture for any msgType not in
    _KNOWN_MSG_TYPES -- a GDP/GS/AFP/AAR/FCA declaration, if one ever fires,
    would show up here as a first sighting rather than being silently
    handled by the unknown-type stub below."""
    global _unknown_msgtype_count
    if _unknown_msgtype_count >= _UNKNOWN_MSGTYPE_MAX:
        return
    try:
        os.makedirs(_UNKNOWN_MSGTYPE_DIR, exist_ok=True)
        safe_type = "".join(c if c.isalnum() else "_" for c in msg_type) or "empty"
        path = f"{_UNKNOWN_MSGTYPE_DIR}/{safe_type}_{_unknown_msgtype_count}.xml"
        with open(path, "wb") as f:
            f.write(xml_bytes)
        _unknown_msgtype_count += 1
        log.info("tfms: wrote UNKNOWN msgType=%r sample %s (%d bytes)",
                  msg_type, path, len(xml_bytes))
    except Exception as e:
        log.warning("tfms: unknown-msgType sample capture failed: %s", e)

# Map raw TFMS type codes to canonical form used in nas_programs.type
_TYPE_MAP = {
    "GDP": "GDP",
    "GS":  "GS",
    "AFP": "AFP",
    "AAR": "AAR",
    "FCA": "FCA",
    # Aliases sometimes seen in TFMS messages
    "GROUND_DELAY_PROGRAM": "GDP",
    "GROUND_STOP":          "GS",
    "AIRSPACE_FLOW_PROGRAM":"AFP",
}


def _txt(elem: ET.Element | None, *tags: str) -> str | None:
    """Walk a chain of child tags; return text of the last or None."""
    cur = elem
    for tag in tags:
        if cur is None:
            return None
        # Try bare tag, then with each known namespace
        found = cur.find(tag)
        if found is None:
            for uri in _TFMS_NS.values():
                found = cur.find(f"{{{uri}}}{tag}")
                if found is not None:
                    break
        cur = found
    return (cur.text or "").strip() or None if cur is not None else None


def _ts_to_epoch(ts: str | None) -> float | None:
    """Parse ISO-8601 or YYYYMMDDHHMMSS timestamp to Unix epoch."""
    if not ts:
        return None
    ts = ts.strip()
    # ISO 8601
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except ValueError:
        pass
    # TFMS compact format: YYYYMMDDHHMMSS
    for fmt in ("%Y%m%d%H%M%S", "%Y%m%d%H%M"):
        try:
            return datetime.strptime(ts, fmt).replace(
                tzinfo=timezone.utc).timestamp()
        except ValueError:
            pass
    return None


def _parse_single_program(elem: ET.Element, raw_xml: str) -> dict | None:
    """Extract fields from a single program element."""
    # Program ID — try several possible element names
    program_id = (
        _txt(elem, "programId") or
        _txt(elem, "nasId") or
        _txt(elem, "id")
    )
    if not program_id:
        return None

    raw_type = (
        _txt(elem, "type") or
        _txt(elem, "programType") or
        elem.tag.split("}")[-1].upper()  # fall back to element tag name
    )
    prog_type = _TYPE_MAP.get((raw_type or "").upper(), raw_type or "UNKNOWN")

    facility = (
        _txt(elem, "airport") or
        _txt(elem, "facility") or
        _txt(elem, "affectedFacility") or
        _txt(elem, "center")
    )

    payload = {
        "program_id": program_id,
        "type": prog_type,
        "facility": facility,
        "start_time": _ts_to_epoch(_txt(elem, "startTime") or _txt(elem, "gdpStart")),
        "end_time":   _ts_to_epoch(_txt(elem, "endTime")   or _txt(elem, "gdpEnd")),
        "reason":     _txt(elem, "reason") or _txt(elem, "initiationReason"),
        "status":     _txt(elem, "status") or "ACTIVE",
        "source":     "swim_tfms",
    }
    return payload


def _tfms_program_metric(program: dict) -> str:
    """Return the human-readable METRIC phrase for a TFMS program, honest to
    what kind of program it actually is.

    2026-08-16 drift audit (operator-confirmed live): the alert display used
    to hardcode 'avg delay +{avg_delay_minutes, "?"}min' for EVERY program.
    That is only meaningful for GDP/GS (ground delay / ground stop delay
    programs, which _handle_gdp/_handle_gs DO populate with avg_delay_minutes).
    But the overwhelming majority of live TFMS alerts are RSTR restrictions
    -- MIT/MINIT/APREQ/STOP -- built by _handle_restriction, which never sets
    an avg_delay_minutes key at all (a MIT is a miles-in-trail SPACING, not a
    delay; APREQ/STOP have no numeric delay). So `.get('avg_delay_minutes',
    '?')` returned the '?' fallback and every MIT/APREQ/STOP alert read
    "avg delay +?min", which is both wrong and useless. Confirmed against
    live nas_programs rows: MIT LGA carries mit_value="30" (30 NM in-trail),
    APREQ JFK carries mit_value=null, neither carries any avg delay.

    Root-cause fix: pick the metric that actually exists on this program.
      - GDP/GS  -> "avg delay +Nmin"  (delay programs; unchanged behavior)
      - MIT     -> "N NM in-trail"    (miles-in-trail spacing)
      - MINIT   -> "N min in-trail"   (minutes-in-trail spacing)
      - anything with a mit_value but another type -> "N in-trail"
      - APREQ / STOP / others with no metric -> "" (no fake number)
    """
    ptype = (program.get("type") or "").upper()
    avg = program.get("avg_delay_minutes")
    if avg is not None and str(avg).strip() not in ("", "None"):
        return f"avg delay +{avg}min"
    mit = program.get("mit_value")
    if mit is not None and str(mit).strip() not in ("", "None"):
        if ptype == "MINIT":
            return f"{mit} min in-trail"
        if ptype == "MIT":
            return f"{mit} NM in-trail"
        return f"{mit} in-trail"
    return ""


def check_tfms_alerts(programs: list[dict]) -> None:
    """
    Fire nas-alerts ntfy for any TFMS program affecting DC-area facilities.

    UPDATED 2026-07-20 -- routes through shared.sector_coalesce instead of
    calling _fire_ntfy_dual directly, per operator direction on sector/
    corridor coalescing (Task #20). This gets RSTR/GDP/GS programs (the
    only ones that reach this function -- GADV/APTC have their own direct
    alert paths, wired separately below) a resolved sector tag, rolling
    escalation detection, and per-sector/per-feed silence support, all
    still deduped the same way as before (content_hash on type+facility)
    so repeated identical program broadcasts don't spam even when
    escalating.

    UPDATED 2026-08-02 per operator direction: no longer fires to the
    shared "nas-alerts" bucket. Routes through
    shared.sector_coalesce.fire_family_alert("tfms", ...), which fires the
    escalating-only aggregate "tfms-alerts" (any facility) plus, when the
    facility resolves to one of the 8 tracked zones, "tfms-<zone>" -- same
    family pattern as tbfm-alerts/tbfm-<zone>, independently threshold-
    tunable per zone via set_escalate_threshold("tfms", "<ZONE_NAME>", ...).
    NOTE: this only moves TFMS traffic off nas-alerts -- aim_parser.py's
    NOTAM alerts still publish to nas-alerts as of this change; that's a
    separate rename (nas-alerts -> aim_fns-alerts) not yet done, flagged
    to the operator pending confirmation given its much higher volume.
    """
    from shared.sector_coalesce import fire_family_alert, is_tracked_facility

    for program in programs:
        facility = program.get("facility") or ""
        # WIDENED 2026-08-03 per operator direction: this was hardcoded to
        # _DC_FACILITIES (DC-area only) despite the docstring above already
        # describing an "8 sectors" design matching TBFM -- TFMS never
        # actually reached the other 7 zones because this gate silently
        # dropped everything else before fire_family_alert was ever called.
        # is_tracked_facility() checks against the same 8-zone map TBFM
        # already resolves against unrestricted, so TFMS now genuinely
        # matches TBFM's scope instead of just claiming to in a comment.
        if not is_tracked_facility(facility):
            continue
        # 2026-08-16: found live -- program_key (per-program identity: WHICH
        # program) was being passed as should_push/record's CONTENT_KEY
        # position, with the literal constant string "tfms" passed as KEY
        # instead. should_push's `key` is the STABLE SLOT -- since every
        # distinct program (APREQ:PDX, MIT:ATL, MIT:IAH, ...) shared the one
        # "tfms" slot, each different program's rebroadcast overwrote the
        # slot and made the NEXT program (or the same one, next cycle) look
        # "content changed" against whatever was stored last, defeating the
        # 30-min dedup window entirely for interleaved programs -- exactly
        # what caused a 3-4min repeat-spam of the same handful of programs.
        # Confirmed present unchanged since this call site's introduction
        # (git log -L on this range: 3377a8e, b119b21 both carried it
        # forward through unrelated refactors, never touched it).
        # Fix: program_key (per-program identity) is the slot key; a hash
        # of the actually-varying content (delay minutes + reason) is the
        # content key, so a genuinely changed delay can still break through
        # sooner, but an unchanged rebroadcast of the SAME program stays
        # suppressed for the full 30 min like it was always supposed to.
        program_key = content_hash(f"{program['type']}:{facility}")
        # 2026-08-16 drift audit: content_key now includes mit_value, not just
        # avg_delay_minutes. For a MIT/MINIT restriction avg_delay_minutes is
        # always None, so the old key ("None:{reason}") never changed when the
        # spacing value itself changed (20 NM -> 30 NM, same reason), wrongly
        # suppressing a genuinely tightened restriction for the full 30-min
        # window. Keying on the actual varying metric fixes that.
        metric = _tfms_program_metric(program)
        content_key = content_hash(
            f"{program.get('avg_delay_minutes')}:{program.get('mit_value')}:{program.get('reason')}"
        )
        if not _TFMS_ALERT_DEDUP.should_push(program_key, content_key):
            continue
        title = f"TFMS {program['type']} — {facility}"
        reason = program.get("reason") or ""
        # Only include the metric segment when this program actually has one
        # (GDP/GS delay, MIT/MINIT spacing) -- APREQ/STOP show type + reason
        # with no fabricated "+?min".
        _metric_seg = f": {metric}" if metric else ""
        detail = f"{program['type']} {facility}{_metric_seg}" + (f" | {reason}" if reason else "")
        dispatch = f"{facility} {program['type']}" + (f" {metric}" if metric else "")
        try:
            result = fire_family_alert(
                "tfms", "tfms", facility, title, detail, dispatch, base_priority=3,
            )
            _TFMS_ALERT_DEDUP.record(program_key, content_key)
            log.info("tfms: alert coalesced for %s %s -> sector=%s escalating=%s fired=%s zone_fired=%s",
                      program['type'], facility, result.get("sector"),
                      result.get("escalating"), result.get("fired"), result.get("zone_fired"))
        except Exception as e:
            log.error("tfms: nas-alert fire failed for %s: %s", facility, e)


def parse_tfms_message(xml_bytes: bytes) -> list[dict]:
    """
    Entry point / msgType dispatcher for a TFMS NMS XML message.

    --- FIXED 2026-07-20 3pm session ---
    TFMS actually emits TWO distinct message-family shapes under the same
    tfmDataService root, confirmed against real captures:
      1. fiOutput > fiMessage[msgType]           -- TMI_FLIGHT_LIST, RSTR,
         APTC, GADV. One fiMessage per document in every sample seen so far.
      2. fltdOutput > fltdMessage[msgType]       -- FlightModify, FlightTimes,
         trackInformation, flightPlanInformation, departureInformation,
         arrivalInformation, flightPlanAmendmentInformation. COMMONLY
         batches 2-5 fltdMessage children per document (confirmed up to 5).

    The original dispatcher only searched for "fiMessage" and broke on the
    first match -- fltdMessage-shaped documents were silently invisible to
    it regardless of msgType, even though trackInformation/FlightModify/
    flightPlanInformation were already listed in _KNOWN_MSG_TYPES. This is
    why those stubs never got a chance to run. Now routes both families,
    handling every fltdMessage in a batch rather than just the first.
    """
    if not xml_bytes:
        return []
    _maybe_capture_debug_sample(xml_bytes)
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        log.warning("tfms: XML parse error: %s", e)
        return []

    fi_output = _find_child(root, "fiOutput")
    fltd_output = _find_child(root, "fltdOutput")

    if fi_output is None and fltd_output is None:
        # Not either known shape -- fall back to the legacy guess-based
        # parser in case some other TFMS product variant uses that shape.
        return _parse_tfms_message_legacy_guess(xml_bytes, root)

    programs: list[dict] = []

    if fi_output is not None:
        for fi_message in fi_output:
            if _local(fi_message.tag) != "fiMessage":
                continue
            msg_type = fi_message.get("msgType") or ""
            if msg_type not in _KNOWN_MSG_TYPES:
                _capture_unknown_msgtype_sample(xml_bytes, msg_type)
                log.info("tfms: unknown fiMessage msgType=%r captured for review", msg_type)
                continue
            handler = _MSG_TYPE_HANDLERS.get(msg_type)
            if handler is None:
                continue
            result = handler(fi_message)
            if result:
                programs.extend(result)

    if fltd_output is not None:
        for fltd_message in fltd_output:
            if _local(fltd_message.tag) != "fltdMessage":
                continue
            msg_type = fltd_message.get("msgType") or ""
            handler = _FLTD_MSG_TYPE_HANDLERS.get(msg_type)
            if handler is None:
                if msg_type not in _KNOWN_UNHANDLED_FLTD_TYPES:
                    _capture_unknown_msgtype_sample(xml_bytes, msg_type)
                    log.info("tfms: unhandled fltdMessage msgType=%r captured for review", msg_type)
                continue
            # fltdMessage handlers do their own watchlist side-effect and
            # never contribute to nas_programs -- no return value to collect.
            try:
                handler(fltd_message)
            except Exception as e:
                log.error("tfms: fltdMessage handler error (msgType=%s): %s", msg_type, e)

    if programs:
        check_tfms_alerts(programs)

    return programs


def _handle_tmi_flight_list(fi_message: ET.Element) -> list[dict]:
    """
    IMPLEMENTED 2026-07-20 3pm -- per-flight TMI/flow-constraint assignment.
    Confirmed real structure:
        tmiFlightDataList > flightData > flight[aircraftId, gufi, igtd,
            departurePoint/airport, arrivalPoint/airport]
          > flightReference, status
          > tmiFlightInfoList > tmi[updateType, lastUpdateTime] > fcaId
              > fxaFlightData > fxaFlight
                  > fxaId[fcaId, fcaName, lastUpdate]
                  > bentryTm/createTm/eentryTm/entryTm/exitTm/
                    extendedExitTm/ientryTm/oentryTm (flow-constrained-area
                    boundary timing estimates -- NOT airline OOOI; see
                    _handle_flight_times below for real OOOI data)
                  > entryLat/entryLon/entryHeading, exitInd

    Direction locked in 2026-07-20 (the operator): couple into the existing OOOI
    watchlist system rather than a new standalone table. Implemented as:
    match on aircraftId against the flight watchlist, and if matched, fire
    a watchlist_event_hit tagged watchlist_trigger="tfms_tmi" carrying the
    fcaName (human-readable constraint name, e.g. "ZTLKE_SLOJO") and the
    boundary timing estimates -- this lands in watchlist_history where it's
    directly joinable against the flight's actual OOOI events (from
    FlightTimes/departureInformation/arrivalInformation below) by entry_id.
    Does not return nas_programs rows (this isn't program data).
    """
    flight_data = _find_path(fi_message, "tmiFlightDataList", "flightData")
    if flight_data is None:
        return []

    flight_elem = _find_child(flight_data, "flight")
    callsign = _text(_find_child(flight_elem, "aircraftId")) if flight_elem is not None else None
    gufi = _text(_find_child(flight_elem, "gufi")) if flight_elem is not None else None

    entry = _match_watchlist_flight(callsign)
    if entry is None:
        return []

    tmi_info = _find_child(flight_data, "tmiFlightInfoList")
    fca_id = _text(_find_path(tmi_info, "tmi", "fcaId"))
    fxa_flight = _find_path(tmi_info, "fxaFlightData", "fxaFlight")
    fca_name = _text(_find_path(fxa_flight, "fxaId", "fcaName")) if fxa_flight is not None else None
    entry_tm = _text(_find_child(fxa_flight, "entryTm")) if fxa_flight is not None else None
    exit_tm = _text(_find_child(fxa_flight, "exitTm")) if fxa_flight is not None else None
    exit_ind = _text(_find_child(fxa_flight, "exitInd")) if fxa_flight is not None else None

    summary = f"TMI assignment: {fca_name or fca_id or 'unnamed constraint'}"
    detail = {
        "watchlist_trigger": "tfms_tmi",
        "gufi": gufi,
        "fca_id": fca_id,
        "fca_name": fca_name,
        "constraint_entry_time": entry_tm,
        "constraint_exit_time": exit_tm,
        "exit_indicator": exit_ind,
    }
    _fire_tfms_watchlist_hit(entry, summary, detail, priority=3)
    return []


def _handle_flight_times(fltd_message: ET.Element) -> None:
    """
    IMPLEMENTED 2026-07-20 3pm -- FlightModify/FlightTimes messages carry
    real airline OOOI data (flightTimeData: airlineOutTime/airlineOffTime/
    airlineOnTime/airlineInTime -- literally OUT/OFF/ON/IN), confirmed real
    structure:
        ncsmFlightModify|ncsmFlightTimes > qualifiedAircraftId[...]
          > airlineData > flightStatusAndSpec[flightStatus, aircraftModel]
          > eta[etaType, timeValue], etd[etdType, timeValue]
          > flightTimeData[airlineInTime, airlineOffTime, airlineOnTime,
                           airlineOutTime, flightCreation, originalArrival,
                           originalDeparture]
          > arrivalFixAndTime[arrTime, fixName]
    (FlightTimes samples omit flightTimeData/airlineData -- only FlightModify
    carries the full OOOI block in captures seen so far; both share the same
    qualifiedAircraftId + eta/etd shape either way.)

    This is the actual OOOI coupling the watchlist direction calls for --
    matched flights get a watchlist_event_hit tagged watchlist_trigger=
    "tfms_ooOi" carrying whichever OOOI times are present, joinable in
    watchlist_history against tfms_tmi hits from the same flight.
    """
    callsign, gufi, origin, destination = _qualified_aircraft_id(fltd_message)
    entry = _match_watchlist_flight(callsign)
    if entry is None:
        return

    # 2026-08-23: was `_find_child(a, t) or _find_child(b, t)` -- see
    # _first_present() docstring. Not confirmed to have manifested here
    # (both candidates carry real nested structure in every captured
    # sample so far), but fixed defensively during the audit that found
    # the confirmed instances in the GDP/GS handlers below, since a truly
    # empty ncsmFlightModify would have silently and incorrectly fallen
    # through to ncsmFlightTimes.
    body = _first_present(_find_child(fltd_message, "ncsmFlightModify"),
                           _find_child(fltd_message, "ncsmFlightTimes"))
    airline_data = _find_child(body, "airlineData") if body is not None else None
    times_container = airline_data if airline_data is not None else body
    flight_time_data = _find_child(times_container, "flightTimeData") if times_container is not None else None

    ooooi = {}
    original_departure, original_arrival = None, None
    if flight_time_data is not None:
        for key in ("airlineOutTime", "airlineOffTime", "airlineOnTime", "airlineInTime"):
            val = flight_time_data.get(key)
            if val:
                ooooi[key] = val
        # 2026-08-20: these two were already documented in this function's
        # own docstring (flightTimeData[..., originalArrival,
        # originalDeparture]) but never actually read -- they're TFMS's
        # scheduled times, the missing half of a real on-time computation.
        # See db.get_flight_ontime_history().
        original_departure = flight_time_data.get("originalDeparture") or None
        original_arrival = flight_time_data.get("originalArrival") or None

    flight_status = _text(_find_path(times_container, "flightStatusAndSpec", "flightStatus"))

    # 2026-08-20: persist durably (flight_ooooi_times) in addition to the
    # transient watchlist alert below -- previously this data only ever
    # fired a watchlist_event_hit and was discarded, so no historical
    # on-time/delay rate could ever be computed for a flight number. Runs
    # even when ooooi is empty, since a FlightTimes message can carry
    # original_departure/original_arrival with no actual OOOI times yet
    # (the schedule is known before the flight moves).
    if gufi and (ooooi or original_departure or original_arrival):
        try:
            db.upsert_flight_ooooi(
                gufi=gufi, callsign=callsign, origin=origin, destination=destination,
                airline_out_time=ooooi.get("airlineOutTime"),
                airline_off_time=ooooi.get("airlineOffTime"),
                airline_on_time=ooooi.get("airlineOnTime"),
                airline_in_time=ooooi.get("airlineInTime"),
                original_departure=original_departure, original_arrival=original_arrival,
                flight_status=flight_status,
            )
        except Exception as e:
            log.error("tfms: upsert_flight_ooooi failed for %s (%s): %s", callsign, gufi, e)

    # 2026-08-28 (operator directive: "let's wire in those flight OOOOI
    # times from TFMS as well"): the real airline-reported OUT/OFF/ON/IN
    # times just computed above were, until now, ONLY ever persisted to
    # flight_ooooi_times (for on-time-rate analytics) or fired as a
    # one-shot watchlist alert below -- never used to actually advance
    # this entry's own oooi_phase, which every notification and live
    # status check reads. Prefer the LATEST phase present on this message
    # (in > on > off > out) so a message carrying multiple times at once
    # doesn't undercount. update_watchlist_oooi_phase_authoritative()
    # rejects a regressive or lower-authority-tied update on its own --
    # see common/db.py's _OOOI_SOURCE_PRIORITY -- so this is safe to call
    # unconditionally whenever any OOOI time is present.
    if ooooi:
        _tfms_phase, _tfms_time = None, None
        for _key, _phase in (("airlineInTime", "in"), ("airlineOnTime", "on"),
                             ("airlineOffTime", "off"), ("airlineOutTime", "out")):
            if _key in ooooi:
                _tfms_phase, _tfms_time = _phase, ooooi[_key]
                break
        if _tfms_phase:
            try:
                db.update_watchlist_oooi_phase_authoritative(
                    entry["id"], _tfms_phase, source="tfms",
                    updated_at=_tfms_time or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                )
            except Exception as e:
                log.debug("tfms: authoritative OOOI update failed for %s (non-fatal): %s", callsign, e)

    if not ooooi:
        # No OOOI times on this particular message (plain FlightTimes without
        # airlineData) -- not worth a watchlist hit on its own. Persistence
        # above already happened if there was a schedule-only update.
        return

    # Operator directive 2026-08-22: the moment OUT gets pushed, force an
    # immediate hex/tail resolve if this entry doesn't have one yet --
    # don't wait for poller's own sweep interval (up to 120s,
    # FLIGHT_SWEEP_INTERVAL in poller/main.py). Hardcoded to be entirely
    # optional and defensive: a flight with no resolved hex/tail on a
    # scheduled (pre-OUT) update is the normal, expected state for most of
    # a flight's life and must never fail or block anything here -- this
    # only even attempts a resolve when airlineOutTime is newly present in
    # THIS message, and any failure (network, no live contact yet, etc.)
    # is swallowed exactly like every other best-effort call in this
    # function. resolve_flight_identity() itself fires the "identity
    # resolved" notification (hex/tail + airplanes.live tracking URL) the
    # moment it succeeds -- nothing further to do here if it does.
    if "airlineOutTime" in ooooi and not entry.get("hex_id"):
        try:
            from shared.watchlist import resolve_flight_identity
            resolve_flight_identity(entry, callsign, source="tfms_out")
        except Exception as e:
            log.debug("tfms: forced identity resolve at OUT failed for %s (non-fatal): %s",
                      callsign, e)

    # Operator directive 2026-08-23: the moment a watchlisted flight goes
    # airborne (real airlineOffTime), extend its auto_remove_at by however
    # late it actually departed -- see
    # shared.watchlist.extend_auto_remove_for_delay() for the full
    # rationale and the once-only guard (idempotent against a resent
    # airlineOffTime on a later message).
    if "airlineOffTime" in ooooi:
        try:
            from shared.watchlist import extend_auto_remove_for_delay
            extend_auto_remove_for_delay(entry, ooooi["airlineOffTime"],
                                         original_departure, original_arrival)
        except Exception as e:
            log.debug("tfms: auto_remove_at delay extension at OFF failed for %s (non-fatal): %s",
                      callsign, e)

    summary = f"OOOI update: {', '.join(f'{k}={v}' for k, v in ooooi.items())}"
    detail = {
        "watchlist_trigger": "tfms_ooooi",
        "gufi": gufi,
        "origin": origin,
        "destination": destination,
        "flight_status": flight_status,
        **ooooi,
    }
    _fire_tfms_watchlist_hit(entry, summary, detail, priority=3)


def _handle_departure_information(fltd_message: ET.Element) -> None:
    """
    IMPLEMENTED -- departureInformation: an actual OUT/OFF event (real
    airport departure), confirmed structure:
        departureInformation > qualifiedAircraftId[...]
          > timeOfDeparture[estimated="false"/"true"]
          > ncsmFlightTimeData > etd[etdType="ACTUAL", timeValue], eta[...]
    """
    callsign, gufi, origin, destination = _qualified_aircraft_id(fltd_message)
    entry = _match_watchlist_flight(callsign)
    if entry is None:
        return
    body = _find_child(fltd_message, "departureInformation")
    time_of_dep = _find_child(body, "timeOfDeparture") if body is not None else None
    dep_time = _text(time_of_dep)
    estimated = (time_of_dep.get("estimated") if time_of_dep is not None else None) == "true"
    summary = f"Departed {origin or '?'} ({'estimated' if estimated else 'actual'} {dep_time or '?'})"
    detail = {
        "watchlist_trigger": "tfms_departure",
        "gufi": gufi, "origin": origin, "destination": destination,
        "departure_time": dep_time, "estimated": estimated,
    }
    _fire_tfms_watchlist_hit(entry, summary, detail, priority=4)


def _handle_arrival_information(fltd_message: ET.Element) -> None:
    """
    IMPLEMENTED -- arrivalInformation: an actual ON/IN event (real airport
    arrival), confirmed structure mirrors departureInformation:
        arrivalInformation > qualifiedAircraftId[...]
          > timeOfArrival[estimated="false"/"true"]
          > ncsmFlightTimeData > etd[...], eta[etaType="ACTUAL", timeValue]
    """
    callsign, gufi, origin, destination = _qualified_aircraft_id(fltd_message)
    entry = _match_watchlist_flight(callsign)
    if entry is None:
        return
    body = _find_child(fltd_message, "arrivalInformation")
    time_of_arr = _find_child(body, "timeOfArrival") if body is not None else None
    arr_time = _text(time_of_arr)
    estimated = (time_of_arr.get("estimated") if time_of_arr is not None else None) == "true"
    summary = f"Arrived {destination or '?'} ({'estimated' if estimated else 'actual'} {arr_time or '?'})"
    detail = {
        "watchlist_trigger": "tfms_arrival",
        "gufi": gufi, "origin": origin, "destination": destination,
        "arrival_time": arr_time, "estimated": estimated,
    }
    _fire_tfms_watchlist_hit(entry, summary, detail, priority=4)


def _handle_track_information(fltd_message: ET.Element) -> None:
    """
    IMPLEMENTED -- trackInformation: en-route position ping, the dominant
    msgType by volume (45/60 in the larger sample). Confirmed structure:
        trackInformation > qualifiedAircraftId[...]
          > speed, reportedAltitude/assignedAltitude/simpleAltitude
          > position/latitude/latitudeDMS[...], longitude/longitudeDMS[...]
          > timeAtPosition
          > ncsmTrackData > eta[...], arrivalFixAndTime[arrTime, fixName],
                            departureFixAndTime[...], nextEvent[lat/lon decimal]

    Too high-volume to fire a watchlist hit on every ping (would flood
    watchlist_history and spend the whole dedup window on noise). Only
    fires when a watched flight's TFMS-reported ETA puts it within 30
    minutes of arrival -- mirrors FDPS's existing _maybe_alert_on_approach
    pattern so a watched flight gets ONE "getting close" ping from TFMS
    data too, not continuous position spam.
    """
    callsign, gufi, origin, destination = _qualified_aircraft_id(fltd_message)
    entry = _match_watchlist_flight(callsign)
    if entry is None:
        return

    body = _find_child(fltd_message, "trackInformation")
    track_data = _find_child(body, "ncsmTrackData") if body is not None else None
    eta_elem = _find_child(track_data, "eta") if track_data is not None else None
    eta_str = eta_elem.get("timeValue") if eta_elem is not None else None
    if not eta_str:
        return
    try:
        eta_dt = datetime.fromisoformat(eta_str.replace("Z", "+00:00"))
        mins_out = (eta_dt - datetime.now(timezone.utc)).total_seconds() / 60.0
    except ValueError:
        return
    if mins_out < 0 or mins_out > 30:
        return

    # 2026-08-16: same shared-slot bug as _parse_single_program's TFMS
    # program alerts (see that fix's comment for the full explanation) --
    # the literal "tfms_track" constant was the key, collapsing every
    # distinct watchlist entry's approach alert into one shared dedup slot.
    # Fixed the same way: per-entry dedup_key as the slot, a constant
    # content_key (this alert type has no varying content to track --
    # it's a one-shot "already alerted for this entry" gate).
    dedup_key = content_hash(f"tfms:approach:{entry['id']}")
    if not _TFMS_ALERT_DEDUP.should_push(dedup_key, "approach"):
        return

    lat = _dms_to_decimal(_find_path(body, "position", "latitude", "latitudeDMS"))
    lon = _dms_to_decimal(_find_path(body, "position", "longitude", "longitudeDMS"))
    summary = f"{mins_out:.0f} min from {destination or '?'} (TFMS track)"
    detail = {
        "watchlist_trigger": "tfms_track_approach",
        "gufi": gufi, "origin": origin, "destination": destination,
        "eta": eta_str, "latitude": lat, "longitude": lon,
        "minutes_out": round(mins_out, 1),
    }
    _fire_tfms_watchlist_hit(entry, summary, detail, priority=3)
    _TFMS_ALERT_DEDUP.record(dedup_key, "approach")


def _handle_flight_plan_amendment(fltd_message: ET.Element) -> None:
    """
    IMPLEMENTED -- flightPlanAmendmentInformation: route/altitude/speed
    change, confirmed structure:
        flightPlanAmendmentInformation > qualifiedAircraftId[...]
          > amendmentData > newFlightAircraftSpecs, newSpeed/filedTrueAirSpeed,
              newCoordinationPoint[namedFix | fixRadialDistance],
              newCoordinationTime[type, text], newAltitude, newRouteOfFlight[legacyFormat]

    2026-08-10: added content-aware dedup -- this was the one handler in
    this file calling _fire_tfms_watchlist_hit with NO layer beyond
    watchlist_event_hit's own default 5-minute entry+event_type dedup
    (contrast _handle_track_information just above, which layers
    _TFMS_ALERT_DEDUP's longer 30-min window on top). TFMS/FDPS rebroadcast
    the same unchanged amendment on a cadence that can exceed 5 minutes,
    so operator-observed "several amendments that look like the exact same
    copy with no changes" were real repeat fires, not a misread. Keyed on
    route_text (not just entry id, unlike track_approach's key -- ETA/
    position naturally changes every cycle so entry-id-only keying is
    fine there; an amendment's actual content does NOT change on a bare
    rebroadcast, so keying on it here means an unchanged repeat is
    suppressed indefinitely while a genuinely NEW amendment still fires
    immediately regardless of the 30-min window).
    """
    callsign, gufi, origin, destination = _qualified_aircraft_id(fltd_message)
    entry = _match_watchlist_flight(callsign)
    if entry is None:
        return
    body = _find_child(fltd_message, "flightPlanAmendmentInformation")
    amend = _find_child(body, "amendmentData") if body is not None else None
    new_route = _find_child(amend, "newRouteOfFlight") if amend is not None else None
    route_text = new_route.get("legacyFormat") if new_route is not None else None

    # 2026-08-16: same shared-slot bug as the other TFMS dedup call sites in
    # this file (see _parse_single_program's fix comment) -- "tfms_amendment"
    # as a literal key collapsed every distinct flight's amendment alerts
    # into one shared slot. Fixed by splitting the per-flight identity
    # (key) from the actual amendment content (content_key) -- same
    # semantic intent as before (unchanged route on a flight = suppressed
    # for the window; a genuinely new route for that flight, or the first
    # amendment for a DIFFERENT flight, still fires immediately), just no
    # longer cross-contaminating between flights.
    dedup_key = content_hash(f"tfms:amendment:{entry['id']}")
    content_key = content_hash(route_text or "")
    if not _TFMS_ALERT_DEDUP.should_push(dedup_key, content_key):
        return

    summary = "Flight plan amended" + (f": {route_text}" if route_text else "")
    detail = {
        "watchlist_trigger": "tfms_amendment",
        "gufi": gufi, "origin": origin, "destination": destination,
        "new_route": route_text,
    }
    _fire_tfms_watchlist_hit(entry, summary, detail, priority=3)
    _TFMS_ALERT_DEDUP.record(dedup_key, content_key)


def _handle_flight_route(fltd_message: ET.Element) -> None:
    """
    FlightRoute -- IMPLEMENTED 2026-07-20. Confirmed real structure via
    tfms_debug_unknown_msgtype/FlightRoute_0.xml (discovered mixed into a
    document batch alongside FlightModify/FlightTimes -- msgType attribute
    on the individual fltdMessage child, not on the document as a whole):
        fdm:ncsmFlightRoute > qualifiedAircraftId[...]
          > flightStatusAndSpec[flightStatus, aircraftModel]
          > altitude, speed
          > ncsmRouteData
              > etd[...], eta[...], diversionIndicator, rvsmData
              > dp[routeName, routeType]         -- departure procedure/SID
              > dpTransitionFix
              > star[routeName, routeType]        -- arrival STAR
              > starTransitionFix
              > arrivalFixAndTime[arrTime, fixName]
              > departureFixAndTime[arrTime, fixName]
              > nextPosition[latitudeDecimal, longitudeDecimal]
              > flightTraversalData2 > fix[sequenceNumber, elapsedTime]*

    This directly answers "what STAR/corridor is this arrival actually on"
    at the per-flight level -- named SID and STAR with transition fixes,
    exactly the kind of ground truth the earlier ARTCC-level TBFM corridor
    inference this session had to leave unverified. Watchlist-coupled like
    the other fltdMessage handlers: fires watchlist_event_hit tagged
    watchlist_trigger="tfms_flight_route" carrying dp/star/transition fixes
    plus the full ordered fix list (trimmed to fix names only -- elapsed
    times are in the raw event_detail JSON for anyone who wants the full
    timeline, but the summary stays readable).
    """
    callsign, gufi, origin, destination = _qualified_aircraft_id(fltd_message)
    entry = _match_watchlist_flight(callsign)
    if entry is None:
        return

    body = _find_child(fltd_message, "ncsmFlightRoute")
    route_data = _find_child(body, "ncsmRouteData") if body is not None else None
    if route_data is None:
        return

    dp_elem = _find_child(route_data, "dp")
    star_elem = _find_child(route_data, "star")
    sid_name = dp_elem.get("routeName") if dp_elem is not None else None
    sid_type = dp_elem.get("routeType") if dp_elem is not None else None
    star_name = star_elem.get("routeName") if star_elem is not None else None
    star_type = star_elem.get("routeType") if star_elem is not None else None
    dp_transition = _text(_find_child(route_data, "dpTransitionFix"))
    star_transition = _text(_find_child(route_data, "starTransitionFix"))

    traversal = _find_child(route_data, "flightTraversalData2")
    fix_sequence: list[str] = []
    if traversal is not None:
        for fix_elem in traversal:
            if _local(fix_elem.tag) == "fix":
                name = _text(fix_elem)
                if name:
                    fix_sequence.append(name)

    if not (sid_name or star_name):
        # No named procedure on this particular message -- not worth a
        # watchlist hit on its own (would just be a bare fix list).
        return

    summary_parts = []
    if sid_name:
        summary_parts.append(f"SID {sid_name}" + (f"/{dp_transition}" if dp_transition else ""))
    if star_name:
        summary_parts.append(f"STAR {star_name}" + (f"/{star_transition}" if star_transition else ""))
    summary = f"Route: {' -> '.join(summary_parts)}"

    detail = {
        "watchlist_trigger": "tfms_flight_route",
        "gufi": gufi, "origin": origin, "destination": destination,
        "sid_name": sid_name, "sid_type": sid_type, "dp_transition_fix": dp_transition,
        "star_name": star_name, "star_type": star_type, "star_transition_fix": star_transition,
        "fix_sequence": fix_sequence,
    }
    _fire_tfms_watchlist_hit(entry, summary, detail, priority=3)


def _handle_flight_plan_information(fi_message: ET.Element) -> list[dict]:
    """STUB -- flightPlanInformation. Only one real sample seen (batched
    alongside trackInformation in the same document), body content not yet
    isolated/confirmed in detail. Left as a no-op pending a clearer sample;
    _capture_unknown_msgtype_sample doesn't apply here since the msgType
    itself is already known (in _KNOWN_MSG_TYPES), just not yet field-mapped."""
    return []


def _handle_restriction(fi_message: ET.Element) -> list[dict]:
    """
    RSTR -- restriction message. IMPLEMENTED (not a stub) -- confirmed real
    schema, 2026-07-20:

      <fi:fiMessage sourceFacility="ZDC" msgType="RSTR">
        <fi:restrictionMessage>
          <fcm:eventTime>...</fcm:eventTime>
          <fcm:facility>ZDC</fcm:facility>              -- issuing ARTCC/facility
          <fcm:restrictionId>16961620</fcm:restrictionId>
          <fcm:restrictedNasElements>JDUBB/SCRAM</fcm:restrictedNasElements>
          <fcm:startTime>...</fcm:startTime>
          <fcm:stopTime>...</fcm:stopTime>
          <fcm:airports>BWI</fcm:airports>              -- impacted airport(s)
          <fcm:restrictionType>DEPARTURE</fcm:restrictionType>
          <fcm:restrictionCategory>MIT</fcm:restrictionCategory>  -- MIT/GS/GDP/etc
          <fcm:mitNumber>147457</fcm:mitNumber>
          <fcm:mitValue>15</fcm:mitValue>
          <fcm:reasonText>VOL:Compacted Demand</fcm:reasonText>
          <fcm:remarks>...</fcm:remarks>
        </fi:restrictionMessage>
      </fi:fiMessage>

    `facility` on the output dict is set to the impacted airport(s)
    (fcm:airports) when present, falling back to the issuing facility
    (fcm:facility) -- write_tfms_programs's existing geo-filter checks
    facility against is_core_airport()/DC ARTCC, and an impacted-airport
    code is what that filter actually wants to see (a ZDC-issued
    restriction can affect any airport; a BWI-impacting restriction can be
    issued by any facility -- this example happens to have both point to
    DC-area, which is what confirmed the mapping).
    """
    restriction = None
    for child in fi_message:
        if child.tag.split("}")[-1] == "restrictionMessage":
            restriction = child
            break
    if restriction is None:
        return []

    restriction_id = _fcm_text(restriction, "restrictionId")
    if not restriction_id:
        return []

    issuing_facility = _fcm_text(restriction, "facility")
    airports = _fcm_text(restriction, "airports")
    category = _fcm_text(restriction, "restrictionCategory")
    rtype = _fcm_text(restriction, "restrictionType")

    payload = {
        "program_id": restriction_id,
        "type": category or rtype or "RESTRICTION",
        "facility": (airports or issuing_facility or "").strip(),
        "start_time": _ts_to_epoch(_fcm_text(restriction, "startTime")),
        "end_time": _ts_to_epoch(_fcm_text(restriction, "stopTime")),
        "reason": _fcm_text(restriction, "reasonText"),
        "status": "ACTIVE",
        "source": "swim_tfms",
        # Extra RSTR-specific fields, preserved in raw_json for full fidelity
        # even though nas_programs' own columns don't have dedicated slots:
        "issuing_facility": issuing_facility,
        "restricted_nas_elements": _fcm_text(restriction, "restrictedNasElements"),
        "restriction_type": rtype,
        "restriction_category": category,
        "mit_number": _fcm_text(restriction, "mitNumber"),
        "mit_value": _fcm_text(restriction, "mitValue"),
        "remarks": _fcm_text(restriction, "remarks"),
    }
    return [payload]


_APTC_LAST_CONFIG: dict[str, dict] = {}
_APTC_ALERT_DEDUP = PushDedup("tfms_aptc_alerts", dedup_secs=900)


def _handle_airport_config(fi_message: ET.Element) -> list[dict]:
    """
    APTC -- airport configuration message. IMPLEMENTED 2026-07-20, real
    schema confirmed via tfms_debug_unknown_msgtype/APTC_*.xml:
      <fi:airportConfigMessage>
        <fcm:eventTime/> <fcm:entryTime/> <fcm:facility/> <fcm:airport/>
        <fcm:arrRunwayConf/> <fcm:depRunwayConf/> <fcm:arrRate/> <fcm:depRate/>
        <fcm:updateTime/> <fcm:weather/>  -- e.g. VMC/IMC/LVMC
        <fcm:stratAar/> <fcm:aarAdjust/> ...
      </fi:airportConfigMessage>

    Design decision: this is rolling airport operating status, not a
    program/restriction -- doesn't fit nas_programs' program_id/start/end
    shape and doesn't warrant a new table today (returns [] always, never
    written to nas_programs). Instead: cache the last-seen config per
    DC-area airport in-memory and fire a nas-alerts ntfy ONLY when a
    DC-area airport's arrival rate drops materially (>=20%) or its weather
    category degrades to IMC/LVMC -- the same "silence routine, escalate
    real drops" pattern requested for sector/corridor coalescing (Task
    #20), applied here at the single-airport-rate level. Non-DC airports
    are parsed but never alert (in-memory cache still updated for them,
    cheap and keeps the door open if the DC set expands later).
    """
    body = _find_child(fi_message, "airportConfigMessage")
    if body is None:
        return []

    airport = _fcm_text(body, "airport") or _fcm_text(body, "facility")
    if not airport:
        return []
    airport_upper = airport.upper()

    def _to_int(s: str | None) -> int | None:
        if not s:
            return None
        try:
            return int(float(s.strip()))
        except ValueError:
            return None

    arr_rate = _to_int(_fcm_text(body, "arrRate"))
    dep_rate = _to_int(_fcm_text(body, "depRate"))
    weather = _fcm_text(body, "weather")
    arr_conf = _fcm_text(body, "arrRunwayConf")
    dep_conf = _fcm_text(body, "depRunwayConf")

    prev = _APTC_LAST_CONFIG.get(airport_upper)
    _APTC_LAST_CONFIG[airport_upper] = {"arr_rate": arr_rate, "dep_rate": dep_rate, "weather": weather}

    if airport_upper in _DC_FACILITIES and prev is not None:
        rate_dropped = bool(
            prev.get("arr_rate") and arr_rate is not None
            and arr_rate <= prev["arr_rate"] * 0.8
        )
        weather_degraded = (
            (prev.get("weather") or "").upper() in ("VMC", "")
            and (weather or "").upper() in ("IMC", "LVMC")
        )
        if rate_dropped or weather_degraded:
            # 2026-08-16: same shared-slot bug as the other TFMS dedup call
            # sites in this file -- see _parse_single_program's fix comment.
            # Split per-airport identity (key) from the varying rate/weather
            # (content_key) so different airports' config-change alerts stop
            # cross-contaminating each other's dedup window.
            dedup_key = content_hash(f"aptc:{airport_upper}")
            content_key = content_hash(f"{arr_rate}:{weather}")
            if _APTC_ALERT_DEDUP.should_push(dedup_key, content_key):
                title = f"Airport config change — {airport_upper}"
                detail = (
                    f"{airport_upper}: arr {arr_conf or '?'} @ "
                    f"{arr_rate if arr_rate is not None else '?'}/hr, "
                    f"dep {dep_conf or '?'} @ {dep_rate if dep_rate is not None else '?'}/hr, "
                    f"weather {weather or '?'} (was arr {prev.get('arr_rate')}/hr, {prev.get('weather')})"
                )
                dispatch = f"{airport_upper} config: arr {arr_rate}/hr wx {weather}"
                try:
                    # tfms-alerts/tfms-<zone> family pattern, 2026-08-02 -- see
                    # check_tfms_alerts() docstring above
                    from shared.sector_coalesce import fire_family_alert
                    fire_family_alert(
                        "tfms", "tfms_aptc", airport_upper, title, detail, dispatch, base_priority=3,
                    )
                    _APTC_ALERT_DEDUP.record(dedup_key, content_key)
                    log.info("tfms: APTC config-change alert fired for %s", airport_upper)
                except Exception as e:
                    log.error("tfms: APTC alert fire failed for %s: %s", airport_upper, e)

    return []


_GADV_ALERT_DEDUP = PushDedup("tfms_gadv_alerts", dedup_secs=3600)


def _handle_general_advisory(fi_message: ET.Element) -> list[dict]:
    """
    GADV -- ATCSCC general advisory bulletin. IMPLEMENTED 2026-07-20, real
    schema confirmed via tfms_debug_unknown_msgtype/GADV_5.xml:
      <fi:generalAdvisory>
        <fcm:advisoryNumber>0170</fcm:advisoryNumber>
        <fcm:origin>ATCSCC</fcm:origin>
        <fcm:dateSent/>
        <fcm:facilities>ZNY</fcm:facilities>
        <fcm:effectivePeriod><fce:startTime/><fce:endTime/></fcm:effectivePeriod>
        <fcm:advisoryTitle>ATCSCC ADVZY 170 DCC/ZNY ...</fcm:advisoryTitle>
        <fcm:advisoryText>free-form bulletin text...</fcm:advisoryText>
      </fi:generalAdvisory>

    Doesn't fit nas_programs (free text, no program-id/start-end shape) --
    would need a dedicated advisories table for full history, out of scope
    for this pass. Instead: fire a nas-alerts ntfy only when `facilities`
    names a DC-relevant ARTCC/airport (reusing _DC_FACILITIES), deduped by
    advisoryNumber -- ATCSCC advisories get re-broadcast verbatim on every
    SWIM refresh cycle, so the advisory number (not a content hash) is the
    correct identity for "have we already surfaced this one."
    """
    ga = _find_child(fi_message, "generalAdvisory")
    if ga is None:
        return []

    from shared.sector_coalesce import is_tracked_facility

    facilities_raw = _fcm_text(ga, "facilities") or ""
    facilities = {f.strip().upper() for f in facilities_raw.replace(",", " ").split() if f.strip()}
    # WIDENED 2026-08-03 (see check_tfms_alerts comment above) -- was
    # `facilities & _DC_FACILITIES`, DC-only. "dc_hit" name kept for
    # minimal diff even though it's no longer DC-exclusive; represents
    # whichever of this advisory's named facilities fall in one of the 8
    # tracked zones.
    dc_hit = {f for f in facilities if is_tracked_facility(f)}
    if not dc_hit:
        return []

    # 2026-08-16: same shared-slot bug as the other TFMS dedup call sites in
    # this file -- see _parse_single_program's fix comment. This is the
    # exact one that produced the live "[ESCALATING/DC_LOCAL] ATCSCC ADVZY
    # 0075" spam: with "tfms_gadv" as a global literal key, a DIFFERENT
    # advisory number (e.g. one rotating in) overwrote the shared slot, so
    # the next rebroadcast of advisory 0075 looked "new" again. Per-advisory
    # identity as the key, constant content_key (advisory content for a
    # given number doesn't meaningfully change -- this is a one-shot "have
    # we already surfaced this advisory number" gate, same as tfms_track's
    # approach-alert fix above).
    advisory_number = _fcm_text(ga, "advisoryNumber")
    dedup_key = advisory_number or content_hash(_fcm_text(ga, "advisoryTitle") or "")
    if not _GADV_ALERT_DEDUP.should_push(dedup_key, "gadv"):
        return []

    title_text = _fcm_text(ga, "advisoryTitle") or "ATCSCC General Advisory"
    body_text = _fcm_text(ga, "advisoryText") or ""
    origin = _fcm_text(ga, "origin")

    title = f"ATCSCC ADVZY {advisory_number or '?'} — {origin or ''} {'/'.join(sorted(dc_hit))}".strip()
    # advisoryText can run long (multi-line bulletins) -- truncate for push,
    # full text still available via log for reference.
    detail = body_text[:900]
    dispatch = title_text[:200]
    # Representative facility for sector resolution -- all of _DC_FACILITIES'
    # members map to the same DC_LOCAL sector anyway, so which one is picked
    # when multiple match doesn't change the outcome.
    representative_facility = sorted(dc_hit)[0]

    try:
        # tfms-alerts/tfms-<zone> family pattern, 2026-08-02 -- see
        # check_tfms_alerts() docstring above
        from shared.sector_coalesce import fire_family_alert
        fire_family_alert(
            "tfms", "tfms_gadv", representative_facility, title, detail, dispatch, base_priority=3,
        )
        _GADV_ALERT_DEDUP.record(dedup_key, "gadv")
        log.info("tfms: GADV alert fired for advisory %s (facilities=%s)", advisory_number, dc_hit)
    except Exception as e:
        log.error("tfms: GADV alert fire failed for advisory %s: %s", advisory_number, e)

    return []


def _handle_ground_delay_program(fi_message: ET.Element) -> list[dict]:
    """
    GDP -- Ground Delay Program declaration. IMPLEMENTED 2026-07-20. This
    is the actual national GDP declaration message the platform's CPS
    "gdp" scoring factor has always needed -- it was silently falling into
    the unknown-msgType bypass capture all session because GDP was never
    added to _KNOWN_MSG_TYPES (see corrected module docstring). Confirmed
    real structure via tfms_debug_unknown_msgtype/GDP_1.xml:
      <fi:fiMessage sourceFacility="TFMS" msgType="GDP">
        <fi:gdpCompression>
          <fcm:updateTime/> <fcm:tmiStatus>ACTUAL</fcm:tmiStatus>
          <fcm:airportId>SFO</fcm:airportId> <fcm:center>ZOA</fcm:center>
          <fcm:pgmExpTime/> <fcm:elementType>APT</fcm:elementType> <fcm:adlTime/>
          <fcm:compressionPeriod><fce:startTime/><fce:endTime/></fcm:compressionPeriod>
          <fcm:cumulativeProgramPeriod><fce:startTime/><fce:endTime/></fcm:cumulativeProgramPeriod>
          <fcm:slotHoldOverrideList><fcm:carrier>EJA</fcm:carrier>...</fcm:slotHoldOverrideList>
          <fcm:impactingCondition>OTHER</fcm:impactingCondition>
          <fcm:advisoryValidPeriod><fce:startTime/><fce:endTime/></fcm:advisoryValidPeriod>
          <fcm:currentDelays><fce:totalDelay/><fce:maxDelay/><fce:avgDelay/></fcm:currentDelays>
          <fcm:anticipatedDelays><fce:totalDelay/><fce:maxDelay/><fce:avgDelay/></fcm:anticipatedDelays>
        </fi:gdpCompression>
      </fi:fiMessage>

    No native program-ID field exists on this message -- synthesized as
    "GDP-{airportId}-{cumulativeProgramPeriod start epoch}" so repeated
    ACTUAL/PLANNED updates for the same program upsert cleanly instead of
    creating duplicate rows, while a genuinely new program (different
    start time) gets its own row. Feeds nas_programs directly -- existing
    write_tfms_programs geo-filter + check_tfms_alerts DC-facility dedup
    both apply unchanged (an IAD/DCA/BWI GDP fires nas-alerts automatically,
    no extra code needed here).
    """
    gdp = _find_child(fi_message, "gdpCompression")
    if gdp is None:
        return []

    airport_id = _fcm_text(gdp, "airportId")
    if not airport_id:
        return []

    cum_period = _find_child(gdp, "cumulativeProgramPeriod")
    comp_period = _find_child(gdp, "compressionPeriod")
    valid_period = _find_child(gdp, "advisoryValidPeriod")
    # BUG FIXED 2026-08-23: this used the `_find_child(a, t) or
    # _find_child(b, t) or _find_child(c, t)` form -- see _first_present()
    # docstring. cumulativeProgramPeriod/compressionPeriod's startTime/
    # endTime are childless leaf elements, so `bool(elem)` was False even
    # when genuinely found, and the `or` chain always fell through to the
    # LAST candidate (advisoryValidPeriod) regardless of which of the
    # earlier ones was actually present -- contradicting this function's
    # own docstring, which has always claimed cumulativeProgramPeriod is
    # the key source. Every program_id written before this fix is keyed
    # on advisoryValidPeriod's start instead -- see key_scheme on
    # nas_programs (db.py SCHEMA_V36) for how existing rows are marked
    # and correlated across this change rather than silently re-keyed.
    start_elem = _first_present(_find_child(cum_period, "startTime"),
                                 _find_child(comp_period, "startTime"),
                                 _find_child(valid_period, "startTime"))
    end_elem = _first_present(_find_child(cum_period, "endTime"),
                               _find_child(comp_period, "endTime"),
                               _find_child(valid_period, "endTime"))
    start_time = _ts_to_epoch(_text(start_elem))
    end_time = _ts_to_epoch(_text(end_elem))

    program_id = f"GDP-{airport_id}-{int(start_time) if start_time else 'nostart'}"

    current_delays = _find_child(gdp, "currentDelays")
    anticipated_delays = _find_child(gdp, "anticipatedDelays")
    slot_override_elem = _find_child(gdp, "slotHoldOverrideList")
    carriers: list[str] = []
    if slot_override_elem is not None:
        for c in slot_override_elem:
            if _local(c.tag) == "carrier":
                t = _text(c)
                if t:
                    carriers.append(t)

    payload = {
        "program_id": program_id,
        "type": "GDP",
        "facility": airport_id,
        "start_time": start_time,
        "end_time": end_time,
        "reason": _fcm_text(gdp, "impactingCondition"),
        "status": "ACTIVE" if (_fcm_text(gdp, "tmiStatus") or "ACTUAL").upper() == "ACTUAL" else "PLANNED",
        "source": "swim_tfms",
        "center": _fcm_text(gdp, "center"),
        "avg_delay_minutes": _fcm_text(current_delays, "avgDelay") if current_delays is not None else None,
        "max_delay_minutes": _fcm_text(current_delays, "maxDelay") if current_delays is not None else None,
        "total_delay_minutes": _fcm_text(current_delays, "totalDelay") if current_delays is not None else None,
        "anticipated_avg_delay_minutes": (
            _fcm_text(anticipated_delays, "avgDelay") if anticipated_delays is not None else None
        ),
        "slot_hold_override_carriers": carriers,
        "program_expire_time": _fcm_text(gdp, "pgmExpTime"),
    }
    return [payload]


def _handle_ground_stop(fi_message: ET.Element) -> list[dict]:
    """
    GS -- Ground Stop declaration. IMPLEMENTED 2026-07-20, same discovery
    as GDP above. Confirmed real structure via
    tfms_debug_unknown_msgtype/GS_2.xml:
      <fi:fiMessage msgType="GS">
        <fi:gsAdvisory>
          <fcm:advisoryTypeName>CDM GROUND STOP</fcm:advisoryTypeName>
          <fcm:updateTime/> <fcm:tmiStatus>ACTUAL</fcm:tmiStatus>
          <fcm:currentDelays/> <fcm:anticipatedDelays/>
          <fcm:airportId>BOS</fcm:airportId> <fcm:center>ZBW</fcm:center>
          <fcm:pgmExpTime/> <fcm:elementType>APT</fcm:elementType> <fcm:adlTime/>
          <fcm:groundStopPeriod><fce:startTime/><fce:endTime/></fcm:groundStopPeriod>
          <fcm:cumulativeProgramPeriod>...</fcm:cumulativeProgramPeriod>
          <fcm:departureFacilitiesIncludedList><fce:facilityId>...</fce:facilityId>...</fcm:departureFacilitiesIncludedList>
          <fcm:aircraftTypesIncluded>JET AND PROP ONLY</fcm:aircraftTypesIncluded>
          <fcm:impactingCondition>WIND</fcm:impactingCondition>
          <fcm:advisoryValidPeriod>...</fcm:advisoryValidPeriod>
        </fi:gsAdvisory>
      </fi:fiMessage>

    Ground stops are the most acute NAS event type (zero-notice full
    departure halt) -- program_id synthesized the same way as GDP:
    "GS-{airportId}-{groundStopPeriod start epoch}". departureFacilities
    IncludedList is preserved in raw_json (a DC-area ground stop's included-
    facilities list tells us whether ZDC-originating traffic is actually
    swept in, not just the declaring airport).
    """
    gs = _find_child(fi_message, "gsAdvisory")
    if gs is None:
        return []

    airport_id = _fcm_text(gs, "airportId")
    if not airport_id:
        return []

    gs_period = _find_child(gs, "groundStopPeriod")
    valid_period = _find_child(gs, "advisoryValidPeriod")
    # BUG FIXED 2026-08-23: same class as the GDP fix above -- groundStopPeriod's
    # startTime/endTime are childless leaf elements, so the `or` chain
    # always fell through to advisoryValidPeriod regardless of whether
    # groundStopPeriod's own time was present, contradicting this
    # function's docstring claim that groundStopPeriod is the key source.
    start_elem = _first_present(_find_child(gs_period, "startTime"),
                                 _find_child(valid_period, "startTime"))
    end_elem = _first_present(_find_child(gs_period, "endTime"),
                               _find_child(valid_period, "endTime"))
    start_time = _ts_to_epoch(_text(start_elem))
    end_time = _ts_to_epoch(_text(end_elem))

    program_id = f"GS-{airport_id}-{int(start_time) if start_time else 'nostart'}"

    current_delays = _find_child(gs, "currentDelays")
    anticipated_delays = _find_child(gs, "anticipatedDelays")
    facilities_elem = _find_child(gs, "departureFacilitiesIncludedList")
    included_facilities: list[str] = []
    if facilities_elem is not None:
        for c in facilities_elem:
            if _local(c.tag) == "facilityId":
                t = _text(c)
                if t:
                    included_facilities.append(t)

    payload = {
        "program_id": program_id,
        "type": "GS",
        "facility": airport_id,
        "start_time": start_time,
        "end_time": end_time,
        "reason": _fcm_text(gs, "impactingCondition"),
        "status": "ACTIVE" if (_fcm_text(gs, "tmiStatus") or "ACTUAL").upper() == "ACTUAL" else "PLANNED",
        "source": "swim_tfms",
        "center": _fcm_text(gs, "center"),
        "advisory_type": _fcm_text(gs, "advisoryTypeName"),
        "avg_delay_minutes": _fcm_text(current_delays, "avgDelay") if current_delays is not None else None,
        "max_delay_minutes": _fcm_text(current_delays, "maxDelay") if current_delays is not None else None,
        "total_delay_minutes": _fcm_text(current_delays, "totalDelay") if current_delays is not None else None,
        "anticipated_avg_delay_minutes": (
            _fcm_text(anticipated_delays, "avgDelay") if anticipated_delays is not None else None
        ),
        "included_facilities": included_facilities,
        "aircraft_types_included": _fcm_text(gs, "aircraftTypesIncluded"),
        "program_expire_time": _fcm_text(gs, "pgmExpTime"),
    }
    return [payload]


def _handle_airspace_flow_program(fi_message: ET.Element) -> list[dict]:
    """
    AFP -- Airspace Flow Program declaration/cancellation. IMPLEMENTED
    2026-08-22. AFP is one of this module's documented target types (see
    module docstring) but -- exactly like GDP/GS before it -- every real
    AFP message was falling into the unknown-msgType bypass capture
    because "AFP" was never added to _KNOWN_MSG_TYPES (15 real samples in
    tfms_debug_unknown_msgtype/ at implementation time). Unlike GDP/GS,
    one msgType covers TWO sub-element shapes: a declaration
    (fi:afpAdvisory, tmiStatus PROPOSED/ACTUAL) and a cancellation
    (fi:afpCancel, tmiStatus PURGED). Confirmed real structures:

    tfms_debug_unknown_msgtype/AFP_0.xml (declaration):
      <fi:fiMessage msgType="AFP">
        <fi:afpAdvisory>
          <fcm:updateTime/> <fcm:tmiStatus>PROPOSED</fcm:tmiStatus>
          <fcm:afpName>FCAJX5</fcm:afpName> <fcm:pgmExpTime/>
          <fcm:elementType>FCA</fcm:elementType>
          <fcm:altitudesIncluded><fce:lowerAltitude>FL180</fce:lowerAltitude>
            <fce:upperAltitude>FL600</fce:upperAltitude></fcm:altitudesIncluded>
          <fcm:adlTime/> <fcm:delayAssignmentMode>UDP</fcm:delayAssignmentMode>
          <fcm:overrideAfp>NO</fcm:overrideAfp>
          <fcm:entryEstimatedFor><fce:startTime/><fce:endTime/></fcm:entryEstimatedFor>
          <fcm:cumulativeProgramPeriod><fce:startTime/><fce:endTime/></fcm:cumulativeProgramPeriod>
          <fcm:programRateList><fce:programRate>100</fce:programRate>...</fcm:programRateList>
          <fcm:departureScope><fcm:keyword>MANUAL</fcm:keyword>
            <fcm:center>ZLA</fcm:center>...</fcm:departureScope>
          <fcm:departureFacilitiesIncludedList><fce:facilityId>ZLA</fce:facilityId>...
          </fcm:departureFacilitiesIncludedList>
          <fcm:maxDelay>375</fcm:maxDelay> <fcm:avgDelay>89.3</fcm:avgDelay>
          <fcm:impactingCondition>THUNDERSTORMS</fcm:impactingCondition>
          <fcm:advisoryValidPeriod><fce:startTime/><fce:endTime/></fcm:advisoryValidPeriod>
        </fi:afpAdvisory>
      </fi:fiMessage>

    tfms_debug_unknown_msgtype/AFP_13.xml (cancellation):
      <fi:fiMessage msgType="AFP">
        <fi:afpCancel>
          <fcm:updateTime/> <fcm:tmiStatus>PURGED</fcm:tmiStatus>
          <fcm:afpName>FCABW3</fcm:afpName> <fcm:pgmExpTime/>
          <fcm:afpCtlElement>FCABW3</fcm:afpCtlElement>
          <fcm:elementType>FCA</fcm:elementType> <fcm:adlTime/>
          <fcm:advisoryValidPeriod><fce:startTime/><fce:endTime/></fcm:advisoryValidPeriod>
        </fi:afpCancel>
      </fi:fiMessage>

    An AFP constrains an FCA (elementType=FCA -- airspace, not an
    airport), so there is no airportId; the afpName IS the facility
    identifier. program_id synthesized like GDP/GS:
    "AFP-{afpName}-{program start epoch}", start taken
    cumulativeProgramPeriod -> entryEstimatedFor -> advisoryValidPeriod.
    cumulativeProgramPeriod start is the stable per-program key in the
    real captures (all 6 FCAJX5 revisions share cumStart while pgmExpTime
    and advisoryValidPeriod both drift across revisions), so repeated
    PROPOSED/ACTUAL updates upsert cleanly and a genuinely new program
    gets its own row. Known limitation, on purpose: afpCancel carries
    ONLY advisoryValidPeriod, so a cancel whose valid-period start
    differs from its advisory's cumulative-period start lands in its own
    row (status=CANCELLED in raw_json) rather than flipping the original
    row -- consumers filter on payload status, same as they already must
    for GDP/GS PLANNED vs ACTIVE. Status mapping extends GDP/GS's
    ACTUAL->ACTIVE / else->PLANNED convention with afpCancel/PURGED ->
    CANCELLED rather than inventing a new mechanism.
    """
    is_cancel = False
    afp = _find_child(fi_message, "afpAdvisory")
    if afp is None:
        afp = _find_child(fi_message, "afpCancel")
        is_cancel = True
    if afp is None:
        return []

    afp_name = _fcm_text(afp, "afpName")
    if not afp_name:
        return []

    cum_period = _find_child(afp, "cumulativeProgramPeriod")
    entry_period = _find_child(afp, "entryEstimatedFor")
    valid_period = _find_child(afp, "advisoryValidPeriod")
    # Deliberately NOT the `_find_child(a, t) or _find_child(b, t)` chain
    # GDP/GS use: ElementTree elements are FALSY when they have no children,
    # and <fce:startTime> is a text-only leaf, so that chain always falls
    # through to the LAST period regardless of which exist -- caught live
    # while verifying program_id stability across the 6 real FCAJX5
    # revisions (the chain keyed on advisoryValidPeriod, which drifts
    # between revisions; cumulativeProgramPeriod is the stable one).
    start_elem = end_elem = None
    for period in (cum_period, entry_period, valid_period):
        if start_elem is None:
            start_elem = _find_child(period, "startTime")
        if end_elem is None:
            end_elem = _find_child(period, "endTime")
    start_time = _ts_to_epoch(_text(start_elem))
    end_time = _ts_to_epoch(_text(end_elem))

    program_id = f"AFP-{afp_name}-{int(start_time) if start_time else 'nostart'}"

    tmi_status = (_fcm_text(afp, "tmiStatus") or "ACTUAL").upper()
    if is_cancel or tmi_status == "PURGED":
        status = "CANCELLED"
    elif tmi_status == "ACTUAL":
        status = "ACTIVE"
    else:
        status = "PLANNED"

    program_rates: list[int] = []
    rate_list_elem = _find_child(afp, "programRateList")
    if rate_list_elem is not None:
        for c in rate_list_elem:
            if _local(c.tag) == "programRate":
                t = _text(c)
                if t is not None:
                    try:
                        program_rates.append(int(t))
                    except ValueError:
                        pass

    departure_scope_centers: list[str] = []
    scope_elem = _find_child(afp, "departureScope")
    if scope_elem is not None:
        for c in scope_elem:
            if _local(c.tag) == "center":
                t = _text(c)
                if t:
                    departure_scope_centers.append(t)

    included_facilities: list[str] = []
    facilities_elem = _find_child(afp, "departureFacilitiesIncludedList")
    if facilities_elem is not None:
        for c in facilities_elem:
            if _local(c.tag) == "facilityId":
                t = _text(c)
                if t:
                    included_facilities.append(t)

    altitudes_elem = _find_child(afp, "altitudesIncluded")

    payload = {
        "program_id": program_id,
        "type": "AFP",
        "facility": afp_name,
        "start_time": start_time,
        "end_time": end_time,
        "reason": _fcm_text(afp, "impactingCondition"),
        "status": status,
        "source": "swim_tfms",
        "element_type": _fcm_text(afp, "elementType"),
        "control_element": _fcm_text(afp, "afpCtlElement"),
        "lower_altitude": _fcm_text(altitudes_elem, "lowerAltitude") if altitudes_elem is not None else None,
        "upper_altitude": _fcm_text(altitudes_elem, "upperAltitude") if altitudes_elem is not None else None,
        "delay_assignment_mode": _fcm_text(afp, "delayAssignmentMode"),
        "override_afp": _fcm_text(afp, "overrideAfp"),
        # maxDelay/avgDelay are DIRECT children on afpAdvisory (unlike
        # GDP/GS's nested currentDelays block) -- same payload key names
        # kept so downstream consumers see one convention.
        "avg_delay_minutes": _fcm_text(afp, "avgDelay"),
        "max_delay_minutes": _fcm_text(afp, "maxDelay"),
        "program_rates": program_rates,
        "departure_scope_keyword": _fcm_text(scope_elem, "keyword") if scope_elem is not None else None,
        "departure_scope_centers": departure_scope_centers,
        "included_facilities": included_facilities,
        "program_expire_time": _fcm_text(afp, "pgmExpTime"),
    }
    return [payload]


# In-memory FCA/FEA polygon-definition registry -- fcaId -> definition dict.
# Not persisted (process-lifetime cache only); intended groundwork for
# Task #20 (sector/corridor-based alert coalescing), which needs to resolve
# a TMI_FLIGHT_LIST hit's fcaId into a human/geographic description. Not
# alerted on its own -- a definition update isn't itself a dispatch event.
_FXA_REGISTRY: dict[str, dict] = {}
_FXA_REGISTRY_MAX = 500  # bound memory growth; oldest entries dropped via FIFO-ish eviction


def _handle_fxa(fi_message: ET.Element) -> list[dict]:
    """
    FXA -- Flow-Constrained-Area / FEA polygon definition. IMPLEMENTED
    2026-07-20, real schema confirmed via
    tfms_debug_unknown_msgtype/FXA_0.xml (most abundant unknown fiMessage
    type seen, 15/60 in one capture batch):
      <fi:fiMessage msgType="FXA" sourceFacility="DCC">
        <fi:feaFca>
          <fcm:fcaId>fca.dccops2.lxstn55.20260625120947</fcm:fcaId>
          <fcm:fcaName>Z_MA34</fcm:fcaName>
          <fcm:fcaDomain>PUBLIC</fcm:fcaDomain>
          <fcm:tmiStatus>UPDATED</fcm:tmiStatus>
          <fcm:lastUpdate/> <fcm:fcaReason>NONE</fcm:fcaReason>
          <fcm:feaFcaType>FEA</fcm:feaFcaType>
          <fcm:startTime/> <fcm:endTime/>
          <fcm:line><fcm:ceiling/><fcm:floor/>
            <fcm:points><fcm:point><fce:latitude/><fce:longitude/></fcm:point>...</fcm:points>
          </fcm:line>
        </fi:feaFca>
      </fi:fiMessage>

    This is what TMI_FLIGHT_LIST's fcaId field actually names -- caches
    fcaId -> {name, type, reason, start/end, ceiling/floor, source_facility}
    in-memory so future corridor/sector work can resolve an ID to a name
    without re-parsing raw FXA messages. Returns [] always (not
    nas_programs data -- this is reference/definition data, not a program).
    """
    fea = _find_child(fi_message, "feaFca")
    if fea is None:
        return []
    fca_id = _fcm_text(fea, "fcaId")
    if not fca_id:
        return []

    if len(_FXA_REGISTRY) >= _FXA_REGISTRY_MAX and fca_id not in _FXA_REGISTRY:
        # Cheap eviction -- drop an arbitrary existing entry rather than
        # tracking insertion order; this is a best-effort cache, not a
        # source of truth (FXA messages re-broadcast on every update anyway).
        _FXA_REGISTRY.pop(next(iter(_FXA_REGISTRY)), None)

    _FXA_REGISTRY[fca_id] = {
        "fca_name": _fcm_text(fea, "fcaName"),
        "fca_type": _fcm_text(fea, "feaFcaType"),
        "fca_reason": _fcm_text(fea, "fcaReason"),
        "start_time": _fcm_text(fea, "startTime"),
        "end_time": _fcm_text(fea, "endTime"),
        "source_facility": fi_message.get("sourceFacility"),
        "last_update": _fcm_text(fea, "lastUpdate"),
    }
    return []


def _handle_tmi_update(fi_message: ET.Element) -> list[dict]:
    """
    TMI_UPDATE -- per-flight reroute-amendment APPLY status. IMPLEMENTED
    2026-07-20, real schema confirmed via
    tfms_debug_unknown_msgtype/TMI_UPDATE_0.xml:
      <fi:fiMessage msgType="TMI_UPDATE" sourceFacility="TFMS">
        <fi:eramAmendmentStatusUpdate>
          <fm2:rrAmendment>
            <fm2:flightIdfr/> <fm2:acid>DAL2775</fm2:acid> <fm2:gufi/>
            <fm2:amendmentRequestType>UPDATE</fm2:amendmentRequestType>
            <fm2:flightAmendmentStatus>
              <fm2:tmiId>RRDCC514</fm2:tmiId>
              <fm2:routeAmendment>KMCO.JEEMY4.PAINN..CAMJO...KJFK</fm2:routeAmendment>
              <fm2:amendmentProtectedSegment>...</fm2:amendmentProtectedSegment>
              <fm2:amendmentStatus>APLD</fm2:amendmentStatus>
              <fm2:amendmentTime/> <fm2:eramStatus>SUCCESS</fm2:eramStatus>
              <fm2:amendmentGeneratedType>MANUAL</fm2:amendmentGeneratedType>
            </fm2:flightAmendmentStatus>
          </fm2:rrAmendment>
        </fi:eramAmendmentStatusUpdate>
      </fi:fiMessage>

    Same watchlist-coupling convention as TMI_FLIGHT_LIST: match acid
    against the flight watchlist, fire watchlist_event_hit tagged
    watchlist_trigger="tfms_tmi_update" carrying tmiId/amendmentStatus/
    routeAmendment/eramStatus -- joinable in watchlist_history against the
    original tfms_tmi hit (same tmiId lineage) to show whether a flight's
    reroute was actually applied (APLD) or rejected/pending.
    """
    update = _find_child(fi_message, "eramAmendmentStatusUpdate")
    rr = _find_child(update, "rrAmendment") if update is not None else None
    if rr is None:
        return []

    callsign = _fcm_text(rr, "acid")
    entry = _match_watchlist_flight(callsign)
    if entry is None:
        return []

    gufi = _fcm_text(rr, "gufi")
    status_block = _find_child(rr, "flightAmendmentStatus")
    tmi_id = _fcm_text(status_block, "tmiId") if status_block is not None else None
    amendment_status = _fcm_text(status_block, "amendmentStatus") if status_block is not None else None
    route_amendment = _fcm_text(status_block, "routeAmendment") if status_block is not None else None
    eram_status = _fcm_text(status_block, "eramStatus") if status_block is not None else None

    summary = f"TMI reroute {amendment_status or '?'}: {tmi_id or '?'}"
    detail = {
        "watchlist_trigger": "tfms_tmi_update",
        "gufi": gufi,
        "tmi_id": tmi_id,
        "amendment_status": amendment_status,
        "route_amendment": route_amendment,
        "eram_status": eram_status,
    }
    _fire_tfms_watchlist_hit(entry, summary, detail, priority=3)
    return []


# fiOutput/fiMessage family dispatch table -- confirmed one-per-document
# so far, returns nas_programs-shaped rows (or [] for TMI_FLIGHT_LIST/GDP-
# adjacent handlers that do their own watchlist/cache/alert side effect
# instead -- see each handler's docstring for which).
_MSG_TYPE_HANDLERS = {
    "TMI_FLIGHT_LIST": _handle_tmi_flight_list,
    "RSTR": _handle_restriction,
    "APTC": _handle_airport_config,
    "GADV": _handle_general_advisory,
    "GDP": _handle_ground_delay_program,
    "GS": _handle_ground_stop,
    "AFP": _handle_airspace_flow_program,
    "FXA": _handle_fxa,
    "TMI_UPDATE": _handle_tmi_update,
}

def _handle_flight_plan_cancellation(fltd_message: ET.Element) -> None:
    """
    flightPlanCancellation -- IMPLEMENTED 2026-07-21. Confirmed real
    structure via tfms_debug_unknown_msgtype/flightPlanCancellation_*.xml
    (10 real samples): fdm:flightPlanCancellation > qualifiedAircraftId
    [aircraftId, departurePoint, arrivalPoint]. fdTrigger=
    "FD_FLIGHT_CANCEL_MSG" on the fltdMessage itself confirms this is a
    genuine cancellation, not just a document label matching the msgType
    string. A cancelled flight plan for a watchlist-tracked flight is
    high-priority: the pickup this entry was tracking may no longer be
    happening as planned, distinct from a routine delay/reroute.
    """
    callsign, gufi, origin, destination = _qualified_aircraft_id(fltd_message)
    entry = _match_watchlist_flight(callsign)
    if entry is None:
        return

    dep_arpt = fltd_message.get("depArpt") or origin
    arr_arpt = fltd_message.get("arrArpt") or destination
    summary = f"Flight plan cancelled: {callsign or entry['identifier']} ({dep_arpt or '?'} -> {arr_arpt or '?'})"
    detail = {
        "watchlist_trigger": "tfms_flight_plan_cancellation",
        "gufi": gufi, "origin": dep_arpt, "destination": arr_arpt,
        "fd_trigger": fltd_message.get("fdTrigger"),
        "source_facility": fltd_message.get("sourceFacility"),
    }
    _fire_tfms_watchlist_hit(entry, summary, detail, priority=4)


def _handle_flight_create(fltd_message: ET.Element) -> None:
    """
    FlightCreate -- IMPLEMENTED 2026-07-21. Confirmed real structure via
    tfms_debug_unknown_msgtype/FlightCreate_*.xml:
        fdm:ncsmFlightCreate > qualifiedAircraftId[...]
          > airlineData > flightStatusAndSpec[flightStatus, aircraftModel]
                        > eta[timeValue], etd[timeValue]
                        > flightTimeData[airlineInTime, airlineOutTime]
    This is the earliest possible signal a flight plan exists in the NAS --
    fires before TMI_FLIGHT_LIST/trackInformation would ever see the same
    flight. Useful as a first-sighting confirmation for a watchlist entry
    ahead of the flight actually being tracked elsewhere. Informational,
    not urgent -- lower priority than the other watchlist hits.
    """
    callsign, gufi, origin, destination = _qualified_aircraft_id(fltd_message)
    entry = _match_watchlist_flight(callsign)
    if entry is None:
        return

    body = _find_child(fltd_message, "ncsmFlightCreate")
    airline_data = _find_child(body, "airlineData") if body is not None else None
    status_spec = _find_child(airline_data, "flightStatusAndSpec") if airline_data is not None else None
    flight_status = _text(_find_child(status_spec, "flightStatus")) if status_spec is not None else None
    aircraft_model = _text(_find_child(status_spec, "aircraftModel")) if status_spec is not None else None
    eta_elem = _find_child(airline_data, "eta") if airline_data is not None else None
    etd_elem = _find_child(airline_data, "etd") if airline_data is not None else None
    eta = eta_elem.get("timeValue") if eta_elem is not None else None
    etd = etd_elem.get("timeValue") if etd_elem is not None else None

    summary = f"Flight plan filed: {callsign or entry['identifier']} ({origin or '?'} -> {destination or '?'})"
    if etd:
        summary += f", ETD {etd}"
    detail = {
        "watchlist_trigger": "tfms_flight_create",
        "gufi": gufi, "origin": origin, "destination": destination,
        "flight_status": flight_status, "aircraft_model": aircraft_model,
        "etd": etd, "eta": eta,
    }
    _fire_tfms_watchlist_hit(entry, summary, detail, priority=2)


def _handle_flight_schedule_activate(fltd_message: ET.Element) -> None:
    """
    FlightScheduleActivate -- IMPLEMENTED 2026-07-21. Confirmed real
    structure via tfms_debug_unknown_msgtype/FlightScheduleActivate_*.xml
    (batches of up to 14 fltdMessage children seen in one document):
        fdm:ncsmFlightScheduleActivate > qualifiedAircraftId[...]
          > flightStatusAndSpec[flightStatus, aircraftModel]
          > ncsmRouteData > etd[timeValue], eta[timeValue], dp[routeName],
                            star[routeName], flightTraversalData2
    fdTrigger="ACTIVATE_SCHEDULED_FLIGHTS" -- fires roughly 24h ahead of a
    scheduled flight's departure, when it moves from a recurring-schedule
    template into an actual trackable flight instance. Same route-data
    shape as FlightRoute (_handle_flight_route above), just wrapped under
    a different msgType at schedule-activation time rather than in-flight.
    Useful for CTDI's recurring watchlist entries (daily shuttle, standing
    morning departure): earliest confirmation that tomorrow's instance of
    a recurring flight now has a real ETD, informational priority.
    """
    callsign, gufi, origin, destination = _qualified_aircraft_id(fltd_message)
    entry = _match_watchlist_flight(callsign)
    if entry is None:
        return

    body = _find_child(fltd_message, "ncsmFlightScheduleActivate")
    route_data = _find_child(body, "ncsmRouteData") if body is not None else None

    etd = eta = None
    sid_name = star_name = None
    if route_data is not None:
        etd_elem = _find_child(route_data, "etd")
        eta_elem = _find_child(route_data, "eta")
        etd = etd_elem.get("timeValue") if etd_elem is not None else None
        eta = eta_elem.get("timeValue") if eta_elem is not None else None
        dp_elem = _find_child(route_data, "dp")
        star_elem = _find_child(route_data, "star")
        sid_name = dp_elem.get("routeName") if dp_elem is not None else None
        star_name = star_elem.get("routeName") if star_elem is not None else None

    summary = f"Schedule activated: {callsign or entry['identifier']} ({origin or '?'} -> {destination or '?'})"
    if etd:
        summary += f", ETD {etd}"
    detail = {
        "watchlist_trigger": "tfms_flight_schedule_activate",
        "gufi": gufi, "origin": origin, "destination": destination,
        "etd": etd, "eta": eta, "sid_name": sid_name, "star_name": star_name,
    }
    _fire_tfms_watchlist_hit(entry, summary, detail, priority=2)


def _handle_oceanic_report(fltd_message: ET.Element) -> None:
    """
    oceanicReport -- RE-ENABLED 2026-07-21 with a facility filter, after
    being fully quieted earlier the same day. Confirmed real structure via
    tfms_debug_unknown_msgtype/oceanicReport_*.xml (15 samples): the bulk
    (247/~470 sample lines) were sourceFacility="KOOA"/"KZAP" (Oakland /
    Anchorage Oceanic) -- Pacific transoceanic traffic genuinely irrelevant
    to a DC-area ground-transport desk, correctly quieted.

    But two samples (oceanicReport_13/14.xml) show sourceFacility="KONY"
    (New York Oceanic) carrying KMIA->LFPG (AFR091) and KMIA->EGLL (VIR118)
    -- international departures out of Miami climbing the East Coast and
    handed to NY Oceanic for the North Atlantic gateway. That is exactly
    the traffic the operator flagged as relevant: transatlantic flights
    routed through the New York oceanic corridor, including ones that
    originate in Miami. Filtering on sourceFacility=="KONY" keeps that
    slice and continues to silently drop KOOA/KZAP Pacific traffic --
    no debug-capture, no unknown-type log line either way, since this
    handler is now registered instead of falling through to the
    unhandled-type stub.
    """
    if fltd_message.get("sourceFacility") != "KONY":
        return

    callsign, gufi, origin, destination = _qualified_aircraft_id(fltd_message)
    entry = _match_watchlist_flight(callsign)
    if entry is None:
        return

    dep_arpt = fltd_message.get("depArpt") or origin
    arr_arpt = fltd_message.get("arrArpt") or destination

    body = _find_child(fltd_message, "oceanicReport")
    reported = _find_child(body, "reportedPositionData") if body is not None else None
    pos = _find_child(reported, "position") if reported is not None else None
    lat = _dms_to_decimal(_find_child(pos, "latitudeDMS")) if pos is not None else None
    lon = _dms_to_decimal(_find_child(pos, "longitudeDMS")) if pos is not None else None
    altitude = _text(_find_child(reported, "altitude")) if reported is not None else None

    track_data = _find_child(body, "ncsmTrackData") if body is not None else None
    eta_elem = _find_child(track_data, "eta") if track_data is not None else None
    eta = eta_elem.get("timeValue") if eta_elem is not None else None

    summary = f"Oceanic position report: {callsign or entry['identifier']} ({dep_arpt or '?'} -> {arr_arpt or '?'}) via NY Oceanic"
    if lat is not None and lon is not None:
        summary += f", {lat:.2f},{lon:.2f}"
    if eta:
        summary += f", ETA {eta}"
    detail = {
        "watchlist_trigger": "tfms_oceanic_report",
        "gufi": gufi, "origin": dep_arpt, "destination": arr_arpt,
        "lat": lat, "lon": lon, "altitude_fl": altitude, "eta": eta,
        "source_facility": fltd_message.get("sourceFacility"),
    }
    _fire_tfms_watchlist_hit(entry, summary, detail, priority=3)


# fltdOutput/fltdMessage family dispatch table -- confirmed to batch 2-5
# messages per document. All handlers do their own watchlist side-effect
# and return None (never nas_programs data).
_FLTD_MSG_TYPE_HANDLERS = {
    "FlightModify": _handle_flight_times,
    "FlightTimes": _handle_flight_times,
    "trackInformation": _handle_track_information,
    "departureInformation": _handle_departure_information,
    "arrivalInformation": _handle_arrival_information,
    "flightPlanAmendmentInformation": _handle_flight_plan_amendment,
    "FlightRoute": _handle_flight_route,
    "flightPlanCancellation": _handle_flight_plan_cancellation,
    "FlightCreate": _handle_flight_create,
    "FlightScheduleActivate": _handle_flight_schedule_activate,
    "oceanicReport": _handle_oceanic_report,
}

# Confirmed-live under the fltdMessage family but not yet field-mapped, or
# deliberately out of scope:
#   flightPlanInformation -- only one ambiguous sample seen (batched
#     alongside a trackInformation message), body not yet isolated.
#   FlightSectors -- ncsmFlightSectors > flightTraversalData2 fix-sequence
#     data is a strict subset of what FlightRoute already carries (which
#     additionally has the named SID/STAR) -- redundant, skipped.
#   boundaryCrossingUpdate -- position/speed/altitude at an ARTCC boundary
#     crossing, same underlying fields as trackInformation (already
#     handled) just keyed to a boundary event rather than a periodic ping.
#     Low incremental value over the existing approach-proximity alert.
#   oceanicReport -- MOVED to _FLTD_MSG_TYPE_HANDLERS 2026-07-21, see
#     _handle_oceanic_report docstring: KOOA/KZAP (Pacific) traffic is
#     still silently dropped, but KONY (New York Oceanic) traffic -- the
#     NAT gateway corridor, including Miami-departing transatlantic
#     flights -- is now watchlist-matched and alerted like any other
#     fltdMessage type.
#   RAPT -- Route Availability Planning Tool convective-blockage timelines,
#     NYC-metroplex product per the module docstring; DC has no RAPT
#     product observed to date. Was documented as "left unhandled" in the
#     original module docstring but never actually added to this set --
#     fixed 2026-07-21 (was still spamming unknown-type capture/log lines).
_KNOWN_UNHANDLED_FLTD_TYPES = frozenset({
    "flightPlanInformation",
    "FlightSectors",
    "boundaryCrossingUpdate",
    "RAPT",
})


def _parse_tfms_message_legacy_guess(xml_bytes: bytes, root: ET.Element) -> list[dict]:
    """
    LEGACY -- the original hand-guessed parser, never confirmed against
    real data (see module docstring). Kept only as a fallback for the rare
    case a message doesn't match the real fiMessage shape, in case some
    other TFMS product variant genuinely uses this tag set.
    """
    raw_xml = xml_bytes.decode("utf-8", errors="replace")
    programs: list[dict] = []

    # TFMS wraps programs in a container; scan for any known program element
    _PROGRAM_TAGS = {
        "gdpElement", "gsElement", "afpElement", "aarElement", "fcaElement",
        "GDP", "GS", "AFP", "AAR", "FCA",
        "nasProgram", "trafficProgram", "flowProgram",
    }

    for elem in root.iter():
        local = elem.tag.split("}")[-1]
        if local in _PROGRAM_TAGS:
            p = _parse_single_program(elem, raw_xml)
            if p:
                programs.append(p)

    if not programs:
        # Treat the root itself as a single program if it has an ID field
        p = _parse_single_program(root, raw_xml)
        if p:
            programs.append(p)

    if not programs:
        log.debug("tfms: no programs parsed from message (tag=%s)", root.tag)
    else:
        check_tfms_alerts(programs)

    return programs


def write_tfms_programs(programs: list[dict]) -> int:
    """Upsert parsed TFMS programs into nas_programs. Returns count written.

    Geo filter: only store programs affecting CORE_AIRPORTS or DC
    ARTCC/TRACON facilities (ZDC, PCT).  National FCA/AFP programs that
    reference no specific facility still pass (facility is empty/None) --
    and AFP rows pass unconditionally (2026-08-22): their facility is the
    FCA/afpName identifier (e.g. "FCAJX5"), an airspace element, not an
    airport code, so the airport-based filter can never match it; AFPs
    are national-scope by nature (real samples' departure scope spans
    ~20 ARTCCs including ZDC).

    GDP/GS key_scheme (2026-08-23, see db.py SCHEMA_V36): every GDP/GS
    row written here now is stamped key_scheme=2 (the fixed
    cumulativeProgramPeriod/groundStopPeriod-keyed scheme -- see
    _first_present() and _handle_ground_delay_program/_handle_ground_stop
    above) and, best-effort, correlated against the most recent
    key_scheme=1 ("legacy", pre-fix) row for the same type+facility via
    db.find_legacy_nas_program() -- so a program that straddles the
    parser-fix deploy is still recognizable as the same real-world
    program across the key change instead of silently fragmenting into
    an orphaned old row plus an unrelated-looking new one.
    """
    # ARTCC/TRACON identifiers that are not airport codes but are always relevant.
    _DC_ARTCC = frozenset({"ZDC", "PCT"})

    written = 0
    for p in programs:
        facility = (p.get("facility") or "").upper()
        # Pass if: no facility specified (national scope), an AFP (facility
        # is an FCA identifier, not an airport -- see docstring), DC
        # ARTCC/TRACON, or airport is in the 30-airport core set.
        if (facility and p.get("type") != "AFP"
                and facility not in _DC_ARTCC and not is_core_airport(facility)):
            log.debug("tfms: geo-filtered program %s (facility=%s not in core)",
                      p.get("program_id"), facility)
            continue
        try:
            key_scheme = None
            legacy_correlate_id = None
            if p.get("type") in ("GDP", "GS"):
                key_scheme = 2
                try:
                    legacy_correlate_id = db.find_legacy_nas_program(p["type"], facility)
                except Exception as e:
                    log.debug("tfms: legacy correlation lookup failed for %s: %s",
                              p.get("program_id"), e)
            db.upsert_nas_program(
                program_id=p["program_id"],
                prog_type=p["type"],
                facility=p.get("facility") or "",
                raw_json=json.dumps(p),
                key_scheme=key_scheme,
                legacy_correlate_id=legacy_correlate_id,
            )
            written += 1
        except Exception as e:
            log.error("tfms: db write error for %s: %s", p.get("program_id"), e)
    return written
