"""
Ops plan fetcher — reads operator-populated scheduled trip file.

File location: /var/lib/corporatetraveldc/ops-plan.json
Polled every 5 minutes. If file hasn't changed (hash match), skips DB write.

File format (operator populates manually or via Nextcloud export):
{
  "date": "2026-05-21",
  "trips": [
    {
      "id": "trip-001",
      "pickup_time": "08:30",
      "pickup_location": "DCA Terminal B",
      "dropoff_location": "Pentagon City",
      "client_tier": "vip",
      "notes": "Client: [redacted]"
    }
  ]
}

Client tier options: "standard" | "vip" | "corporate"
VIP trips trigger elevated CPS threshold alerting in the route-impact skill.

Config:
  OPS_PLAN_PATH in dispatch.env — defaults to state_dir/ops-plan.json
"""

import hashlib
import json
import logging
import time
from pathlib import Path

from common import config, db

log = logging.getLogger(__name__)


def _plan_path() -> Path:
    custom = config.get("OPS_PLAN_PATH", "")
    if custom:
        return Path(custom)
    return Path(config.state_dir()) / "ops-plan.json"


def run() -> dict:
    feed_name = "ops_plan"
    fetched_at = time.time()
    plan_path = _plan_path()

    if not plan_path.exists():
        # Not an error — operator hasn't populated yet. Skip silently.
        log.debug("Ops plan file not found at %s — skipping", plan_path)
        db.upsert_feed(feed_name, fetched_at, error=None,
                       payload_hash="no-file")
        return {"skipped": True, "reason": "file not found"}

    try:
        raw = plan_path.read_text()
        payload_hash = hashlib.sha256(raw.encode()).hexdigest()[:16]

        # Check if content changed since last load
        with db.conn() as c:
            last = c.execute("""
                SELECT raw_json FROM ops_plan
                ORDER BY loaded_at DESC LIMIT 1
            """).fetchone()

        if last:
            last_hash = hashlib.sha256(last["raw_json"].encode()).hexdigest()[:16]
            if last_hash == payload_hash:
                log.debug("Ops plan unchanged — skipping DB write")
                db.upsert_feed(feed_name, fetched_at, error=None,
                               payload_hash=payload_hash)
                return {"unchanged": True}

        data = json.loads(raw)
        plan_date = data.get("date", "unknown")
        trips = data.get("trips", [])
        trip_count = len(trips)

        db.upsert_ops_plan(
            plan_date=str(plan_date),
            raw_json=raw,
            trip_count=trip_count,
        )
        db.upsert_feed(feed_name, fetched_at, error=None,
                       payload_hash=payload_hash)
        log.info("Ops plan loaded — date=%s, %d trips", plan_date, trip_count)
        return {"plan_date": plan_date, "trip_count": trip_count}

    except json.JSONDecodeError as e:
        msg = f"Ops plan JSON parse error: {e}"
        log.error(msg)
        db.upsert_feed(feed_name, fetched_at, error=msg)
        return {"error": msg}

    except Exception as e:
        msg = str(e)
        log.error("Ops plan fetch FAILED: %s", msg)
        db.upsert_feed(feed_name, fetched_at, error=msg)
        return {"error": msg}
