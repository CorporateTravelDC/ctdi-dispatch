"""
ingest.parsers.itws_parser — FAA ITWS (Integrated Terminal Weather System) NMS parser.

ITWS delivers processed terminal weather products for DCA, IAD, and BWI:
  - PRECIP     : precipitation type, rate, forecast (0-60 min)
  - WIND_SHEAR : wind shear alerts at runway thresholds
  - MICROBURST : microburst alerts (short-duration wind shear, high severity)
  - LIGHTNING  : lightning strike counts and proximity alerts
  - CEILING    : terminal ceiling and visibility reports (METARs already cover this
                 but ITWS adds forecast confidence)

ITWS data is NOT available via REST — NMS is the only source. It augments rather
than replaces the METAR REST feed. Heartbeat key: "itws".

Severity scale (FAA ITWS convention):
  1-2 = light, 3-4 = moderate, 5-6 = severe
"""
from __future__ import annotations

import json
import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from common import db
from shared.watchlist import _fire_ntfy_dual  # reuse ntfy infra for ITWS alerts

log = logging.getLogger("ingest.parsers.itws")

_ITWS_NS = {
    "itws": "http://www.faa.aero/itws/1.0",
    "wx":   "http://www.faa.aero/wx/1.0",
}

ITWS_AIRPORTS = frozenset({"KDCA", "KIAD", "KBWI"})

# Severity threshold above which we fire an ntfy alert
ITWS_ALERT_SEVERITY = 4

_PRODUCT_TYPES = frozenset({
    "PRECIP", "WIND_SHEAR", "MICROBURST", "LIGHTNING", "CEILING",
    # Aliases in some ITWS schemas
    "PRECIPITATION", "WINDSHEAR", "MICRO_BURST",
})

_PRODUCT_CANONICAL = {
    "PRECIPITATION": "PRECIP",
    "WINDSHEAR": "WIND_SHEAR",
    "MICRO_BURST": "MICROBURST",
}


def _txt(elem: ET.Element | None, *tags: str) -> str | None:
    cur = elem
    for tag in tags:
        if cur is None:
            return None
        found = cur.find(tag)
        if found is None:
            for uri in _ITWS_NS.values():
                found = cur.find(f"{{{uri}}}{tag}")
                if found is not None:
                    break
        cur = found
    return (cur.text or "").strip() or None if cur is not None else None


def _parse_time(ts: str | None) -> str | None:
    if not ts:
        return None
    ts = ts.strip()
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return ts


def parse_itws_message(xml_bytes: bytes) -> list[dict]:
    """
    Parse an ITWS NMS XML message.
    Returns list of alert dicts: {airport, product_type, severity, detail,
    valid_time, expires_time, raw_json}.
    Filtered to KDCA/KIAD/KBWI only.
    """
    if not xml_bytes:
        return []
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        log.warning("itws: XML parse error: %s", e)
        return []

    alerts: list[dict] = []
    raw_xml = xml_bytes.decode("utf-8", errors="replace")

    _ALERT_TAGS = {
        "itwsProduct", "weatherAlert", "terminalWeather",
        "precipAlert", "windShearAlert", "microburstAlert", "lightningAlert",
        "itwsAlert", "itwsData",
    }

    for elem in root.iter():
        local = elem.tag.split("}")[-1]
        if local in _ALERT_TAGS:
            alert = _parse_single_alert(elem, raw_xml)
            if alert and alert["airport"] in ITWS_AIRPORTS:
                alerts.append(alert)

    if not alerts:
        # Try root itself
        alert = _parse_single_alert(root, raw_xml)
        if alert and alert["airport"] in ITWS_AIRPORTS:
            alerts.append(alert)

    if not alerts and xml_bytes:
        log.debug("itws: no alerts parsed; raw prefix: %s",
                  xml_bytes[:300].decode("utf-8", errors="replace"))

    return alerts


def _parse_single_alert(elem: ET.Element, raw_xml: str) -> dict | None:
    airport = (
        _txt(elem, "airport") or
        _txt(elem, "facility") or
        _txt(elem, "icao")
    )
    if not airport:
        return None

    # Normalise airport — add K prefix if needed
    airport = airport.upper()
    if len(airport) == 3:
        airport = "K" + airport

    raw_type = (
        _txt(elem, "productType") or
        _txt(elem, "alertType") or
        _txt(elem, "type") or
        elem.tag.split("}")[-1].upper()
    )
    product_type = _PRODUCT_CANONICAL.get(
        (raw_type or "").upper(), (raw_type or "UNKNOWN").upper()
    )

    sev_raw = _txt(elem, "severity") or _txt(elem, "level") or _txt(elem, "intensity")
    severity: int | None = None
    if sev_raw:
        try:
            severity = int(float(sev_raw))
        except (ValueError, TypeError):
            pass

    detail = (
        _txt(elem, "detail") or
        _txt(elem, "description") or
        _txt(elem, "alertText") or
        _txt(elem, "text")
    )

    valid_time = _parse_time(
        _txt(elem, "validTime") or _txt(elem, "startTime") or _txt(elem, "issueTime")
    ) or datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    expires_time = _parse_time(
        _txt(elem, "expiresTime") or _txt(elem, "endTime") or _txt(elem, "expireTime")
    )

    payload = {
        "airport": airport,
        "product_type": product_type,
        "severity": severity,
        "detail": detail,
        "valid_time": valid_time,
        "expires_time": expires_time,
        "source": "swim_itws",
    }

    return {**payload, "raw_json": json.dumps(payload)}


def write_itws_alerts(alerts: list[dict]) -> int:
    """Upsert ITWS alerts into itws_alerts table. Returns count written."""
    written = 0
    for a in alerts:
        try:
            db.upsert_itws_alert(
                airport=a["airport"],
                product_type=a["product_type"],
                severity=a.get("severity"),
                detail=a.get("detail"),
                valid_time=a["valid_time"],
                expires_time=a.get("expires_time"),
                raw_json=a["raw_json"],
            )
            written += 1
        except Exception as e:
            log.error("itws: db write error for %s/%s: %s",
                      a.get("airport"), a.get("product_type"), e)
    return written


def check_itws_alerts(alerts: list[dict]) -> None:
    """Fire ntfy for high-severity ITWS alerts (severity >= 4)."""
    for a in alerts:
        sev = a.get("severity") or 0
        if sev < ITWS_ALERT_SEVERITY:
            continue
        airport = a["airport"]
        product_type = a["product_type"]
        detail = a.get("detail") or product_type
        title = f"ITWS {product_type} — {airport} (sev {sev})"
        dispatch = f"{airport}: {product_type} severity {sev}"
        try:
            _fire_ntfy_dual("wx-alerts", title, detail, dispatch, priority=4)
        except Exception as e:
            log.error("itws: ntfy error for %s/%s: %s", airport, product_type, e)
