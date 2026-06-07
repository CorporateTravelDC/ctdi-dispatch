"""
ingest.parsers.smes_parser — STDDS surface and terminal track parser.

SMES (Surface Movement Events): ASDE-X surface positions at DCA/IAD/BWI.
TAIS (Terminal Automation Information Service): PCT TRACON radar tracks.
"""
from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from common import db

log = logging.getLogger("ingest.parsers.smes")

# AIRPORTS we care about for surface tracks.
SMES_AIRPORTS = frozenset({"KDCA", "KIAD", "KBWI"})
TAIS_FACILITY = "PCT"  # Potomac TRACON

# Namespace prefixes used in STDDS surface/terminal messages.
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


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── SMES surface track parser ─────────────────────────────────────────────────

def parse_smes_message(xml_bytes: bytes) -> list[dict]:
    """
    Parse a SMES (Surface Movement Event Service) message.
    Returns a list of surface track dicts.
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        log.warning("smes: XML parse error: %s", e)
        return []

    results: list[dict] = []

    # The message may be a single positionReport or wrap multiple.
    pos_reports = list(root.iter())
    report_elems = [
        el for el in pos_reports
        if el.tag.endswith("}positionReport") or el.tag == "positionReport"
    ]
    if not report_elems:
        # Try the root itself if it looks like a position report.
        if root.tag.endswith("}positionReport") or root.tag == "positionReport":
            report_elems = [root]

    for rpt in report_elems:
        track = _parse_smes_report(rpt)
        if track:
            results.append(track)

    return results


def _parse_smes_report(rpt: ET.Element) -> dict | None:
    # Airport (facility identifier).
    airport = (_attr(rpt, "airport")
               or _attr(rpt, "facilityIdentifier")
               or _text(rpt, "facilityIdentifier")
               or _text(rpt, "airport"))
    if airport:
        airport = airport.upper().strip()
        if not airport.startswith("K"):
            airport = "K" + airport
    if airport not in SMES_AIRPORTS:
        return None

    track_id = (_attr(rpt, "trackNumber")
                or _text(rpt, "trackNumber")
                or _attr(rpt, "id"))
    if not track_id:
        return None

    callsign = (_text(rpt, "aircraftIdentification")
                or _text(rpt, "acid")
                or _attr(rpt, "acid"))
    squawk = _text(rpt, "modeACode") or _text(rpt, "ssrCode")
    aircraft_type = _text(rpt, "aircraftType") or _text(rpt, "acType")
    target_type = _attr(rpt, "targetType") or _text(rpt, "targetType")
    eram_gufi = _text(rpt, "eramGufi") or _text(rpt, "gufi")

    # Position.
    lat_str = (_text(rpt, "latitude")
               or _attr(rpt, "latitude"))
    lon_str = (_text(rpt, "longitude")
               or _attr(rpt, "longitude"))
    pos_text = _text(rpt, "position", "pos")
    if pos_text and (lat_str is None or lon_str is None):
        parts = pos_text.strip().split()
        if len(parts) >= 2:
            lat_str, lon_str = parts[0], parts[1]

    try:
        latitude = float(lat_str) if lat_str else None
        longitude = float(lon_str) if lon_str else None
    except ValueError:
        latitude = longitude = None

    if latitude is None or longitude is None:
        return None

    alt_str = _text(rpt, "altitude") or _text(rpt, "altitude", "value")
    spd_str = _text(rpt, "speed") or _text(rpt, "groundSpeed")
    hdg_str = _text(rpt, "heading") or _text(rpt, "track")

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

    return {
        "track_id": str(track_id),
        "airport": airport,
        "callsign": callsign,
        "squawk": squawk,
        "aircraft_type": aircraft_type,
        "target_type": target_type,
        "latitude": latitude,
        "longitude": longitude,
        "altitude_ft": altitude_ft,
        "speed_kts": speed_kts,
        "heading_deg": heading_deg,
        "eram_gufi": eram_gufi,
        "last_seen": _now_iso(),
    }


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


# ── TAIS terminal track parser ────────────────────────────────────────────────

def parse_tais_message(xml_bytes: bytes) -> list[dict]:
    """
    Parse a TAIS (Terminal Automation Information Service) TrackPositionEvent.
    Returns a list of terminal track dicts for PCT TRACON.
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        log.warning("tais: XML parse error: %s", e)
        return []

    results: list[dict] = []
    track_elems = [
        el for el in root.iter()
        if el.tag.endswith("}TrackPositionEvent") or el.tag == "TrackPositionEvent"
           or el.tag.endswith("}trackPositionEvent") or el.tag == "trackPositionEvent"
    ]
    if not track_elems and (
        root.tag.endswith("}TrackPositionEvent") or root.tag == "TrackPositionEvent"
    ):
        track_elems = [root]

    for el in track_elems:
        track = _parse_tais_event(el)
        if track:
            results.append(track)

    return results


def _parse_tais_event(el: ET.Element) -> dict | None:
    facility = (_attr(el, "facilityIdentifier")
                or _text(el, "facilityIdentifier")
                or TAIS_FACILITY)
    facility = facility.upper().strip()

    track_id = (_attr(el, "trackNumber")
                or _text(el, "trackNumber")
                or _attr(el, "id"))
    if not track_id:
        return None

    callsign = (_text(el, "aircraftIdentification")
                or _text(el, "acid")
                or _attr(el, "acid"))
    squawk = _text(el, "modeACode") or _text(el, "ssrCode")
    mode_s = _text(el, "modeSAddress") or _attr(el, "modeSAddress")

    lat_str = _text(el, "latitude") or _attr(el, "latitude")
    lon_str = _text(el, "longitude") or _attr(el, "longitude")
    pos_text = _text(el, "position", "pos")
    if pos_text and (lat_str is None or lon_str is None):
        parts = pos_text.strip().split()
        if len(parts) >= 2:
            lat_str, lon_str = parts[0], parts[1]

    try:
        latitude = float(lat_str) if lat_str else None
        longitude = float(lon_str) if lon_str else None
    except ValueError:
        latitude = longitude = None

    alt_str = _text(el, "altitude") or _text(el, "altitude", "value")
    spd_str = _text(el, "groundSpeed") or _text(el, "speed")

    try:
        altitude_ft = float(alt_str) if alt_str else None
    except ValueError:
        altitude_ft = None
    try:
        ground_speed = int(float(spd_str)) if spd_str else None
    except ValueError:
        ground_speed = None

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
