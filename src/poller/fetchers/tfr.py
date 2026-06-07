"""
TFR fetcher — FAA TFR JSON list feed.
Endpoint: https://tfr.faa.gov/tfrapi/getTfrList  (JSON array, replaces the
broken tfr2/list.jsp XML feed which returns empty/malformed XML upstream).

Sample record shape (confirmed against live endpoint 2026-05-26):
  {"notam_id": "6/2998", "facility": "ZLC", "state": "ID",
   "type": "HAZARDS", "description": "38NM SE TWIN FALLS ...",
   "mod_date": "05/26/2026 18:08:00", "mod_abs_time": "202605261808",
   "is_new": "Y", "gid": "6/2998"}

Polled every 5 minutes by the poller scheduler.
VIP pattern match fires ntfy priority 5 via the pusher.
"""

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass

import requests

from common import db

log = logging.getLogger(__name__)

TFR_URL = "https://tfr.faa.gov/tfrapi/getTfrList"
FETCH_TIMEOUT = 15  # seconds

# ── VIP pattern matching ───────────────────────────────────────────────────────
VIP_PATTERNS = [
    re.compile(r"\bMARINE ONE\b", re.IGNORECASE),
    re.compile(r"\bPOTUS\b", re.IGNORECASE),
    re.compile(r"\bMOVEMENT OF\b", re.IGNORECASE),
    re.compile(r"\bSECURITY\b.*\bPRESIDENT\b", re.IGNORECASE),
    re.compile(r"\bPRESIDENTIAL\b", re.IGNORECASE),
    re.compile(r"\bAIR FORCE ONE\b", re.IGNORECASE),
    re.compile(r"\bSECURITY TFR\b", re.IGNORECASE),
]


def is_vip(text: str) -> bool:
    return any(p.search(text) for p in VIP_PATTERNS)


@dataclass
class TFRRecord:
    tfr_id: str
    raw_text: str
    raw_json: str
    vip: bool
    effective_start: float | None
    effective_end: float | None


def fetch() -> list[TFRRecord]:
    """
    Fetch and parse the FAA TFR JSON list. Returns a list of TFRRecord.
    Raises on HTTP/parse error — caller handles.
    """
    resp = requests.get(TFR_URL, timeout=FETCH_TIMEOUT,
                        headers={"User-Agent": "corporatetraveldc/1.0",
                                 "Accept": "application/json"})
    resp.raise_for_status()

    data = resp.json()
    if not isinstance(data, list):
        raise ValueError(f"TFR feed: expected JSON array, got {type(data).__name__}")

    records: list[TFRRecord] = []
    for item in data:
        tfr_id = str(item.get("notam_id") or item.get("gid") or "").strip()
        if not tfr_id:
            tfr_id = "hash-" + hashlib.sha1(
                json.dumps(item, sort_keys=True).encode()
            ).hexdigest()[:12]

        description = item.get("description", "")
        tfr_type = item.get("type", "")
        vip_text = f"{tfr_type} {description}"

        records.append(TFRRecord(
            tfr_id=tfr_id,
            raw_text=vip_text,
            raw_json=json.dumps(item),
            vip=is_vip(vip_text),
            effective_start=None,
            effective_end=None,
        ))

    return records


def run() -> dict:
    """
    Called by the poller scheduler every 5 minutes.
    Returns a summary dict for the feed state record.
    """
    feed_name = "tfr"
    fetched_at = time.time()
    new_vip_ids: list[str] = []

    try:
        records = fetch()
        payload_hash = hashlib.sha256(
            "".join(r.tfr_id for r in records).encode()
        ).hexdigest()[:16]

        for rec in records:
            db.upsert_tfr(
                tfr_id=rec.tfr_id,
                raw_json=rec.raw_json,
                is_vip=rec.vip,
                effective_start=rec.effective_start,
                effective_end=rec.effective_end,
            )
            if rec.vip:
                new_vip_ids.append(rec.tfr_id)

        db.upsert_feed(feed_name, fetched_at, error=None,
                       payload_hash=payload_hash)
        log.info("TFR fetch OK — %d TFRs, %d VIP", len(records), len(new_vip_ids))
        return {"count": len(records), "vip_ids": new_vip_ids}

    except Exception as e:
        msg = str(e)
        log.error("TFR fetch FAILED: %s", msg)
        db.upsert_feed(feed_name, fetched_at, error=msg)
        return {"error": msg}
