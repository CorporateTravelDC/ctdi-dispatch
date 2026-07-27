"""
ingest.parsers.smes_parser -- STDDS surface and terminal track parser.

SMES (Surface Movement Events): ASDE-X surface positions at DCA/IAD/BWI.
TAIS (Terminal Automation Information Service): PCT TRACON radar tracks.
"""
from __future__ import annotations

import logging
import os
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from common import db

log = logging.getLogger("ingest.parsers.smes")

# One-shot full-message debug capture -- 2026-07-20, same technique that
# confirmed tbfm_parser.py's real schema (see that file's docstring for the
# result of doing this for TBFM). terminal_tracks is now confirmed-good
# (TAIS path fixed and live, 327+ rows); surface_tracks is still 0 rows --
# all 5 samples caught under both "smes" and "tais" labels this session were
# byte-identical, meaning every message seen on this queue so far has been
# TAIS-shaped, not SMES-shaped (both parse functions are called unconditionally
# on every stdds payload -- see swim_client._handle_stdds_message -- so the
# capture isn't selective, it just records what came in). TAIS cap dropped to
# a small ongoing spot-check; SMES cap raised substantially to maximize the
# chance of catching a genuinely different (real ASDE-X) message if one ever
# arrives on this subscription. If SMES stays empty after this much larger
# sample, the working conclusion becomes "this STDDS entitlement doesn't
# carry ASDE-X SMES data" rather than "we haven't looked hard enough."
_DEBUG_SAMPLE_DIR = "/var/lib/corporatetraveldc/smes_debug"
_DEBUG_SAMPLE_MAX_TAIS = 3
_DEBUG_SAMPLE_MAX_SMES = 40
_smes_debug_count = 0
_tais_debug_count = 0

# Priority facility capture -- 2026-07-20. Generic capture above is
# unfiltered/random (confirmed: 45 samples across two restarts hit small
# out-of-area TRACONs like TRI/GPT/PWM/CID, nothing DC-area), so a message
# actually sourced from IAD/DCA/BWI could be comparatively rare in a random
# sample even if it exists. IAD in particular has had known ASDE-X
# outage/repair periods recently, which cuts both ways -- may mean fewer
# live ASDE-X messages from IAD right now, or may mean an outage/fault
# status message is what's on the wire instead of track data. Either way,
# worth a dedicated, uncapped-relative-to-the-generic-budget catch: any
# message whose <src> matches one of our actual airports of interest gets
# captured regardless of the generic per-kind cap above, into its own
# directory so it can't be crowded out by non-DC-area traffic.
_PRIORITY_FACILITIES = ("IAD", "DCA", "BWI")
_PRIORITY_SAMPLE_DIR = "/var/lib/corporatetraveldc/smes_debug_priority"
_PRIORITY_SAMPLE_MAX = 25
_priority_debug_count = 0


def _maybe_capture_priority_sample(xml_bytes: bytes, kind: str) -> None:
    global _priority_debug_count
    if _priority_debug_count >= _PRIORITY_SAMPLE_MAX:
        return
    # Cheap pre-check before touching XML parsing: <src>IAD</src> etc.
    if not any(f"<src>{fac}</src>".encode() in xml_bytes for fac in _PRIORITY_FACILITIES):
        return
    try:
        os.makedirs(_PRIORITY_SAMPLE_DIR, exist_ok=True)
        path = f"{_PRIORITY_SAMPLE_DIR}/{kind}_priority_{_priority_debug_count}.xml"
        with open(path, "wb") as f:
            f.write(xml_bytes)
        _priority_debug_count += 1
        log.info("stdds: wrote PRIORITY %s debug sample %s (%d bytes)", kind, path, len(xml_bytes))
    except Exception as e:
        log.warning("stdds: priority %s debug sample capture failed: %s", kind, e)


def _capture_debug_sample(xml_bytes: bytes, kind: str) -> None:
    _maybe_capture_priority_sample(xml_bytes, kind)
    global _smes_debug_count, _tais_debug_count
    count = _smes_debug_count if kind == "smes" else _tais_debug_count
    cap = _DEBUG_SAMPLE_MAX_SMES if kind == "smes" else _DEBUG_SAMPLE_MAX_TAIS
    if count >= cap:
        return
    try:
        os.makedirs(_DEBUG_SAMPLE_DIR, exist_ok=True)
        path = f"{_DEBUG_SAMPLE_DIR}/{kind}_sample_{count}.xml"
        with open(path, "wb") as f:
            f.write(xml_bytes)
        log.info("stdds: wrote %s debug sample %s (%d bytes)", kind, path, len(xml_bytes))
    except Exception as e:
        log.warning("stdds: %s debug sample capture failed: %s", kind, e)
    if kind == "smes":
        _smes_debug_count += 1
    else:
        _tais_debug_count += 1


# AIRPORTS we care about for surface tracks.
SMES_AIRPORTS = frozenset({"KDCA", "KIAD", "KBWI"})
TAIS_FACILITY = "PCT"  # Potomac TRACON

# Namespace prefixes used in STDDS surface/terminal messages (SMES path only
# -- kept for now; SMES has no confirmed real sample yet, see below).
STDDS_NS = {
    "smes": "urn:us:gov:dot:faa:atm:terminal:entities:v2-0:smes:base",
    "tais": "urn:us:gov:dot:faa:atm:terminal:entities:v2-0:tais:base",
    "base": "urn:us:gov:dot:faa:atm:terminal:entities:v2-0:base",
    "ds":   "urn:us:gov:dot:faa:atm:ds",
}


def _ns(tag: str, prefix: str) -> str:
    return f"{{{STDDS_NS[prefix]}}}{tag}"


def _find_any(elem: ET.Element, tag: str) -> ET.Element | None:
    """Try each known namespace prefix, then unqualified."""
    for prefix in STDDS_NS:
        child = elem.find(_ns(tag, prefix))
        if child is not None:
            return child
    return elem.find(tag)


def _text(elem: ET.Element | None, *path: str) -> str | None:
    cur = elem
    for step in path:
        if cur is None:
            return None
        cur = _find_any(cur, step)
    return (cur.text or "").strip() or None if cur is not None else None


def _attr(elem: ET.Element | None, name: str) -> str | None:
    if elem is None:
        return None
    v = elem.get(name)
    return v.strip() if v else None


def _local_tag(tag: str) -> str:
    return tag.split("}")[-1]


def _child_local_text(elem: ET.Element, local_name: str) -> str | None:
    for child in elem:
        if _local_tag(child.tag) == local_name:
            return (child.text or "").strip() or None
    return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# -- SMES surface track parser -----------------------------------------------
#
# REAL SCHEMA, confirmed 2026-07-20 two ways: (1) the official FAA
# "FIXM-Mediated STDDS Data Overview" doc's raw-SimpleXML field mapping
# tables, (2) a genuine captured sample (KPHX, not DC-area, but confirms the
# structure lands on this subscription):
#
#   <ns2:asdexMsg xmlns:ns2="...smes:surfacemovementevent">
#     <airport>KPHX</airport>
#     <adsbReport full="false">              <!-- or mlatReport, or positionReport -->
#       <report>
#         <basicReport>
#           <time>2026-07-20T17:05:27.789Z</time>
#           <track>3953</track>              <!-- TRACK_NUMBER -->
#           <position><x>...</x><y>...</y><lat>33.43806</lat><lon>-112.01024</lon></position>
#           <velocity><x>...</x><y>...</y></velocity>   <!-- per doc, not in this sample -->
#         </basicReport>
#         <mode3ACode>...</mode3ACode>       <!-- direct child of report, per doc -->
#         <level>...</level>                 <!-- altitude, direct child of report -->
#       </report>
#       <acAddresss>...</acAddresss>         <!-- direct child of mlatReport/adsbReport, per doc -->
#     </adsbReport>
#     <enhancedData><eramGufi>KS475352JY</eramGufi></enhancedData>
#   </ns2:asdexMsg>
#
# positionReport (SMES Cat11) is FLATTER -- no report/basicReport nesting,
# per doc: /asdexMsg/positionReport/flightId/{acAddress,aircraftId,mode3ACode},
# /asdexMsg/positionReport/flightInfo/acType, /asdexMsg/positionReport/movement/
# {heading,speed,vx,vy}, /asdexMsg/positionReport/position/{altitude,latitude,
# longitude}, /asdexMsg/positionReport/track, /asdexMsg/positionReport/runway.
# No real positionReport sample captured yet (only adsbReport confirmed live) --
# the flat-path handling below is doc-derived, not sample-confirmed; treat as
# higher-confidence-than-guess but still worth a spot-check if one arrives.

def _find_path_local(elem: ET.Element | None, *tags: str) -> ET.Element | None:
    """Walk a chain of direct children by local (namespace-stripped) tag name."""
    cur = elem
    for tag in tags:
        if cur is None:
            return None
        cur = next((c for c in cur if _local_tag(c.tag) == tag), None)
    return cur


def _path_text(elem: ET.Element | None, *tags: str) -> str | None:
    found = _find_path_local(elem, *tags)
    return (found.text or "").strip() or None if found is not None else None


def parse_smes_message(xml_bytes: bytes) -> list[dict]:
    """
    Parse a SMES (Surface Movement Event Service) message -- real asdexMsg
    schema, not the old positionReport/facilityIdentifier tag guesses.
    Returns a list of surface track dicts (usually 0 or 1).
    """
    _capture_debug_sample(xml_bytes, "smes")
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        log.warning("smes: XML parse error: %s", e)
        return []

    if _local_tag(root.tag) != "asdexMsg":
        return []

    airport = _child_local_text(root, "airport")
    if airport:
        airport = airport.upper().strip()
        if not airport.startswith("K") and len(airport) == 3:
            airport = "K" + airport
    if airport not in SMES_AIRPORTS:
        return []

    report_type = next(
        (t for t in ("positionReport", "mlatReport", "adsbReport")
         if any(_local_tag(c.tag) == t for c in root)),
        None,
    )
    if report_type is None:
        return []
    report_elem = _find_path_local(root, report_type)

    eram_gufi = _path_text(root, "enhancedData", "eramGufi") or _path_text(
        report_elem, "enhancedData", "eramGufi")

    if report_type == "positionReport":
        # Flat structure -- doc-derived, not yet sample-confirmed.
        track_id = _path_text(report_elem, "track")
        callsign = (_path_text(report_elem, "flightId", "aircraftId")
                    or _path_text(report_elem, "manual", "callNum"))
        squawk = _path_text(report_elem, "flightId", "mode3ACode")
        aircraft_type = (_path_text(report_elem, "flightInfo", "acType")
                          or _path_text(report_elem, "manual", "acType"))
        lat_str = _path_text(report_elem, "position", "latitude")
        lon_str = _path_text(report_elem, "position", "longitude")
        alt_str = _path_text(report_elem, "position", "altitude")
        spd_str = _path_text(report_elem, "movement", "speed")
        hdg_str = _path_text(report_elem, "movement", "heading")
    else:
        # mlatReport / adsbReport (SMES Cat10) -- nested under report/basicReport,
        # confirmed against a real adsbReport sample (KPHX, 2026-07-20).
        inner = _find_path_local(report_elem, "report")
        basic = _find_path_local(inner, "basicReport")
        track_id = _path_text(basic, "track")
        callsign = None  # not present in either the doc's mapping or the real sample
        squawk = _path_text(inner, "mode3ACode")
        aircraft_type = None
        pos = _find_path_local(basic, "position")
        lat_str = _path_text(pos, "lat")
        lon_str = _path_text(pos, "lon")
        alt_str = _path_text(inner, "level")
        vel = _find_path_local(basic, "velocity")
        vx_str = _path_text(vel, "x")
        vy_str = _path_text(vel, "y")
        spd_str = None  # derived below from vx/vy if present, like FDPS's vx/vy fallback
        hdg_str = None
        if vx_str and vy_str:
            try:
                vx, vy = float(vx_str), float(vy_str)
                spd_str = str((vx ** 2 + vy ** 2) ** 0.5)
            except ValueError:
                pass

    if not track_id:
        return []

    try:
        latitude = float(lat_str) if lat_str else None
        longitude = float(lon_str) if lon_str else None
    except ValueError:
        latitude = longitude = None
    if latitude is None or longitude is None:
        return []

    try:
        altitude_ft = float(alt_str) if alt_str else None
    except ValueError:
        altitude_ft = None
    try:
        speed_kts = int(float(spd_str)) if spd_str else None
    except ValueError:
        speed_kts = None
    try:
        heading_deg = float(hdg_str) if hdg_str else None
    except ValueError:
        heading_deg = None

    return [{
        "track_id": str(track_id),
        "airport": airport,
        "callsign": callsign,
        "squawk": squawk,
        "aircraft_type": aircraft_type,
        "target_type": report_type,
        "latitude": latitude,
        "longitude": longitude,
        "altitude_ft": altitude_ft,
        "speed_kts": speed_kts,
        "heading_deg": heading_deg,
        "eram_gufi": eram_gufi,
        "last_seen": _now_iso(),
    }]


def write_surface_tracks(tracks: list[dict]) -> int:
    """Upsert a list of surface track dicts. Returns count written."""
    written = 0
    for t in tracks:
        try:
            db.upsert_surface_track(
                track_id=t["track_id"],
                airport=t["airport"],
                callsign=t.get("callsign"),
                squawk=t.get("squawk"),
                aircraft_type=t.get("aircraft_type"),
                target_type=t.get("target_type"),
                latitude=t["latitude"],
                longitude=t["longitude"],
                altitude_ft=t.get("altitude_ft"),
                speed_kts=t.get("speed_kts"),
                heading_deg=t.get("heading_deg"),
                eram_gufi=t.get("eram_gufi"),
                last_seen=t["last_seen"],
            )
            written += 1
        except Exception as e:
            log.error("smes: DB write error for track %s: %s", t.get("track_id"), e)
    return written


# -- TAIS terminal track parser ----------------------------------------------
#
# REAL SCHEMA, confirmed 2026-07-20 against 5 live captured messages (see
# _capture_debug_sample above). The old tag guesses (TrackPositionEvent,
# facilityIdentifier, aircraftIdentification, modeACode/ssrCode, altitude,
# groundSpeed, latitude/longitude) never matched anything -- that's why
# terminal_tracks stayed 0 rows all session. Real structure:
#
#   <ns2:TATrackAndFlightPlan xmlns:ns2="...tais:terminalautomationinformation">
#     <src>TRI</src>                      <!-- facility, once per message -->
#     <record>
#       <recSeqNum>29566</recSeqNum>
#       <recType>210</recType>            <!-- 210 = surveillance track -->
#       <track>
#         <trackNum>3045</trackNum>
#         <mrtTime>2026-07-20T14:05:44.044Z</mrtTime>
#         <status>active</status>
#         <acAddress>000000</acAddress>   <!-- Mode S address, may be all-0 -->
#         <lat>38.01017</lat>
#         <lon>-82.08506</lon>
#         <reportedBeaconCode>6045</reportedBeaconCode>
#         <reportedAltitude>12900</reportedAltitude>
#         <vx>175</vx><vy>219</vy>        <!-- velocity components, not a
#                                              single "groundSpeed" field -->
#       </track>
#     </record>
#     <record>...</record>  <!-- repeated, many tracks per message -->
#   </ns2:TATrackAndFlightPlan>
#
# No callsign/acid field was present on any recType=210 (surveillance-only)
# record in the captured samples -- TAIS apparently carries flight-plan
# correlation (callsign) on a different recType this capture didn't happen
# to catch. callsign is left None when absent rather than guessed.
#
# All 5 captured samples had <src>TRI</src> (a different TRACON), not PCT --
# so the PCT-only filter below is unverified against a real PCT-sourced
# message, but the container/field-name fix itself is now evidence-based.

def parse_tais_message(xml_bytes: bytes) -> list[dict]:
    """
    Parse a TAIS TATrackAndFlightPlan message. Returns a list of terminal
    track dicts for PCT TRACON (src == "PCT"); other facilities are skipped.
    """
    _capture_debug_sample(xml_bytes, "tais")
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        log.warning("tais: XML parse error: %s", e)
        return []

    if "TATrackAndFlightPlan" not in root.tag:
        return []

    facility = (_child_local_text(root, "src") or "").upper().strip()
    if facility != TAIS_FACILITY:
        return []

    results: list[dict] = []
    for record in root:
        if _local_tag(record.tag) != "record":
            continue
        track_elem = None
        for child in record:
            if _local_tag(child.tag) == "track":
                track_elem = child
                break
        if track_elem is None:
            continue
        track = _parse_tais_track(track_elem, facility)
        if track:
            results.append(track)

    return results


def _parse_tais_track(track_elem: ET.Element, facility: str) -> dict | None:
    track_id = _child_local_text(track_elem, "trackNum")
    if not track_id:
        return None

    lat_str = _child_local_text(track_elem, "lat")
    lon_str = _child_local_text(track_elem, "lon")
    try:
        latitude = float(lat_str) if lat_str else None
        longitude = float(lon_str) if lon_str else None
    except ValueError:
        latitude = longitude = None

    squawk = _child_local_text(track_elem, "reportedBeaconCode")
    mode_s = _child_local_text(track_elem, "acAddress")
    # "000000" is TAIS's placeholder for "no Mode S address available" --
    # normalize to None rather than storing a fake address.
    if mode_s == "000000":
        mode_s = None

    alt_str = _child_local_text(track_elem, "reportedAltitude")
    try:
        altitude_ft = float(alt_str) if alt_str else None
    except ValueError:
        altitude_ft = None

    # Ground speed isn't a direct field -- derive magnitude from vx/vy
    # velocity components (units unconfirmed, likely knots; treated as an
    # approximation, not authoritative).
    vx_str = _child_local_text(track_elem, "vx")
    vy_str = _child_local_text(track_elem, "vy")
    ground_speed = None
    try:
        if vx_str and vy_str:
            ground_speed = int(round((float(vx_str) ** 2 + float(vy_str) ** 2) ** 0.5))
    except ValueError:
        pass

    # No callsign field observed on surveillance-only (recType 210) records
    # in the captured samples -- left None rather than guessed.
    callsign = None

    return {
        "track_id": str(track_id),
        "facility": facility,
        "callsign": callsign,
        "squawk": squawk,
        "mode_s": mode_s,
        "latitude": latitude,
        "longitude": longitude,
        "altitude_ft": altitude_ft,
        "ground_speed": ground_speed,
        "last_seen": _now_iso(),
    }


def write_terminal_tracks(tracks: list[dict]) -> int:
    """Upsert a list of terminal track dicts. Returns count written."""
    written = 0
    for t in tracks:
        try:
            db.upsert_terminal_track(
                track_id=t["track_id"],
                facility=t["facility"],
                callsign=t.get("callsign"),
                squawk=t.get("squawk"),
                mode_s=t.get("mode_s"),
                latitude=t.get("latitude"),
                longitude=t.get("longitude"),
                altitude_ft=t.get("altitude_ft"),
                ground_speed=t.get("ground_speed"),
                last_seen=t["last_seen"],
            )
            written += 1
        except Exception as e:
            log.error("tais: DB write error for track %s: %s", t.get("track_id"), e)
    return written
