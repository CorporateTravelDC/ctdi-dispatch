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
VIP-class TFRs (tfrs.vip_push_reason non-NULL; 2026-10-18 widening: VIP type, plus
SECURITY type with presidential language, the 40103(b)(3)/99.7 legal basis, or a ZDC
facility) fire ntfy priority 5 via the pusher.
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

from common import db, tfr_geo

log = logging.getLogger(__name__)

TFR_URL = "https://tfr.faa.gov/tfrapi/getTfrList"
FETCH_TIMEOUT = 15  # seconds

# 2026-10-14 (operator: VIP pushes showed "Active ? to ?"): the list feed carries no
# times -- a VIP entry's description is dates only ("Monroe, MI, Thursday, October 15,
# 2026 through ... Local") -- so effective_start/end were always NULL. The per-NOTAM
# detail record has them: Not/dateEffective + Not/dateExpire (the overall window) and
# TFRAreaGroup/aseTFRArea/ScheduleGroup (each area's window), all in UTC (confirmed:
# the FAA's own text for 6/9607 reads "1715 UTC (1315 EDT)" for dateEffective
# 2026-10-15T17:15:00). Fetched once per (NOTAM, FAA mod time).
# 2026-10-18 (operator: TFR widening): fetched for every VIP AND every SECURITY type
# TFR (was VIP only: 6/0100 Palm Beach, type SECURITY, 'NTL DEFENSE AIRSPACE', was
# stored with no window and never pushed). The same record now also yields the area
# geometry (common.tfr_geo), the full FAA text (txtDescrTraditional) and the
# structured legal basis (TfrNot/codeType, e.g. "99.7").
TFR_DETAIL_URL = "https://tfr.faa.gov/download/detail_{}.xml"
DETAIL_TIMEOUT = 10  # seconds
_detail_cache: dict[str, tuple[str, dict]] = {}   # notam_id -> (mod_abs_time, parsed detail)

# Types that get the detail-record treatment (window + geometry + text).
DETAIL_TYPES = frozenset({"VIP", "SECURITY"})
CLASS_VERSION = "2026-10-18"

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

# 2026-10-18: boilerplate in the UAS paragraph of nearly every 99.7 NOTAM (seen on
# 6/0100 Palm Beach and 6/0096 New York): "PURSUANT TO 10 U.S.C. SECTION 130I AND THE
# PRESIDENTIAL THREAT PROTECTION ACT ...". It names a statute, not a protectee, so it is
# removed before the presidential patterns run on detail text -- otherwise every
# SECURITY TFR would read as "presidential".
_PRESIDENTIAL_BOILERPLATE = re.compile(r"PRESIDENTIAL\s+THREAT\s+PROTECTION\s+ACT", re.IGNORECASE)

# 2026-10-18 (operator): the legal basis that makes a SECURITY TFR VIP-class anywhere.
# TfrNot/codeType "99.7" (the detail record's structured form of the same citation)
# counts too. Live check 2026-10-18 18:46Z: every sampled SECURITY TFR (10 of the 41 in
# the list) carried 49 USC 40103(b)(3) + 'NTL DEFENSE AIRSPACE' + 14 CFR 99.7 -- so in
# practice almost every new SECURITY TFR is VIP-class under this rule.
LEGAL_BASIS_PATTERNS = [
    re.compile(r"\b49\s*U\.?S\.?C\.?\s*(?:SECTION\s*)?40103\s*\(\s*B\s*\)\s*\(\s*3\s*\)", re.IGNORECASE),
    re.compile(r"\bN(?:TL|ATIONAL)\s+DEFENSE\s+AIRSPACE\b", re.IGNORECASE),
    re.compile(r"\b14\s*CFR\s*(?:SECTION\s*|PART\s*)?99\.7\b", re.IGNORECASE),
    re.compile(r"\b99\.7\s*,?\s*SPECIAL\s+SECURITY\s+INSTRUCTIONS\b", re.IGNORECASE),
]
LEGAL_BASIS_CODE_TYPES = frozenset({"99.7"})

# 2026-10-18 (operator, superseding the same day's 30 nm DCA/IAD/BWI radius): inside
# Washington Center (ZDC) EVERY SECURITY TFR -- stadiums and events included -- is
# VIP-class, because of its effect on DCA/IAD/BWI. Decided by the TFR's own FAA
# facility field first (the list item's "facility", else the detail's codeFacility).
VIP_CLASS_SECURITY_FACILITIES = frozenset({"ZDC"})
_ARTCC_RE = re.compile(r"^K?(Z[A-Z]{2})$")
# Fallbacks, used ONLY when the record carries no ARTCC facility (nationwide FDC
# NOTAMs list facility "FDC"; one detail read "Not"). There is no ZDC boundary
# polygon in this repo and none is fetched: ZDC_APPROX_BOX is a deliberately coarse,
# over-inclusive lat/lon box around Washington Center (southern PA border to the NC/SC
# coast, the Blue Ridge to the Atlantic warning areas), so a geometry fallback can
# include edge areas that are really ZNY/ZOB/ZID/ZTL/ZJX. With no geometry either, the
# state/city text decides (DC, MD, VA, DE -- NC is split with ZTL, so it is left out).
ZDC_APPROX_BOX = (34.0, 40.2, -81.5, -73.5)   # (lat_min, lat_max, lon_min, lon_max)
ZDC_FALLBACK_STATES = frozenset({"DC", "MD", "VA", "DE", "DISTRICT OF COLUMBIA",
                                 "MARYLAND", "VIRGINIA", "DELAWARE"})
_ZDC_TEXT_RE = re.compile(r"\bWASHINGTON,?\s*D\.?C\b", re.IGNORECASE)

# 2026-10-18 (operator, tiered push): protected residences. A SECURITY TFR at one of
# these is VIP-class (p5) wherever it is. THE OPERATOR MAINTAINS THIS LIST -- add or
# remove entries here; nothing looks them up anywhere. A TFR matches when the FAA
# detail record's city (AffLocGroup/txtNameCity) equals `city` and its state is one of
# `states`, or the list description starts with "<city>, <state code>", or (where the
# detail record has geometry) any area centre lies within radius_nm of (lat, lon).
# Coordinates are approximate property centres, to ~0.5 nm.
PROTECTED_RESIDENCES = (
    {"name": "Palm Beach, FL", "city": "PALM BEACH", "states": ("FL", "FLORIDA"),
     "lat": 26.677, "lon": -80.037, "radius_nm": 3.0},
    {"name": "Bedminster, NJ", "city": "BEDMINSTER", "states": ("NJ", "NEW JERSEY"),
     "lat": 40.645, "lon": -74.665, "radius_nm": 3.0},
)

# Push reason codes, stored on tfrs.vip_push_reason (migration 0081). The first that
# applies is stored; raw_json["vip_push_reasons"] keeps every one that applies.
# NULL = no push. is_vip keeps its old meaning (FAA type VIP or a VIP_PATTERNS hit on
# the list text) for every other reader (briefs, route impact).
# 2026-10-18 (operator): the push is TIERED. HOT_REASONS = VIP-class (p5 tfr-alert +
# hot-alerts + Pushover Emergency). A TFR whose only reason is security_legal_basis
# (SECURITY outside ZDC, not presidential, not a protected residence) gets a plain
# p3 tfr-alert push: no hot-alerts, no Pushover.
REASON_ORDER = ("vip_type", "vip_text", "security_presidential", "security_residence",
                "security_zdc", "security_legal_basis")
HOT_REASONS = frozenset({"vip_type", "vip_text", "security_presidential",
                         "security_residence", "security_zdc"})
REASON_WORDS = {
    "vip_type": "FAA type VIP (VIP movement)",
    "vip_text": "presidential/VIP language in the FAA text",
    "security_presidential": "SECURITY TFR with presidential language in the FAA text",
    "security_residence": "SECURITY TFR at a protected residence",
    "security_zdc": "SECURITY TFR in Washington Center (ZDC) airspace -- affects DCA/IAD/BWI",
    "security_legal_basis": "SECURITY TFR under 49 USC 40103(b)(3) / 14 CFR 99.7 (national defense airspace)",
}


def is_vip(text: str, tfr_type: str | None = None) -> bool:
    """2026-10-13 (ntfy audit): the FAA feed marks VIP TFRs with type "VIP" and a
    plain place/date description ("Anchorage, AK, Tuesday, October 13, ..."), so the
    text patterns alone never matched one: all 69 rows were is_vip=0, including 9 active
    VIP TFRs, and tfr-alert never fired. The FAA's own type field decides first."""
    if (tfr_type or "").strip().upper() == "VIP":
        return True
    return any(p.search(text) for p in VIP_PATTERNS)


def _presidential(text: str) -> bool:
    return any(p.search(_PRESIDENTIAL_BOILERPLATE.sub(" ", text or "")) for p in VIP_PATTERNS)


def has_legal_basis(text: str, code_type: str | None = None) -> bool:
    if (code_type or "").strip() in LEGAL_BASIS_CODE_TYPES:
        return True
    return any(p.search(text or "") for p in LEGAL_BASIS_PATTERNS)


def zdc_basis(item: dict, detail: dict | None) -> str | None:
    """How a TFR was placed in ZDC: 'facility' | 'geometry_approx' | 'text', or None.
    An ARTCC facility on the record decides alone (no fallback when it names another
    centre); geometry and text are consulted only when there is no ARTCC facility."""
    detail = detail or {}
    for fac in (item.get("facility"), detail.get("facility")):
        m = _ARTCC_RE.match((fac or "").strip().upper())
        if m:
            return "facility" if m.group(1) in VIP_CLASS_SECURITY_FACILITIES else None
    areas = detail.get("areas_geo") or []
    if areas:
        lat0, lat1, lon0, lon1 = ZDC_APPROX_BOX
        hit = any(lat0 <= a["lat"] <= lat1 and lon0 <= a["lon"] <= lon1 for a in areas)
        return "geometry_approx" if hit else None
    states = {(item.get("state") or "").strip().upper(), (detail.get("state") or "").strip().upper()}
    if states & ZDC_FALLBACK_STATES or _ZDC_TEXT_RE.search(
            f"{item.get('description') or ''} {detail.get('city') or ''}"):
        return "text"
    return None


def protected_residence(item: dict, detail: dict | None) -> str | None:
    """Name of the PROTECTED_RESIDENCES entry this TFR is at, or None."""
    detail = detail or {}
    city = (detail.get("city") or "").strip().upper()
    state = (detail.get("state") or item.get("state") or "").strip().upper()
    desc = " ".join(str(item.get("description") or "").upper().split())
    for r in PROTECTED_RESIDENCES:
        if city == r["city"] and state in r["states"]:
            return r["name"]
        if any(desc.startswith(f"{r['city']}, {st}") for st in r["states"] if len(st) == 2):
            return r["name"]
        for a in detail.get("areas_geo") or []:
            try:
                if tfr_geo.haversine_nm(float(a["lat"]), float(a["lon"]), r["lat"], r["lon"]) <= r["radius_nm"]:
                    return r["name"]
            except (KeyError, TypeError, ValueError):
                continue
    return None


def push_tier(reasons) -> str | None:
    """'hot' (VIP-class p5), 'p3' (legal basis only), or None (no push)."""
    reasons = set(reasons or ())
    if reasons & HOT_REASONS:
        return "hot"
    if "security_legal_basis" in reasons:
        return "p3"
    return None


def classify(item: dict, detail: dict | None) -> tuple[bool, list[str], str | None]:
    """(is_vip, push reasons in REASON_ORDER, zdc basis) for one list item + its
    parsed detail (None when not fetched or the fetch failed -- then only the list
    text and facility are known, and the next poll retries the detail)."""
    tfr_type = (item.get("type") or "").strip().upper()
    description = item.get("description") or ""
    vip = is_vip(f"{tfr_type} {description}", tfr_type)
    reasons: set[str] = set()
    zdc = None
    if tfr_type == "VIP":
        reasons.add("vip_type")
    elif tfr_type == "SECURITY":
        full = f"{description}\n{(detail or {}).get('text') or ''}"
        if vip or _presidential(full):
            reasons.add("security_presidential")
        if protected_residence(item, detail):
            reasons.add("security_residence")
        zdc = zdc_basis(item, detail)
        if zdc:
            reasons.add("security_zdc")
        if has_legal_basis(full, (detail or {}).get("code_type")):
            reasons.add("security_legal_basis")
    elif vip:
        reasons.add("vip_text")       # pre-10-18 behaviour: a VIP_PATTERNS hit pushed
    return vip, [r for r in REASON_ORDER if r in reasons], zdc


@dataclass
class TFRRecord:
    tfr_id: str
    raw_text: str
    raw_json: str
    vip: bool
    effective_start: float | None
    effective_end: float | None
    vip_push_reason: str | None = None


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


def parse_detail(xml_text: str) -> dict:
    """Everything used from a TFR detail XML (XNOTAM-Update): start/end (Not/
    dateEffective..dateExpire, UTC), area windows (each ScheduleGroup, deduplicated,
    sorted), areas_geo (common.tfr_geo), text (txtDescrTraditional, else USNS),
    code_type (TfrNot/codeType), facility, city, state. Missing pieces are None/[]."""
    root = ET.fromstring(xml_text.lstrip("\ufeff").encode("utf-8"))
    nots = root.findall(".//Not")
    out = {"start": None, "end": None, "windows": [], "areas_geo": [], "text": "",
           "code_type": None, "facility": None, "city": None, "state": None}
    if not nots:
        return out
    n = nots[0]
    out["start"] = _utc_epoch(n.findtext("dateEffective"))
    out["end"] = _utc_epoch(n.findtext("dateExpire"))
    windows = set()
    for sg in n.iter("ScheduleGroup"):
        s, e = _utc_epoch(sg.findtext("dateEffective")), _utc_epoch(sg.findtext("dateExpire"))
        if s is not None and e is not None:
            windows.add((s, e))
    out["windows"] = [list(w) for w in sorted(windows)]
    out["areas_geo"] = tfr_geo.parse_areas(n)
    out["text"] = (n.findtext("txtDescrTraditional") or n.findtext("txtDescrUSNS") or "").strip()
    out["code_type"] = (n.findtext("TfrNot/codeType") or "").strip() or None
    out["facility"] = (n.findtext("codeFacility") or "").strip() or None
    out["city"] = (n.findtext("AffLocGroup/txtNameCity") or "").strip() or None
    out["state"] = (n.findtext("AffLocGroup/txtNameUSState") or "").strip() or None
    return out


def parse_detail_window(xml_text: str) -> tuple[float | None, float | None, list[list[float]]]:
    """(start, end, area windows) from a TFR detail XML -- see parse_detail()."""
    d = parse_detail(xml_text)
    return d["start"], d["end"], d["windows"]


def fetch_detail(notam_id: str, mod: str) -> dict | None:
    """Parsed detail for one TFR, from cache when the FAA mod time is unchanged. A
    fetch or parse failure returns None and is not cached, so the next poll retries."""
    hit = _detail_cache.get(notam_id)
    if hit and hit[0] == mod:
        return hit[1]
    try:
        resp = requests.get(TFR_DETAIL_URL.format(notam_id.replace("/", "_")), timeout=DETAIL_TIMEOUT,
                            headers={"User-Agent": "corporatetraveldc/1.0", "Accept": "application/xml"})
        resp.raise_for_status()
        detail = parse_detail(resp.content.decode("utf-8-sig", "replace"))
    except Exception as e:
        if hit:
            # The FAA changed the TFR and the new detail is unreachable: keep the last
            # parsed detail (window, geometry, legal basis) rather than blanking it.
            log.warning("TFR detail %s: refresh failed (%s); keeping the previous detail", notam_id, e)
            return hit[1]
        log.warning("TFR detail %s: window unavailable (%s)", notam_id, e)
        return None
    _detail_cache[notam_id] = (mod, detail)
    return detail


def fetch_detail_window(notam_id: str, mod: str) -> tuple[float | None, float | None, list[list[float]]]:
    """(start, end, area windows) for one TFR; (None, None, []) on failure."""
    d = fetch_detail(notam_id, mod)
    if d is None:
        return None, None, []
    return d["start"], d["end"], d["windows"]


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

        detail = None
        if item.get("notam_id") and (
                (tfr_type or "").strip().upper() in DETAIL_TYPES or is_vip(vip_text, tfr_type)):
            detail = fetch_detail(
                str(item["notam_id"]), str(item.get("mod_abs_time") or item.get("mod_date") or ""))
        vip, reasons, zdc = classify(item, detail)

        start = end = None
        extra: dict = {}
        if detail is not None:
            start, end = detail["start"], detail["end"]
            if detail["windows"]:
                extra["area_windows_utc"] = detail["windows"]
            if detail["areas_geo"]:
                extra["areas_geo"] = detail["areas_geo"]
        if reasons:
            extra["vip_push_reasons"] = reasons
        if zdc:
            extra["zdc_basis"] = zdc
        if "security_residence" in reasons:
            extra["residence"] = protected_residence(item, detail)
        if (tfr_type or "").strip().upper() in DETAIL_TYPES:
            # Lets the pusher's one-time cutover tell a row this classifier wrote
            # from a row the pre-10-18 poller wrote (pusher.main.tfr_security_cutover).
            extra["class_v"] = CLASS_VERSION
        if extra:
            item = {**item, **extra}

        records.append(TFRRecord(
            tfr_id=tfr_id,
            raw_text=vip_text,
            raw_json=json.dumps(item),
            vip=vip,
            effective_start=start,
            effective_end=end,
            vip_push_reason=reasons[0] if reasons else None,
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
                vip_push_reason=rec.vip_push_reason,
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
        log.info("TFR fetch OK — %d TFRs, %d VIP, %d VIP-class push", len(records), len(new_vip_ids),
                 sum(1 for r in records if r.vip_push_reason))
        return {"count": len(records), "vip_ids": new_vip_ids}

    except Exception as e:
        msg = str(e)
        log.error("TFR fetch FAILED: %s", msg)
        db.upsert_feed(feed_name, fetched_at, error=msg)
        return {"error": msg}
