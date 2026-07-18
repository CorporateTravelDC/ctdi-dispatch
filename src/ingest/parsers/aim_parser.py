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

Storage/alert routing:
  Permanent watch set : DC_STATIONS (KDCA, KIAD, KBWI, KFDK, KHEF, KJYO, KGAI)
  Transient watch set : K[A-Z]{3} codes in today's runsheet trip locations,
                        minus permanent set (non-DC origin/dest airports)
  DC-region ARTCCs    : ZDC, ZNY, ZID, ZTL, ZOB -- any NOTAM (FDC or NOTAM-D)
                        affecting one of these FIRs is always stored, not just
                        alerted (see DC_REGION_ARTCCS)
  FDC elsewhere       : stored nationwide only if it reads as a major
                        event/closure/airshow/VIP TFR (CFR 91.137/141/143/145,
                        99.7, or matching keywords -- see
                        _is_national_significant). Routine FDC noise from
                        outside the DC region is dropped at write time.
  VIP (POTUS/VP/AF1/AF2/Marine One) : always stored + alerted, nationwide,
                        regardless of facility.
  Dedup               : 24h window keyed on notam_id (PushDedup "notam")
  Alert priority      : VIP → hot-alerts priority=5; everything else in the
                        watch set → nas-alerts priority=3

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

_VIP_KEYWORDS = frozenset({
    "POTUS", "PRESIDENT", "AIR FORCE ONE", "MARINE ONE", "AIR FORCE 1", "AF1",
    "VPOTUS", "VICE PRESIDENT", "AIR FORCE TWO", "AF2",
})

# ARTCCs covering the DC operating region -- any NOTAM tied to one of these
# FIRs is a must-ingest regardless of classification (Washington, New York,
# Indianapolis, Atlanta, Cleveland all border or overlap ZDC-relevant traffic).
DC_REGION_ARTCCS: frozenset[str] = frozenset({"ZDC", "ZNY", "ZID", "ZTL", "ZOB"})

# Nationwide FDC NOTAMs outside the DC region are only worth keeping if they
# read as a genuinely major event -- airshows, VIP movement, disasters,
# space launches, large-scale closures. Everything else nationwide is noise.
# FAA TFR text conventionally cites the governing CFR section, which is the
# most reliable signal; keywords are a fallback for text that doesn't.
_NATIONAL_SIGNIFICANT_CFR_RE = re.compile(r"\b91\.(137|141|143|145)\b|\b99\.7\b")
_NATIONAL_SIGNIFICANT_KEYWORDS = frozenset({
    "AIR SHOW", "AIRSHOW", "AERIAL DEMONSTRATION", "SPORTING EVENT",
    "STADIUM", "SPACE LAUNCH", "SPACEPORT", "DISASTER", "HAZARD AREA",
    "RUNWAY CLOSED", "AIRPORT CLOSED", "CLOSED INDEFINITELY",
})


def _is_national_significant(notam_text: str) -> bool:
    """True if a nationwide FDC NOTAM is a major event/closure/airshow/VIP TFR
    worth keeping outside the DC region (see module docstring)."""
    upper = (notam_text or "").upper()
    if _NATIONAL_SIGNIFICANT_CFR_RE.search(upper):
        return True
    return any(kw in upper for kw in _NATIONAL_SIGNIFICANT_KEYWORDS)


def _artcc_candidates(notam: dict) -> set[str]:
    """Collect every ARTCC/FIR-shaped code available for a parsed NOTAM."""
    cands: set[str] = set()
    for key in ("fir", "location"):
        v = (notam.get(key) or "").upper().strip()
        if v:
            cands.add(v)
    fac = (notam.get("facility") or "").upper().strip()
    if fac:
        cands.add(fac)
        # Some FNS extensions wrap ARTCC codes with a pseudo-ICAO K-prefix
        # (e.g. "KZDC" for the ZDC FIR) -- strip it so it still matches.
        if len(fac) == 4 and fac.startswith("K") and fac[1] == "Z":
            cands.add(fac[1:])
    return cands


def _in_dc_region(notam: dict) -> bool:
    """True if this NOTAM is tied to a DC-region ARTCC (must-ingest)."""
    return bool(_artcc_candidates(notam) & DC_REGION_ARTCCS)


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


def _normalize_notam_number(number: str) -> str:
    """Strip leading zeros so the same NOTAM doesn't get two different IDs
    depending on how the source feed padded the number this delivery
    ("006" one time, "6" the next -- observed live for IIY/2026/006 vs
    IIY/2026/6, identical text and effective window, stored/alerted twice)."""
    number = (number or "").strip()
    if number.isdigit():
        return str(int(number))
    return number


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

            number = _normalize_notam_number(number)
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
                "location":        location,
                "fir":             fir,
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


_LAST_CLEANUP = [0.0]
_CLEANUP_INTERVAL_SECS = 600  # 10 minutes -- throttle so every message batch
                               # doesn't trigger a DELETE scan


def _maybe_cleanup_expired() -> None:
    now = time.time()
    if now - _LAST_CLEANUP[0] < _CLEANUP_INTERVAL_SECS:
        return
    _LAST_CLEANUP[0] = now
    try:
        removed = db.cleanup_expired_notams()
        if removed:
            log.info("aim: cleanup removed %d expired/stale NOTAM row(s)", removed)
    except Exception as e:
        log.warning("aim: cleanup_expired_notams failed: %s", e)


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
        # DC-region-ARTCC must-ingest applies to FDC only (Corey: "on the FDC
        # thing... anything within ZDC/ZNY/ZID/ZTL/ZOB must ingest"). ZID/ZTL/
        # ZOB/ZNY each cover a huge geographic area -- applying this to routine
        # NOTAM-D too would mean every airport-level NOTAM anywhere in the
        # Midwest/Northeast becomes a must-ingest+alert item, which is not
        # what was asked and is confirmed noisy in practice.
        in_dc_region = is_fdc and _in_dc_region(n)

        # Geo/significance filter on DB writes.
        # Always store: VIP NOTAMs (POTUS/VP/AF1/AF2/Marine One, nationwide),
        # any NOTAM (FDC or NOTAM-D) whose facility is in CORE_AIRPORTS or the
        # configured watch set, and -- for FDC only -- entries tied to a
        # DC-region ARTCC or that read as a major event/closure/airshow TFR
        # nationwide.
        in_watch = facility in watch_set
        in_core  = is_core_airport(facility)
        is_national_sig = is_fdc and _is_national_significant(n.get("text_body", ""))
        if not (is_vip or in_watch or in_core or in_dc_region or is_national_sig):
            log.debug("aim: geo-filtered NOTAM %s facility=%s (not DC-region FDC, watch set, or nationally significant)",
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

            # Alert routing: VIP always; DC-region and watch-set NOTAMs too.
            if is_vip or in_watch or in_dc_region:
                _fire_notam_alert(n)
            elif is_fdc:
                log.debug("aim: FDC NOTAM stored but not alerted (facility=%s not in watch set)", facility)

        except Exception as e:
            log.error("aim: db write error for %s: %s", n.get("notam_id"), e)

    _maybe_cleanup_expired()
    return written
