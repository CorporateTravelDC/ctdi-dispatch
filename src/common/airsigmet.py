"""
common.airsigmet -- shared AWC AIRMET/SIGMET Data-API client + normalizer.

Factored out of web/main.py's map-overlay integration on 2026-08-31 so the
convective-SIGMET archiver (poller/skills/convective_sigmet_archiver.py)
and the web overlay normalize identically -- importing the function, not
copying it (the db_swim.py conn() precedent), so a field-shape fix lands
in both consumers at once. web/main.py's _normalize_airsigmet is now a
thin wrapper over normalize_airsigmet() that only adds the UI color.

Source: AWC's real Data API (aviationweather.gov/api/data/airsigmet) --
NOT the aviationweather.gov HTML/progchart pages, which are blocked by an
edge WAF for automated requests (see web/main.py's 2026-08-03 section
comment). The /api/data/* endpoints are the same documented public API
already used elsewhere on this platform for METAR/TAF. One call covers
BOTH domestic AIRMETs and SIGMETs (airSigmetType distinguishes them).

Live-verified field shapes (2026-08-31, 16 convective SIGMETs active
nationwide at fetch time; verbatim capture snapshotted to
tests/poller/fixtures/awc_airsigmet_live_20260831.json):

  - validTimeFrom/validTimeTo arrive as UNIX EPOCH INTEGERS, not ISO
    strings. The web overlay has always passed them through raw (the
    Leaflet layer never parses them); epoch_to_iso() below is for
    consumers that store ISO alongside the rest of the platform's
    timestamps.
  - the composite id (icaoId-seriesId-alphaChar, e.g. "KKCI-38E-E") is
    only unique per issuance: convective SIGMET series numbers restart,
    so KKCI-38E-E recurs on a later day naming a DIFFERENT storm with
    different validity. Durable keying must pair the id with valid_from
    -- see db_swim.SCHEMA_SWIM_V47.
  - movementDir/movementSpd (storm motion, deg/kt) exist and are often
    null; creationTime (ISO) is the product's own issue time.
"""
from __future__ import annotations

from datetime import datetime, timezone

AWC_AIRSIGMET_URL = "https://aviationweather.gov/api/data/airsigmet"

CONVECTIVE = "CONVECTIVE"


def normalize_airsigmet(r: dict, raw_text_limit: int | None = 600) -> dict | None:
    """Normalize one raw AWC airsigmet record. Returns None for records
    without a usable polygon (<3 coordinate pairs) -- a hazard area we
    cannot place on a map or match a reroute against is not worth
    carrying. raw_text_limit=600 preserves the web overlay's historical
    truncation; the archiver passes None to keep the full product text
    (outlook sections routinely run past 600 chars and are exactly the
    kind of context a later attribution pass wants)."""
    coords = r.get("coords") or []
    latlngs = [[c["lat"], c["lon"]] for c in coords if "lat" in c and "lon" in c]
    if len(latlngs) < 3:
        return None
    hazard = (r.get("hazard") or "").upper().replace(" ", "_")
    raw_text = r.get("rawAirSigmet") or ""
    if raw_text_limit is not None:
        raw_text = raw_text[:raw_text_limit]
    return {
        "id": f"{r.get('icaoId','')}-{r.get('seriesId','')}-{r.get('alphaChar','')}",
        "type": r.get("airSigmetType") or "AIRMET",
        "hazard": hazard,
        "severity": r.get("severity"),
        "altitude_low": r.get("altitudeLow1"),
        "altitude_high": r.get("altitudeHi1"),
        "valid_from": r.get("validTimeFrom"),
        "valid_to": r.get("validTimeTo"),
        "issued_at": r.get("creationTime"),
        "movement_dir": r.get("movementDir"),
        "movement_spd": r.get("movementSpd"),
        "coords": latlngs,
        "raw_text": raw_text,
    }


def fetch_airsigmets(timeout: float = 15.0,
                     raw_text_limit: int | None = 600) -> list[dict]:
    """Synchronous fetch + normalize of the full active AIRMET/SIGMET
    set. Raises on transport/HTTP/decode errors -- callers own their
    failure policy (the web overlay serves its stale cache, the archiver
    logs and exits non-fatally; neither wants a half-parsed list).
    httpx import is deferred so importing this module never drags the
    HTTP stack into pure-DB consumers (db_swim, tests)."""
    import httpx
    r = httpx.get(AWC_AIRSIGMET_URL, params={"format": "json"}, timeout=timeout)
    r.raise_for_status()
    raw = r.json()
    return [n for n in (normalize_airsigmet(x, raw_text_limit) for x in raw) if n]


def epoch_to_iso(v) -> str | None:
    """AWC epoch-int (or numeric string) -> platform-standard ISO-8601 Z
    string. ISO strings pass through untouched; None/garbage -> None
    (never raises -- a bad timestamp must not kill an archive cycle)."""
    if v is None:
        return None
    try:
        if isinstance(v, str):
            s = v.strip()
            if not s:
                return None
            if not s.replace(".", "", 1).isdigit():
                return s  # already ISO-ish; store verbatim
            v = float(s)
        return datetime.fromtimestamp(float(v), tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, OverflowError, OSError, TypeError):
        return None
