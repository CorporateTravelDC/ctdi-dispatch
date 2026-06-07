"""
ingest.parsers.aim_parser — FAA AIM (Aeronautical Information Management) NMS parser.

AIM on NMS delivers Digital NOTAMs (DONUTS format or ICAO NOTAM XML).
This replaces the legacy AIM_FNS/JMS topic subscription.

The poller's REST notam.py fetcher defers to push:fns when this feed is healthy,
making the REST fetcher a pure fallback. The heartbeat key used by this feed
is "fns" (not "aim") for REST-fallback compatibility.

DONUTS format reference: FAA JO 7930.2, Appendix C (Digital NOTAM)
ICAO NOTAM format reference: ICAO Annex 15
"""
from __future__ import annotations

import json
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from common import db

log = logging.getLogger("ingest.parsers.aim")

_AIM_NS = {
    "aim":  "http://www.faa.aero/aim/1.0",
    "notam":"http://www.faa.aero/notam/1.0",
    "aixm": "http://www.aixm.aero/schema/5.1",
    "gml":  "http://www.opengis.net/gml/3.2",
}

# NOTAM type classification keywords
_NOTAM_TYPE_MAP = {
    "N": "NOTAM-D",
    "C": "CANCEL",
    "R": "REPLACE",
}


def _txt(elem: ET.Element | None, *tags: str) -> str | None:
    cur = elem
    for tag in tags:
        if cur is None:
            return None
        found = cur.find(tag)
        if found is None:
            for uri in _AIM_NS.values():
                found = cur.find(f"{{{uri}}}{tag}")
                if found is not None:
                    break
        cur = found
    return (cur.text or "").strip() or None if cur is not None else None


def _parse_timestamp(ts: str | None) -> float | None:
    if not ts:
        return None
    ts = ts.strip()
    for fmt in (
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S",
        "%Y%m%d%H%M",       # NOTAM compact: 2606281400
        "%y%m%d%H%M",       # NOTAM compact (2-digit year): 260628
    ):
        try:
            dt = datetime.strptime(ts, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except ValueError:
        pass
    return None


def _classify_notam(notam_id: str, text: str) -> str:
    """Determine NOTAM classification from ID prefix or text."""
    if not notam_id:
        return "NOTAM-D"
    prefix = notam_id.split("/")[0].upper() if "/" in notam_id else notam_id[:1].upper()
    if prefix == "A":
        return "NOTAM-D"
    if prefix.startswith("FDC"):
        return "FDC"
    # Check text for FDC indicator
    if text and "FDC" in text.upper():
        return "FDC"
    return "NOTAM-D"


def parse_aim_message(xml_bytes: bytes) -> list[dict]:
    """
    Parse an AIM NMS XML message. Returns list of NOTAM dicts.
    Each dict contains the fields required by db.upsert_notam().
    """
    if not xml_bytes:
        return []
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        log.warning("aim: XML parse error: %s", e)
        return []

    raw_xml = xml_bytes.decode("utf-8", errors="replace")
    notams: list[dict] = []

    # Scan for NOTAM elements — try several possible root structures
    _NOTAM_TAGS = {"NOTAM", "notam", "digitalNotam", "notamMessage", "Notam"}

    candidates: list[ET.Element] = []
    for elem in root.iter():
        local = elem.tag.split("}")[-1]
        if local in _NOTAM_TAGS:
            candidates.append(elem)

    if not candidates:
        candidates = [root]

    for elem in candidates:
        notam = _parse_single_notam(elem, raw_xml)
        if notam:
            notams.append(notam)

    if not notams:
        log.debug("aim: no NOTAMs parsed from message (tag=%s)", root.tag)

    return notams


def _parse_single_notam(elem: ET.Element, raw_xml: str) -> dict | None:
    notam_id = (
        _txt(elem, "NOTAM-ID") or
        _txt(elem, "notamId") or
        _txt(elem, "id") or
        _txt(elem, "series")
    )
    if not notam_id:
        return None

    facility = (
        _txt(elem, "ICAO-LOCATION") or
        _txt(elem, "icaoLocation") or
        _txt(elem, "location") or
        _txt(elem, "airport")
    )

    text_body = (
        _txt(elem, "TEXT") or
        _txt(elem, "text") or
        _txt(elem, "notamText") or
        _txt(elem, "fullNotam") or
        raw_xml[:500]  # last resort: prefix of raw message
    )

    classification = _classify_notam(notam_id, text_body or "")

    effective_start = _parse_timestamp(
        _txt(elem, "EFFECTIVE-FROM") or _txt(elem, "effectiveStart") or _txt(elem, "startTime")
    )
    effective_end = _parse_timestamp(
        _txt(elem, "EFFECTIVE-TO") or _txt(elem, "effectiveEnd") or _txt(elem, "endTime")
    )

    return {
        "notam_id": notam_id,
        "facility": facility or "",
        "classification": classification,
        "effective_start": effective_start,
        "effective_end": effective_end,
        "text_body": text_body or "",
        "raw_json": json.dumps({
            "notam_id": notam_id,
            "facility": facility,
            "classification": classification,
            "text_body": text_body,
            "source": "swim_aim",
        }),
    }


def write_aim_notams(notams: list[dict]) -> int:
    """Upsert parsed NOTAMs into the notams table. Returns count written."""
    written = 0
    for n in notams:
        try:
            db.upsert_notam(
                notam_id=n["notam_id"],
                raw_json=n["raw_json"],
                facility=n["facility"],
                classification=n["classification"],
                effective_start=n.get("effective_start"),
                effective_end=n.get("effective_end"),
                text_body=n["text_body"],
            )
            written += 1
        except Exception as e:
            log.error("aim: db write error for %s: %s", n.get("notam_id"), e)
    return written
