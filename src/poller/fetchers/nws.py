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
    "User-Agent": "corporatetraveldc/1.0 (dispatch@corporatetraveldc.com)",
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
