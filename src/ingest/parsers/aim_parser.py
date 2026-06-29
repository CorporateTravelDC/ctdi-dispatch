"""
ingest.parsers.aim_parser — FAA FNS AIM NOTAM parser (AIXM 5.1 BasicMessage).

FNS delivers AIXM 5.1 AIXMBasicMessage XML over Solace AMQP. Structure:

  message:AIXMBasicMessage
    message:hasMember
      event:Event
        event:timeSlice > event:EventTimeSlice
          event:textNOTAM > event:NOTAM   ← NOTAM payload
          event:extension > fnse:EventExtension  ← ICAO loc + classification
    message:hasMember
      aixm:AirportHeliport   ← airport reference, ignored

Alert routing:
  Permanent watch set : DC_STATIONS (KDCA, KIAD, KBWI, KFDK, KHEF, KJYO, KGAI)
  Transient watch set : K[A-Z]{3} codes in today's runsheet trip locations,
                        minus permanent set (non-DC origin/dest airports)
  FDC NOTAMs          : always alert regardless of facility
  Dedup               : 24h window keyed on notam_id (PushDedup "notam")
  VIP NOTAMs          : FDC NOTAMs containing POTUS/AF1/Marine One keywords →
                        hot-alerts priority=5; all others → nas-alerts priority=3

NOTAM ID: "{location}/{year}/{number}" e.g. "PSG/2026/081"
Effective timestamps: YYYYMMDDHHmm compact (12-digit UTC) e.g. "202606152335"
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone

import xml.etree.ElementTree as ET

from common import db
from common.ntfy_push import send as ntfy_send
from common.push_dedup import PushDedup, content_hash
from ingest import config as ingest_config
from ingest.parsers.geo_filter import is_core_airport

log = logging.getLogger("ingest.parsers.aim")

_NS = {
    "message": "http://www.aixm.aero/schema/5.1/message",
    "event":   "http://www.aixm.aero/schema/5.1/event",
    "aixm":    "http://www.aixm.aero/schema/5.1",
    "fnse":    "http://www.aixm.aero/schema/5.1/extensions/FAA/FNSE",
    "gml":     "http://www.opengis.net/gml/3.2",
}

# Permanent watch set — mirrors DC_STATIONS in metar.py
_PERMANENT_AIRPORTS: frozenset[str] = frozenset({
    "KDCA", "KIAD", "KBWI", "KFDK", "KHEF", "KJYO", "KGAI",
})

_ICAO_RE = re.compile(r"\b(K[A-Z]{3})\b")
_DEDUP_TTL = 86400   # 24 hours — one push per NOTAM per day
_NOTAM_DEDUP = PushDedup("notam", dedup_secs=_DEDUP_TTL)

_VIP_KEYWORDS = frozenset({"POTUS", "PRESIDENT", "AIR FORCE ONE", "MARINE ONE", "AIR FORCE 1", "AF1"})


def _get_facility_filter() -> frozenset[str]:
    """
    Returns the effective facility alert set: permanent DC airports + any extras
    from NOTAM_FACILITY_FILTER. Called per-batch so env changes take effect on
    ingest restart without a code rebuild.
    """
    cfg = ingest_config.NotamConfig()
    extra = frozenset(f.upper() for f in cfg.facility_filter if f.strip())
    return _PERMANENT_AIRPORTS | extra


def _is_vip_notam(notam_text: str) -> bool:
    upper = (notam_text or "").upper()
    return any(kw in upper for kw in _VIP_KEYWORDS)


def _txt(elem: ET.Element | None, path: str) -> str | None:
    if elem is None:
        return None
    found = elem.find(path, _NS)
    if found is None:
        return None
    return (found.text or "").strip() or None


def _parse_timestamp(ts: str | None) -> float | None:
    if not ts:
        return None
    ts = ts.strip()
    for fmt in (
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S",
        "%Y%m%d%H%M",   # 12-digit YYYYMMDDHHmm e.g. "202606152335"
        "%y%m%d%H%M",   # 10-digit YYMMDDHHmm
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


def _get_transient_airports() -> frozenset[str]:
    """
    Extract non-permanent K[A-Z]{3} codes from today's runsheet trip locations.
    Only airport-leg trips will contain ICAO codes in their location strings.
    """
    try:
        sheet = db.get_runsheet()
        if not sheet:
            return frozenset()
        raw = sheet.get("scheduled_trips") or "[]"
        trips = json.loads(raw) if isinstance(raw, str) else raw
        found: set[str] = set()
        for trip in trips:
            for field in ("pickup_location", "dropoff_location"):
                text = trip.get(field, "") or ""
                for m in _ICAO_RE.finditer(text.upper()):
                    found.add(m.group(1))
        return frozenset(found - _PERMANENT_AIRPORTS)
    except Exception as e:
        log.debug("aim: transient airport lookup failed: %s", e)
        return frozenset()


def _fire_notam_alert(notam: dict) -> None:
    """Push ntfy alert for a NOTAM that matches the watch set.

    Routing:
      VIP NOTAMs (POTUS/AF1/Marine One keywords) → hot-alerts, priority=5
      All other NOTAMs                           → nas-alerts, priority=3
    dispatch-alerts is not used for NOTAMs.
    """
    notam_id = notam["notam_id"]
    dedup_key = content_hash(notam_id)
    if not _NOTAM_DEDUP.should_push(notam_id, dedup_key):
        return

    facility = notam.get("facility", "")
    classification = notam.get("classification", "NOTAM-D")
    text_body = notam.get("text_body", "")
    label = "FDC NOTAM" if classification == "FDC" else "NOTAM"

    title = f"{label} [{facility}] — {notam_id}"
    body = text_body[:400] if text_body else notam_id

    if _is_vip_notam(text_body):
        topic = "hot-alerts"
        priority = 5
    else:
        topic = "nas-alerts"
        priority = 3

    ok = ntfy_send(
        topic=topic,
        message=body,
        title=title,
        priority=priority,
        tags="warning,airplane",
    )
    if ok:
        _NOTAM_DEDUP.record(notam_id, dedup_key)
        log.info("aim: notam alert fired: %s facility=%s topic=%s priority=%d",
                 notam_id, facility, topic, priority)


def parse_aim_message(xml_bytes: bytes) -> list[dict]:
    """
    Parse an FNS AIM AIXM 5.1 message. Returns list of NOTAM dicts
    ready for write_aim_notams().
    """
    if not xml_bytes:
        return []
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        log.warning("aim: XML parse error: %s", e)
        return []

    notams: list[dict] = []

    for member in root.findall("message:hasMember", _NS):
        event = member.find("event:Event", _NS)
        if event is None:
            continue

        for ts_elem in event.findall(".//event:EventTimeSlice", _NS):
            notam_elem = ts_elem.find("event:textNOTAM/event:NOTAM", _NS)
            if notam_elem is None:
                continue

            number      = _txt(notam_elem, "event:number") or ""
            year        = _txt(notam_elem, "event:year") or ""
            location    = _txt(notam_elem, "event:location") or ""
            notam_type  = _txt(notam_elem, "event:type") or "N"
            issued      = _txt(notam_elem, "event:issued")
            text_body   = _txt(notam_elem, "event:text") or ""
            simple_text = _txt(notam_elem, ".//event:simpleText") or ""
            eff_start   = _txt(notam_elem, "event:effectiveStart")
            eff_end     = _txt(notam_elem, "event:effectiveEnd")
            fir         = _txt(notam_elem, "event:affectedFIR") or ""

            ext = ts_elem.find("event:extension/fnse:EventExtension", _NS)
            icao_loc  = _txt(ext, "fnse:icaoLocation") if ext is not None else None
            fns_class = _txt(ext, "fnse:classification") if ext is not None else "DOM"

            notam_id = f"{location}/{year}/{number}" if (location and year and number) else None
            if not notam_id:
                gml_id = event.get("{http://www.opengis.net/gml/3.2}id", "")
                notam_id = gml_id or None
            if not notam_id:
                log.debug("aim: skipping NOTAM with no ID")
                continue

            full_text = simple_text or text_body
            classification = "FDC" if (fns_class or "").upper() == "FDC" else "NOTAM-D"

            notams.append({
                "notam_id":        notam_id,
                "facility":        icao_loc or location or "",
                "classification":  classification,
                "effective_start": _parse_timestamp(eff_start),
                "effective_end":   _parse_timestamp(eff_end),
                "text_body":       full_text,
                "raw_json": json.dumps({
                    "notam_id":    notam_id,
                    "number":      number,
                    "year":        year,
                    "type":        notam_type,
                    "location":    location,
                    "icao":        icao_loc,
                    "fir":         fir,
                    "text":        text_body,
                    "simple_text": simple_text,
                    "issued":      issued,
                    "source":      "swim_aim",
                }),
            })

    if not notams:
        log.debug("aim: no NOTAMs parsed (root=%s)", root.tag)

    return notams


def write_aim_notams(notams: list[dict]) -> int:
    """Upsert parsed NOTAMs into the notams table and fire alerts where applicable."""
    if not notams:
        return 0

    # Build watch set once per batch (transient query is cheap but not free)
    transient = _get_transient_airports()
    facility_filter = _get_facility_filter()          # permanent DC set + NOTAM_FACILITY_FILTER
    watch_set = facility_filter | transient

    written = 0
    for n in notams:
        facility = n["facility"]
        is_fdc   = n["classification"] == "FDC"
        is_vip   = _is_vip_notam(n.get("text_body", ""))

        # Geo filter on DB writes.
        # Always store: VIP NOTAMs (POTUS/AF1/Marine One), FDC NOTAMs (national scope),
        # and any NOTAM-D whose facility is in CORE_AIRPORTS or the configured watch set.
        in_watch = facility in watch_set
        in_core  = is_core_airport(facility)
        if not (is_vip or is_fdc or in_watch or in_core):
            log.debug("aim: geo-filtered NOTAM %s facility=%s (not in core or watch set)",
                      n["notam_id"], facility)
            continue

        try:
            db.upsert_notam(
                notam_id=n["notam_id"],
                raw_json=n["raw_json"],
                facility=facility,
                classification=n["classification"],
                effective_start=n.get("effective_start"),
                effective_end=n.get("effective_end"),
                text_body=n["text_body"],
            )
            written += 1

            # Alert routing: VIP always; others only when in watch set.
            if is_vip or in_watch:
                _fire_notam_alert(n)
            elif is_fdc:
                log.debug("aim: FDC NOTAM stored but not alerted (facility=%s not in watch set)", facility)

        except Exception as e:
            log.error("aim: db write error for %s: %s", n.get("notam_id"), e)

    return written
