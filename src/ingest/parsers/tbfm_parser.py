"""
ingest.parsers.tbfm_parser — FAA TBFM (Time-Based Flow Management) NMS parser.

TBFM delivers arrival sequencing and metering data for DC-area airports:
  - Meter fix ETAs and sequence numbers (DCA/IAD/BWI arrival streams)
  - Assigned crossing times at meter fixes (LUCIT, SWANN, RAVNN, etc.)
  - Speed assignments from TBFM automation

Data is written to the tbfm_sequences table. Unlike GDP/GS programs from TFMS,
TBFM data is not available via any REST API — NMS is the only source.

Heartbeat key: "tbfm" (no REST fallback; when NMS is down this data is simply absent)

DC-area meter fixes for IAD/DCA approaches:
  IAD: LUCIT, SWANN, RAVNN, FLUKY, SFARA
  DCA: JIMBO, WAVER, WOOLY
  BWI: PALEO, MERIT
"""
from __future__ import annotations

import json
import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from common import db

log = logging.getLogger("ingest.parsers.tbfm")

_TBFM_NS = {
    "tbfm": "http://tfms.faa.gov/tbfm/v1",
    "nas":  "http://www.faa.aero/nas/4.2",
}

# DC-area meter fixes — filter to only store relevant arrivals
DC_METER_FIXES = frozenset({
    "LUCIT", "SWANN", "RAVNN", "FLUKY", "SFARA",   # IAD
    "JIMBO", "WAVER", "WOOLY",                       # DCA
    "PALEO", "MERIT",                                # BWI
})


def _txt(elem: ET.Element | None, *tags: str) -> str | None:
    cur = elem
    for tag in tags:
        if cur is None:
            return None
        found = cur.find(tag)
        if found is None:
            for uri in _TBFM_NS.values():
                found = cur.find(f"{{{uri}}}{tag}")
                if found is not None:
                    break
        cur = found
    return (cur.text or "").strip() or None if cur is not None else None


def _parse_eta(ts: str | None) -> str | None:
    if not ts:
        return None
    ts = ts.strip()
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        pass
    # Compact format YYYYMMDDHHMMSS
    for fmt in ("%Y%m%d%H%M%S", "%Y%m%d%H%M"):
        try:
            dt = datetime.strptime(ts, fmt).replace(tzinfo=timezone.utc)
            return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            pass
    return ts  # return as-is if unparseable


def parse_tbfm_message(xml_bytes: bytes) -> list[dict]:
    """
    Parse a TBFM NMS XML message.
    Returns list of sequence dicts: {meter_fix, facility, flight_id, eta,
    sequence_num, assigned_speed}.
    Filters to DC-area meter fixes only. Returns all if no DC fixes found
    (to handle messages from facilities that don't use canonical fix names).
    """
    if not xml_bytes:
        return []
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        log.warning("tbfm: XML parse error: %s", e)
        return []

    sequences: list[dict] = []

    # TBFM messages may nest sequences in various ways; scan generically.
    _SEQ_TAGS = {
        "arrivalSequence", "sequenceElement", "meterData",
        "tbfmData", "flightData", "arrivalFlight",
    }

    for elem in root.iter():
        local = elem.tag.split("}")[-1]
        if local in _SEQ_TAGS:
            seq = _parse_single_sequence(elem)
            if seq:
                sequences.append(seq)

    # Filter to DC-area meter fixes (or keep all if we get no DC hits)
    dc_seqs = [s for s in sequences if s["meter_fix"].upper() in DC_METER_FIXES]
    result = dc_seqs if dc_seqs else sequences

    if not result and xml_bytes:
        # Log first 300 bytes of unrecognised messages to help with format capture
        log.debug("tbfm: no sequences parsed; raw prefix: %s",
                  xml_bytes[:300].decode("utf-8", errors="replace"))

    return result


def _parse_single_sequence(elem: ET.Element) -> dict | None:
    flight_id = (
        _txt(elem, "acid") or
        _txt(elem, "flightId") or
        _txt(elem, "callsign") or
        _txt(elem, "aircraftId")
    )
    if not flight_id:
        return None

    meter_fix = (
        _txt(elem, "meterFix") or
        _txt(elem, "fix") or
        _txt(elem, "meter_fix") or
        _txt(elem, "arrivalFix")
    )
    if not meter_fix:
        return None

    facility = (
        _txt(elem, "facility") or
        _txt(elem, "artcc") or
        _txt(elem, "tracon") or
        "ZDC"  # default to Washington ARTCC for DC-area data
    )

    eta_raw = (
        _txt(elem, "eta") or
        _txt(elem, "assignedTime") or
        _txt(elem, "scheduledTime") or
        _txt(elem, "estimatedArrival")
    )

    seq_raw = _txt(elem, "sequence") or _txt(elem, "sequenceNum") or _txt(elem, "seqNum")
    spd_raw = _txt(elem, "assignedSpeed") or _txt(elem, "speed")

    return {
        "meter_fix": meter_fix.upper(),
        "facility": facility,
        "flight_id": flight_id.upper(),
        "eta": _parse_eta(eta_raw) or "",
        "sequence_num": int(seq_raw) if seq_raw and seq_raw.isdigit() else None,
        "assigned_speed": int(spd_raw) if spd_raw and spd_raw.isdigit() else None,
    }


def write_tbfm_sequences(sequences: list[dict]) -> int:
    """Upsert TBFM sequences into tbfm_sequences table. Returns count written."""
    written = 0
    for s in sequences:
        if not s.get("eta"):
            continue
        try:
            db.upsert_tbfm_sequence(
                meter_fix=s["meter_fix"],
                facility=s["facility"],
                flight_id=s["flight_id"],
                eta=s["eta"],
                sequence_num=s.get("sequence_num"),
                assigned_speed=s.get("assigned_speed"),
            )
            written += 1
        except Exception as e:
            log.error("tbfm: db write error for %s@%s: %s",
                      s.get("flight_id"), s.get("meter_fix"), e)
    return written
