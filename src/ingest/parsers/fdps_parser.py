"""
ingest.parsers.fdps_parser — SFDPS FIXM 4.x XML parser for FAA NMS/FDPS feed.

Parses FH (full flight plan), TH (track position), CL (cancellation),
HP/OH (handoff events), and HZ (heartbeat position) message types.

Marine One / POTUS detection: fires swim_alert and ntfy for POTUS callsigns
within 50nm of DCA.
"""
from __future__ import annotations

import logging
import math
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from typing import Any

from common import db

log = logging.getLogger("ingest.parsers.fdps")

# Primary FIXM 4.x namespaces seen in SFDPS
NSMAP = {
    "fx":  "http://www.fixm.aero/flight/4.2",
    "fb":  "http://www.fixm.aero/base/4.2",
    "msg": "http://www.fixm.aero/messaging/4.2",
    "nas": "http://www.fixm.aero/nas/4.2",
    "xsi": "http://www.w3.org/2001/XMLSchema-instance",
}

# ── Marine One / POTUS detection ──────────────────────────────────────────────

MARINE_ONE_CALLSIGNS = frozenset({
    "MARINE1", "MARINE2", "SAM", "AF1", "AF2", "EXEC1F",
    "VENUS", "MUSEL", "AZAZ01", "AZAZ09",
})
MARINE_ONE_SQUAWKS = frozenset({"7700", "5000", "5001"})
DC_LAT, DC_LON = 38.8522, -77.0376
MARINE_ONE_RADIUS_NM = 50.0


def _haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in nautical miles."""
    R_NM = 3440.065
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return R_NM * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def distance_to_dca_nm(lat: float, lon: float) -> float:
    return _haversine_nm(lat, lon, DC_LAT, DC_LON)


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


# ── Core parser ───────────────────────────────────────────────────────────────

def parse_fdps_message(xml_bytes: bytes) -> dict | None:
    """
    Parse a single SFDPS FIXM message.
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
    pos_report = _find(en_route, "positionReport") if en_route else None
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
        ssr_elem = _find(pos_report, "ssrCode") or _find(pos_report, "modeACode")
        if ssr_elem is not None:
            squawk = (ssr_elem.text or "").strip() or None

    # Controlling facility.
    ctl_elem = _find(flight, "controllingUnit") or _find(nas_info, "controllingUnit")
    controlling_facility = _text(ctl_elem, "unitIdentifier") if ctl_elem else None

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

def write_flight_event(parsed: dict) -> None:
    """Upsert a parsed FDPS message into flight_events."""
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

            elif source == "TH":
                _maybe_alert_on_approach(entry, parsed)

            elif source == "CL":
                summary = f"{callsign} cancelled (FDPS CL)"
                watchlist_event_hit(entry["id"], summary,
                                    {**parsed, "watchlist_trigger": "fdps_cl"},
                                    priority=4)
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
