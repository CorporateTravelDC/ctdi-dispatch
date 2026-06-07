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

Message format: TFMS XML — root element varies by product type.
Known namespaces from FAA TFMS SWIM documentation.
"""
from __future__ import annotations

import json
import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from common import db

log = logging.getLogger("ingest.parsers.tfms")

# TFMS XML namespace prefixes
_TFMS_NS = {
    "tfms": "http://tfms.faa.gov/tfms/v1",
    "nas":  "http://www.faa.aero/nas/4.2",
}

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


def parse_tfms_message(xml_bytes: bytes) -> list[dict]:
    """
    Parse a TFMS NMS XML message. Returns a list of program dicts.
    Returns empty list on parse error.
    """
    if not xml_bytes:
        return []
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        log.warning("tfms: XML parse error: %s", e)
        return []

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

    return programs


def write_tfms_programs(programs: list[dict]) -> int:
    """Upsert parsed TFMS programs into nas_programs. Returns count written."""
    written = 0
    for p in programs:
        try:
            db.upsert_nas_program(
                program_id=p["program_id"],
                prog_type=p["type"],
                facility=p.get("facility") or "",
                raw_json=json.dumps(p),
            )
            written += 1
        except Exception as e:
            log.error("tfms: db write error for %s: %s", p.get("program_id"), e)
    return written
