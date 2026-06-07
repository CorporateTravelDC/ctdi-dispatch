"""
Amtrak fetcher — polls the local Amtrak tracker container.

The Amtrak tracker runs as a separate plain Podman container (not Quadlet)
due to its own operational constraints. This fetcher polls its local HTTP
endpoint and writes a status summary to the dispatch DB.

Config:
  AMTRAK_LOCAL_URL in dispatch.env — defaults to http://host.containers.internal:8898
  Set to empty string to disable: AMTRAK_LOCAL_URL=

Polled every 5 minutes by the poller scheduler.
Skips gracefully if container is unreachable.
"""

import hashlib
import json
import logging
import time

import requests

from common import config, db

log = logging.getLogger(__name__)

FETCH_TIMEOUT = 8

# DC Union Station is the primary hub — trains we care about:
# Northeast Regional, Acela, Carolinian, Palmetto, Silver Service,
# Capitol Limited, Cardinal, Vermonter, Pennsylvanian
DC_STATION_CODE = "WAS"


def _amtrak_url() -> str:
    return config.get("AMTRAK_LOCAL_URL", "http://host.containers.internal:8898")


def _disabled() -> bool:
    return _amtrak_url().strip() == ""


def fetch() -> dict:
    """
    Poll the local Amtrak container for DC-area train status.
    Returns the raw response dict from the container API.
    Raises on connection error.
    """
    base = _amtrak_url().rstrip("/")
    # Standard endpoint the Amtrak container exposes — adjust if container API differs
    url = f"{base}/api/trains/{DC_STATION_CODE}"
    resp = requests.get(url, timeout=FETCH_TIMEOUT,
                        headers={"User-Agent": "corporatetraveldc/1.0"})
    resp.raise_for_status()
    return resp.json()


def _summarize(data: dict) -> str:
    """Build a short human-readable delay summary from train data."""
    trains = data if isinstance(data, list) else data.get("trains", [])
    if not trains:
        return "No DC-area train data available."

    delayed = [t for t in trains
               if isinstance(t, dict) and int(t.get("delay_minutes", 0)) > 15]
    on_time = len(trains) - len(delayed)

    if not delayed:
        return f"All {len(trains)} DC-area trains on time."

    delay_lines = []
    for t in delayed[:5]:  # Cap at 5 for ntfy message length
        name = t.get("train_name") or t.get("train_number", "Unknown")
        mins = t.get("delay_minutes", 0)
        delay_lines.append(f"{name}: +{mins}min")

    summary = f"{len(delayed)} of {len(trains)} trains delayed. "
    summary += "; ".join(delay_lines)
    if len(delayed) > 5:
        summary += f" (+{len(delayed)-5} more)"
    return summary


def run() -> dict:
    feed_name = "amtrak"
    fetched_at = time.time()

    if _disabled():
        log.debug("Amtrak fetcher disabled (AMTRAK_LOCAL_URL is empty)")
        return {"skipped": True, "reason": "disabled"}

    try:
        data = fetch()
        trains = data if isinstance(data, list) else data.get("trains", [])
        summary = _summarize(data)

        payload_hash = hashlib.sha256(
            json.dumps(trains, sort_keys=True, default=str).encode()
        ).hexdigest()[:16]

        db.insert_amtrak_status(
            trains_json=json.dumps(trains),
            delay_summary=summary,
        )
        db.upsert_feed(feed_name, fetched_at, error=None,
                       payload_hash=payload_hash)
        log.info("Amtrak fetch OK — %d trains, summary: %s",
                 len(trains) if isinstance(trains, list) else "?", summary[:60])
        return {"train_count": len(trains) if isinstance(trains, list) else 0,
                "summary": summary}

    except requests.exceptions.ConnectionError:
        # Container not running — log at debug, not error (expected during dev)
        msg = "Amtrak container unreachable — skipping"
        log.debug(msg)
        db.upsert_feed(feed_name, fetched_at, error=msg)
        return {"skipped": True, "reason": "container unreachable"}

    except Exception as e:
        msg = str(e)
        log.error("Amtrak fetch FAILED: %s", msg)
        db.upsert_feed(feed_name, fetched_at, error=msg)
        return {"error": msg}
