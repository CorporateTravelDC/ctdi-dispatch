"""
NWS fetcher — api.weather.gov
Fetches active hazardous weather alerts for DC/MD/VA and zone forecasts
for the primary DC-area aviation zones.
Polled every 5 minutes by the poller scheduler.
"""

import hashlib
import json
import logging
import time
from datetime import datetime, timezone

import requests

from common import db

log = logging.getLogger(__name__)

FETCH_TIMEOUT = 10
HEADERS = {
    "User-Agent": "corporatetraveldc/1.0 (dispatch@example.com)",
    "Accept": "application/geo+json",
}

# Active alerts for DC metro region — DC plus surrounding states
ALERTS_URL = "https://api.weather.gov/alerts/active?area=DC,MD,VA"

# Zone forecast URLs for DC-area aviation zones
FORECAST_ZONES = {
    "DC001": "https://api.weather.gov/zones/forecast/DCZ001/forecast",
    "MDZ014": "https://api.weather.gov/zones/forecast/MDZ014/forecast",  # Montgomery Co
    "VAZ036": "https://api.weather.gov/zones/forecast/VAZ036/forecast",  # Arlington/Alexandria
}

# Severity levels we care about — Minor omitted intentionally
ALERT_SEVERITY_FILTER = {"Extreme", "Severe", "Moderate"}

# Severities that trip the ingest backpressure valve (see
# ingest/swim_client.py's _bandwidth_priority_says_pause, added
# 2026-07-26). Deliberately narrower than ALERT_SEVERITY_FILTER --
# "Moderate" alone (e.g. a routine Wind Advisory) isn't worth pausing
# STDDS/TBFM/ITWS/NOTAM ingest over; Severe/Extreme is the bar for
# genuinely disruptive weather where FDPS/TFMS throughput matters more
# than the other four feeds.
WEATHER_PRIORITY_SEVERITIES = {"Extreme", "Severe"}

# TTL on the auto-set override -- self-heals if this fetcher stops
# running (poller crash/restart) instead of leaving ingest permanently
# throttled. 3x this fetcher's own 300s interval gives two missed
# cycles of slack before the override lapses on its own.
WEATHER_PRIORITY_TTL_SECONDS = 900


def _parse_iso(ts: str | None) -> float | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return None


def fetch_alerts() -> list[dict]:
    """Fetch active NWS alerts for DC/MD/VA. Returns list of alert dicts."""
    resp = requests.get(ALERTS_URL, timeout=FETCH_TIMEOUT, headers=HEADERS)
    resp.raise_for_status()
    data = resp.json()
    alerts = []
    for feature in data.get("features", []):
        props = feature.get("properties", {})
        severity = props.get("severity", "Unknown")
        if severity not in ALERT_SEVERITY_FILTER:
            continue
        alerts.append({
            "alert_id": props.get("id", feature.get("id", "")),
            "event_type": props.get("event", ""),
            "area_desc": props.get("areaDesc", ""),
            "severity": severity,
            "certainty": props.get("certainty", ""),
            "effective": _parse_iso(props.get("effective")),
            "expires": _parse_iso(props.get("expires")),
            "headline": props.get("headline", ""),
            "description": (props.get("description") or "")[:2000],
        })
    return alerts


def _maybe_set_weather_priority(alerts: list[dict]) -> None:
    """Auto-trigger the ingest backpressure valve (bandwidth_priority=weather)
    when a Severe/Extreme NWS alert is active for the DC region, and clear it
    back to auto when it isn't -- without ever overriding an operator's own
    manual setting.

    Never touches state that wasn't set by this same auto-trigger: reads the
    current state first and only acts when set_by is None, "auto", or
    "auto-weather" (i.e. nothing, or a prior run of this same function). A
    manually-set priority=nexrad (or a manual priority=weather) is left
    alone either way -- an operator's explicit call always wins.
    """
    try:
        current = db.get_bandwidth_priority()
    except Exception as e:
        log.debug("nws: weather-priority check skipped, DB read failed: %s", e)
        return

    set_by = current.get("set_by")
    if set_by not in (None, "auto", "auto-weather"):
        return  # operator has this set manually -- never override

    severe = [a for a in alerts if a.get("severity") in WEATHER_PRIORITY_SEVERITIES]

    if severe:
        # Refresh (or set) every run so the TTL keeps sliding forward while
        # the event is ongoing; top() by severity then soonest-expires so
        # the reason string reflects the most urgent active alert.
        top = sorted(
            severe,
            key=lambda a: (a.get("severity") != "Extreme", a.get("expires") or 0),
        )[0]
        try:
            db.set_bandwidth_priority(
                priority="weather", set_by="auto-weather",
                reason=f"{top.get('severity')} {top.get('event_type')}: {top.get('area_desc')}"[:200],
                ttl_seconds=WEATHER_PRIORITY_TTL_SECONDS,
            )
            log.info("nws: weather-priority ENGAGED (%s alert(s), top=%s)",
                     len(severe), top.get("event_type"))
        except Exception as e:
            log.warning("nws: failed to set weather priority: %s", e)
    elif current.get("priority") == "weather" and set_by in ("auto", "auto-weather"):
        try:
            db.set_bandwidth_priority(priority="auto", set_by="auto-weather",
                                      reason="no active Severe/Extreme alert")
            log.info("nws: weather-priority CLEARED -- no active Severe/Extreme alert")
        except Exception as e:
            log.warning("nws: failed to clear weather priority: %s", e)


def fetch_zone_forecast(zone: str, url: str) -> dict | None:
    """Fetch a single zone forecast. Returns raw JSON or None on error."""
    try:
        resp = requests.get(url, timeout=FETCH_TIMEOUT, headers=HEADERS)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log.warning("Zone forecast fetch failed for %s: %s", zone, e)
        return None


def run() -> dict:
    feed_name = "nws"
    fetched_at = time.time()

    try:
        # ── Alerts ────────────────────────────────────────────────────────────
        alerts = fetch_alerts()
        active_ids = []
        for a in alerts:
            db.upsert_nws_alert(
                alert_id=a["alert_id"],
                event_type=a["event_type"],
                area_desc=a["area_desc"],
                severity=a["severity"],
                certainty=a["certainty"],
                effective=a["effective"] or fetched_at,
                expires=a["expires"] or (fetched_at + 3600),
                headline=a["headline"],
                description=a["description"],
            )
            active_ids.append(a["alert_id"])

        db.expire_nws_alerts(active_ids)
        _maybe_set_weather_priority(alerts)

        # ── Zone forecasts ────────────────────────────────────────────────────
        for zone, url in FORECAST_ZONES.items():
            forecast = fetch_zone_forecast(zone, url)
            if forecast:
                with db.conn() as c:
                    c.execute("""
                        INSERT INTO nws_forecast (zone, forecast_json)
                        VALUES (?, ?)
                        ON CONFLICT(zone) DO UPDATE SET
                            forecast_json=excluded.forecast_json,
                            fetched_at=unixepoch()
                    """, (zone, json.dumps(forecast)))

        payload_hash = hashlib.sha256(
            json.dumps(sorted(active_ids)).encode()
        ).hexdigest()[:16]

        db.upsert_feed(feed_name, fetched_at, error=None,
                       payload_hash=payload_hash)
        log.info("NWS fetch OK — %d alerts (severity >= Moderate)", len(alerts))
        return {"alert_count": len(alerts), "alert_ids": active_ids}

    except Exception as e:
        msg = str(e)
        log.error("NWS fetch FAILED: %s", msg)
        db.upsert_feed(feed_name, fetched_at, error=msg)
        return {"error": msg}
