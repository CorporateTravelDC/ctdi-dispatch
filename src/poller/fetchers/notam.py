"""
FAA NOTAM fetcher — FAA NOTAM Search API v2
https://api.faa.gov/notamSearch/api/v1/notams

Requires FAA_NOTAM_API_KEY in dispatch-secrets.env.
Free registration at https://api.faa.gov

Fetches NOTAMs for DC-area airports and airspace.
Polled every 5 minutes by the poller scheduler.
Skips gracefully if no API key is configured.
"""

import hashlib
import json
import logging
import time
from datetime import datetime, timezone

import requests

from common import config, db

log = logging.getLogger(__name__)

FETCH_TIMEOUT = 15
NOTAM_URL = "https://api.faa.gov/notamSearch/api/v1/notams"

# DC-area airports and FDC location identifiers
DC_LOCATIONS = [
    "KDCA", "KIAD", "KBWI",        # Primary airports
    "KJYO", "KHEF", "KFDK",        # Secondary GA airports
    "KGAI", "W00",                  # Additional GA fields
    "ZDC",                          # Washington ARTCC (FDC NOTAMs)
]

PAGE_SIZE = 50


def _api_key() -> str | None:
    return config.get("FAA_NOTAM_API_KEY", "")


def _parse_notam_time(ts: str | None) -> float | None:
    if not ts:
        return None
    formats = [
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%S",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(ts, fmt).replace(
                tzinfo=timezone.utc).timestamp()
        except ValueError:
            continue
    return None


def fetch_notams_for_location(location: str, api_key: str) -> list[dict]:
    """Fetch current NOTAMs for a single location identifier."""
    params = {
        "icaoLocation": location,
        "pageSize": PAGE_SIZE,
        "pageNum": 0,
        "sortColumn": "issueDate",
        "sortOrder": "Desc",
    }
    headers = {
        "accept": "application/json",
        "client_id": api_key,
    }
    try:
        resp = requests.get(NOTAM_URL, params=params, headers=headers,
                            timeout=FETCH_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        return data.get("items", [])
    except Exception as e:
        log.warning("NOTAM fetch for %s failed: %s", location, e)
        return []


def run() -> dict:
    feed_name = "notam"
    fetched_at = time.time()

    api_key = _api_key()
    if not api_key:
        log.info("NOTAM: FAA_NOTAM_API_KEY not configured — marking awaiting_credentials")
        db.upsert_feed_skip(feed_name, fetched_at, "awaiting_credentials")
        return {"skipped": True, "reason": "awaiting_credentials"}

    try:
        all_notams: list[dict] = []
        seen_ids: set[str] = set()

        for location in DC_LOCATIONS:
            items = fetch_notams_for_location(location, api_key)
            for item in items:
                notam_id = item.get("coreNOTAMData", {}).get(
                    "notam", {}).get("id") or item.get("id", "")
                if not notam_id or notam_id in seen_ids:
                    continue
                seen_ids.add(notam_id)
                all_notams.append(item)

        for item in all_notams:
            core = item.get("coreNOTAMData", {}).get("notam", {})
            notam_id = core.get("id", "")
            facility = core.get("location", "")
            classification = core.get("classification", "")
            text_body = core.get("text", "") or item.get("icaoMessage", "")

            eff_start = _parse_notam_time(
                core.get("effectiveStart") or core.get("issueDate"))
            eff_end = _parse_notam_time(
                core.get("effectiveEnd") or core.get("estimatedEnd"))

            db.upsert_notam(
                notam_id=str(notam_id),
                raw_json=json.dumps(item),
                facility=facility,
                classification=classification,
                effective_start=eff_start,
                effective_end=eff_end,
                text_body=str(text_body)[:4000],
            )

        payload_hash = hashlib.sha256(
            json.dumps(sorted(seen_ids)).encode()
        ).hexdigest()[:16]

        db.upsert_feed(feed_name, fetched_at, error=None,
                       payload_hash=payload_hash)
        log.info("NOTAM fetch OK — %d NOTAMs across %d locations",
                 len(all_notams), len(DC_LOCATIONS))
        return {"count": len(all_notams), "locations": DC_LOCATIONS}

    except Exception as e:
        msg = str(e)
        log.error("NOTAM fetch FAILED: %s", msg)
        db.upsert_feed(feed_name, fetched_at, error=msg)
        return {"error": msg}
