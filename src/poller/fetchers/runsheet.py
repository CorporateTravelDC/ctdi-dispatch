"""
Runsheet fetcher — daily operational picture.

Aggregates three sources into a single calendar-day runsheet:
  1. Scheduled trips (operator-populated JSON file — renamed from ops_plan)
  2. Active flight/train watchlist sessions (polled each cycle)
  3. Terminated watchlist sessions from the current day (already in DB)

Config:
  RUNSHEET_PATH in dispatch.env — defaults to state_dir/runsheet.json
  (Operator populates this with scheduled trips for the day)

Runsheet file format:
{
  "date": "2026-05-21",
  "trips": [
    {
      "id": "trip-001",
      "pickup_time": "14:30",
      "pickup_location": "IAD Terminal A",
      "dropoff_location": "Hay-Adams Hotel",
      "client_tier": "vip",
      "notes": "Optional notes"
    }
  ]
}

client_tier options: "standard" | "corporate" | "vip"
VIP tier triggers elevated alerting in downstream skills.

Polled every 5 minutes by the poller scheduler.
"""

import hashlib
import json
import logging
import time
from datetime import date as _date
from pathlib import Path

from common import config, db

log = logging.getLogger(__name__)

FEED_NAME = "runsheet"


def _runsheet_path() -> Path:
    custom = config.get("RUNSHEET_PATH", "")
    if custom:
        return Path(custom)
    return Path(config.state_dir()) / "runsheet.json"


def _load_scheduled_trips() -> tuple[str, list, str]:
    """
    Load scheduled trips from the operator's runsheet file.
    Returns (plan_date, trips, payload_hash).
    Returns today's date and empty list if file is absent or malformed.
    """
    today = _date.today().isoformat()
    path = _runsheet_path()

    if not path.exists():
        log.debug("Runsheet file not found at %s", path)
        return today, [], "no-file"

    try:
        raw = path.read_text()
        data = json.loads(raw)
        plan_date = data.get("date", today)
        trips = data.get("trips", [])
        payload_hash = hashlib.sha256(raw.encode()).hexdigest()[:16]
        return str(plan_date), trips, payload_hash
    except json.JSONDecodeError as e:
        log.warning("Runsheet JSON parse error: %s", e)
        return today, [], "parse-error"


def _poll_active_watchlists() -> list[dict]:
    """
    Return all currently active watchlist sessions.
    Called each cycle — the runsheet aggregates these for the API response.
    Note: actual flight/train data updates happen in their respective fetchers.
    """
    return db.get_active_watchlists()


def run() -> dict:
    fetched_at = time.time()
    today = _date.today().isoformat()

    try:
        plan_date, trips, payload_hash = _load_scheduled_trips()
        active = _poll_active_watchlists()
        terminated = db.get_terminated_watchlists(today)

        # Write/update the daily runsheet entry if trips changed
        existing = db.get_runsheet(plan_date)
        existing_hash = ""
        if existing and existing.get("scheduled_trips"):
            existing_hash = hashlib.sha256(
                existing["scheduled_trips"].encode()
            ).hexdigest()[:16]

        if payload_hash not in ("no-file", "parse-error") and \
                payload_hash != existing_hash:
            db.upsert_runsheet(plan_date, trips, len(trips))
            log.info("Runsheet updated — date=%s trips=%d", plan_date, len(trips))
        else:
            log.debug("Runsheet unchanged for %s", plan_date)

        db.upsert_feed(FEED_NAME, fetched_at, error=None,
                       payload_hash=payload_hash)
        return {
            "run_date": plan_date,
            "trip_count": len(trips),
            "active_watchlists": len(active),
            "terminated_today": len(terminated),
        }

    except Exception as e:
        msg = str(e)
        log.error("Runsheet fetch FAILED: %s", msg)
        db.upsert_feed(FEED_NAME, fetched_at, error=msg)
        return {"error": msg}
