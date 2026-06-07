"""
SSE event generator for /api/v1/events.

Emits typed events by polling SQLite every 3 seconds and diffing against
prior state. DB calls run in the thread executor (sqlite3 is sync).

Event types:
  snapshot  — full state on connect (PWA initial hydration)
  cps       — CPS score/label changed
  tfr       — TFR set changed (added or removed)
  weather   — any METAR refreshed
  alert     — NWS alert set changed
  feeds     — any feed error-state changed
  heartbeat — keepalive every 30 s
"""

import asyncio
import json
import time
from typing import AsyncGenerator

from fastapi import Request

from common import db


def _build_snapshot() -> dict:
    cps = db.get_latest_cps()
    tfrs = db.get_active_tfrs()
    metars = db.get_metar_snapshot()
    alerts = db.get_active_nws_alerts()
    feeds = db.get_feed_states()
    now = time.time()
    return {
        "ts": now,
        "cps": _serialize_cps(cps),
        "tfrs": [_serialize_tfr(t) for t in tfrs],
        "metars": [_serialize_metar(m) for m in metars],
        "alerts": [_serialize_alert(a) for a in alerts],
        "feeds": [_serialize_feed(f, now) for f in feeds],
    }


def _serialize_cps(cps: dict | None) -> dict | None:
    if not cps:
        return None
    return {
        "score": cps["score"],
        "label": cps["label"],
        "factors": {
            "ceiling": cps["ceiling_factor"],
            "visibility": cps["visibility_factor"],
            "wind": cps["wind_factor"],
            "precip": cps["precip_factor"],
            "airspace": cps["airspace_factor"],
            "gdp": cps["gdp_factor"],
        },
        "narrative": cps["narrative"],
        "computed_at": cps["computed_at"],
    }


def _serialize_tfr(t: dict) -> dict:
    return {
        "tfr_id": t["tfr_id"],
        "is_vip": bool(t["is_vip"]),
        "effective_start": t["effective_start"],
        "effective_end": t["effective_end"],
    }


def _serialize_metar(m: dict) -> dict:
    return {
        "station": m["station"],
        "ceiling_ft": m["ceiling_ft"],
        "visibility_sm": m["visibility_sm"],
        "wind_kt": m["wind_kt"],
        "precip_code": m["precip_code"],
        "obs_time": m["obs_time"],
        "fetched_at": m["fetched_at"],
    }


def _serialize_alert(a: dict) -> dict:
    return {
        "alert_id": a["alert_id"],
        "event_type": a["event_type"],
        "severity": a["severity"],
        "certainty": a["certainty"],
        "effective": a["effective"],
        "expires": a["expires"],
        "headline": a["headline"],
    }


def _serialize_feed(f: dict, now: float) -> dict:
    age = int(now - f["fetched_at"]) if f["fetched_at"] else None
    return {
        "feed_name": f["feed_name"],
        "age_seconds": age,
        "error": f["error"],
        "consecutive_failures": f["consecutive_failures"],
    }


async def live_events(request: Request) -> AsyncGenerator[dict, None]:
    loop = asyncio.get_running_loop()

    snap = await loop.run_in_executor(None, _build_snapshot)
    yield {"event": "snapshot", "data": json.dumps(snap)}

    last_cps_time = snap["cps"]["computed_at"] if snap["cps"] else None
    last_tfr_ids = {t["tfr_id"] for t in snap["tfrs"]}
    last_alert_ids = {a["alert_id"] for a in snap["alerts"]}
    last_metar_time = max((m["fetched_at"] or 0) for m in snap["metars"]) if snap["metars"] else 0
    last_feed_errors = {f["feed_name"]: f["error"] for f in snap["feeds"]}
    last_heartbeat = time.time()

    while True:
        if await request.is_disconnected():
            break

        await asyncio.sleep(3)
        now = time.time()

        cps = await loop.run_in_executor(None, db.get_latest_cps)
        if cps and cps["computed_at"] != last_cps_time:
            last_cps_time = cps["computed_at"]
            yield {"event": "cps", "data": json.dumps(_serialize_cps(cps))}

        tfrs = await loop.run_in_executor(None, db.get_active_tfrs)
        tfr_ids = {t["tfr_id"] for t in tfrs}
        if tfr_ids != last_tfr_ids:
            added = list(tfr_ids - last_tfr_ids)
            removed = list(last_tfr_ids - tfr_ids)
            last_tfr_ids = tfr_ids
            yield {"event": "tfr", "data": json.dumps({
                "active": [_serialize_tfr(t) for t in tfrs],
                "added": added,
                "removed": removed,
            })}

        alerts = await loop.run_in_executor(None, db.get_active_nws_alerts)
        alert_ids = {a["alert_id"] for a in alerts}
        if alert_ids != last_alert_ids:
            added = list(alert_ids - last_alert_ids)
            removed = list(last_alert_ids - alert_ids)
            last_alert_ids = alert_ids
            yield {"event": "alert", "data": json.dumps({
                "active": [_serialize_alert(a) for a in alerts],
                "added": added,
                "removed": removed,
            })}

        metars = await loop.run_in_executor(None, db.get_metar_snapshot)
        metar_time = max((m["fetched_at"] or 0) for m in metars) if metars else 0
        if metar_time != last_metar_time:
            last_metar_time = metar_time
            yield {"event": "weather", "data": json.dumps({
                "metars": [_serialize_metar(m) for m in metars],
            })}

        feeds = await loop.run_in_executor(None, db.get_feed_states)
        feed_errors = {f["feed_name"]: f["error"] for f in feeds}
        if feed_errors != last_feed_errors:
            last_feed_errors = feed_errors
            yield {"event": "feeds", "data": json.dumps({
                "feeds": [_serialize_feed(f, now) for f in feeds],
            })}

        if now - last_heartbeat >= 30:
            last_heartbeat = now
            yield {"event": "heartbeat", "data": json.dumps({"ts": now})}
