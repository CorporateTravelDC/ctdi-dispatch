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
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone

import requests

from common import db

log = logging.getLogger(__name__)

TFR_URL = "https://tfr.faa.gov/tfrapi/getTfrList"
FETCH_TIMEOUT = 15  # seconds

# 2026-10-14 (operator: VIP pushes showed "Active ? to ?"): the list feed carries no
# times -- a VIP entry's description is dates only ("Monroe, MI, Thursday, October 15,
# 2026 through ... Local") -- so effective_start/end were always NULL. The per-NOTAM
# detail record has them: Not/dateEffective + Not/dateExpire (the overall window) and
# TFRAreaGroup/aseTFRArea/ScheduleGroup (each area's window), all in UTC (confirmed:
# the FAA's own text for 6/9607 reads "1715 UTC (1315 EDT)" for dateEffective
# 2026-10-15T17:15:00). Fetched for VIP TFRs only, once per (NOTAM, FAA mod time).
TFR_DETAIL_URL = "https://tfr.faa.gov/download/detail_{}.xml"
DETAIL_TIMEOUT = 10  # seconds
_detail_cache: dict[str, tuple[str, tuple]] = {}   # notam_id -> (mod_abs_time, parsed window)

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


def is_vip(text: str, tfr_type: str | None = None) -> bool:
    """2026-10-13 (ntfy audit): the FAA feed marks VIP TFRs with type "VIP" and a
    plain place/date description ("Anchorage, AK, Tuesday, October 13, ..."), so the
    text patterns alone never matched one: all 69 rows were is_vip=0, including 9 active
    VIP TFRs, and tfr-alert never fired. The FAA's own type field decides first."""
    if (tfr_type or "").strip().upper() == "VIP":
        return True
    return any(p.search(text) for p in VIP_PATTERNS)


@dataclass
class TFRRecord:
    tfr_id: str
    raw_text: str
    raw_json: str
    vip: bool
    effective_start: float | None
    effective_end: float | None


def _utc_epoch(text: str | None) -> float | None:
    """'2026-10-15T17:15:00' (FAA detail, UTC, no zone suffix) -> epoch seconds."""
    if not text or not text.strip():
        return None
    try:
        dt = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def parse_detail_window(xml_text: str) -> tuple[float | None, float | None, list[list[float]]]:
    """(start, end, area windows) from a TFR detail XML (XNOTAM-Update). The overall
    window is Not/dateEffective..Not/dateExpire; area windows come from each
    ScheduleGroup (deduplicated, sorted). Missing pieces come back None / []."""
    root = ET.fromstring(xml_text.lstrip("\ufeff").encode("utf-8"))
    nots = root.findall(".//Not")
    if not nots:
        return None, None, []
    n = nots[0]
    start = _utc_epoch(n.findtext("dateEffective"))
    end = _utc_epoch(n.findtext("dateExpire"))
    windows = set()
    for sg in n.iter("ScheduleGroup"):
        s, e = _utc_epoch(sg.findtext("dateEffective")), _utc_epoch(sg.findtext("dateExpire"))
        if s is not None and e is not None:
            windows.add((s, e))
    return start, end, [list(w) for w in sorted(windows)]


def fetch_detail_window(notam_id: str, mod: str) -> tuple[float | None, float | None, list[list[float]]]:
    """Window for one TFR, from cache when the FAA mod time is unchanged. A fetch or
    parse failure returns (None, None, []) and is not cached, so the next poll retries."""
    hit = _detail_cache.get(notam_id)
    if hit and hit[0] == mod:
        return hit[1]
    try:
        resp = requests.get(TFR_DETAIL_URL.format(notam_id.replace("/", "_")), timeout=DETAIL_TIMEOUT,
                            headers={"User-Agent": "corporatetraveldc/1.0", "Accept": "application/xml"})
        resp.raise_for_status()
        window = parse_detail_window(resp.content.decode("utf-8-sig", "replace"))
    except Exception as e:
        log.warning("TFR detail %s: window unavailable (%s)", notam_id, e)
        return None, None, []
    _detail_cache[notam_id] = (mod, window)
    return window


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
        vip = is_vip(vip_text, tfr_type)

        start = end = None
        if vip and item.get("notam_id"):
            start, end, windows = fetch_detail_window(
                str(item["notam_id"]), str(item.get("mod_abs_time") or item.get("mod_date") or ""))
            if windows:
                item = {**item, "area_windows_utc": windows}

        records.append(TFRRecord(
            tfr_id=tfr_id,
            raw_text=vip_text,
            raw_json=json.dumps(item),
            vip=vip,
            effective_start=start,
            effective_end=end,
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

        # Sweep TFRs that are no longer in the FAA feed.
        # The getTfrList endpoint returns the full current active list, so any
        # ID we have in the DB that is absent from this response is expired.
        active_ids = [r.tfr_id for r in records]
        db.expire_tfrs(active_ids)

        db.upsert_feed(feed_name, fetched_at, error=None,
                       payload_hash=payload_hash)
        log.info("TFR fetch OK — %d TFRs, %d VIP", len(records), len(new_vip_ids))
        return {"count": len(records), "vip_ids": new_vip_ids}

    except Exception as e:
        msg = str(e)
        log.error("TFR fetch FAILED: %s", msg)
        db.upsert_feed(feed_name, fetched_at, error=msg)
        return {"error": msg}
