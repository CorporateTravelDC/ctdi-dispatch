"""common.airplanes_live -- FALLBACK-ONLY lookup against the airplanes.live API
(2026-10-08, operator).

History: removed from every lookup path 2026-08-27 ("everything is meant to be
local"). The operator's feeder status was validated by the site's manager and
the account whitelisted on 2026-10-08 (it had been caught by bot-abuse controls
enabled from 2026-08-12), and the operator reinstated it with a fixed role:

  * FALLBACK ONLY: asked only after every local source (own ADS-B receiver,
    ingested FDPS, local registry) has no contact. Never first, never a second
    opinion on a local answer.
  * Position and identity only. Its data is ADS-B, so it carries no OOOI
    authority: the sweep's existing gate already refuses ADS-B-derived phases
    without ACARS/FIDS/FDPS confirmation and never lets ADS-B assert ON or IN.
  * Hex, not flight number, for any published tracking URL
    (https://globe.airplanes.live/?icao=<hex>), unchanged.

Privacy: an identifier on the FAA LADD list is never sent to the API (the query
itself would tell a third party which privacy-listed aircraft this deployment is
following). Etiquette: one request at a time, at least MIN_INTERVAL_S apart per
process, short timeout, 30 s cache, identified User-Agent.

Configuration: AIRPLANES_LIVE_FALLBACK=1 enables it (dispatch.env). The code
default is OFF, so the public edition never calls a third party unless the
deploying operator turns it on. Never raises.
"""
from __future__ import annotations

import logging
import re
import threading
import time

import requests

from common import config

log = logging.getLogger(__name__)

API_BASE = "https://api.airplanes.live/v2"
USER_AGENT = "ctdi-dispatch/1 (feeder; fallback lookups only)"
MIN_INTERVAL_S = 1.1
TIMEOUT_S = 5.0
CACHE_TTL_S = 30.0
MAX_POSITION_AGE_S = 60.0
SOURCE = "airplanes_live"

_HEX_RE = re.compile(r"^[0-9a-f]{6}$")
_CALLSIGN_RE = re.compile(r"^[A-Z0-9]{2,8}$")
_lock = threading.Lock()
_last_call = 0.0
_cache: dict[str, tuple[float, dict | None]] = {}

# 2026-10-23: back off after a refusal / outage. Seen 2026-10-22: a hex lookup
# for an aircraft the callsign lookup had just found returned None -- not a
# path/parsing difference (both endpoints answer the same 'ac' list), but an
# HTTP error. The poller's sweep retried every un-answered hex on every cycle
# (errors were never cached, no backoff), ~1 request per MIN_INTERVAL_S all day
# (39k hex HTTPErrors on 2026-10-22, status never logged), so the shared IP sat
# at the API's limit and any other lookup from the box -- here the hex one --
# drew the error, indistinguishable from "no contact" to the caller. Now:
# 429/403/5xx and connection errors stop all calls from this process for a
# Retry-After / exponential window, and the status code is logged.
BACKOFF_BASE_S = 60.0
BACKOFF_MAX_S = 900.0
_backoff_until = 0.0
_backoff_s = 0.0


def _retry_after_s(resp) -> float | None:
    try:
        v = float((getattr(resp, "headers", None) or {}).get("Retry-After"))
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def _enter_backoff(why: str, retry_after: float | None = None) -> None:
    global _backoff_until, _backoff_s
    _backoff_s = min(BACKOFF_MAX_S, max(BACKOFF_BASE_S, _backoff_s * 2))
    wait = min(BACKOFF_MAX_S, retry_after) if retry_after else _backoff_s
    _backoff_until = time.monotonic() + wait
    log.warning("airplanes.live fallback %s -- pausing lookups for %.0f s", why, wait)


def backoff_remaining() -> float:
    """Seconds left in the current refusal/outage backoff (0 when none)."""
    return max(0.0, _backoff_until - time.monotonic())


def enabled() -> bool:
    return str(config.get("AIRPLANES_LIVE_FALLBACK", "0")).strip().lower() in ("1", "true", "yes", "on")


def _ladd_listed(ident: str, kind: str) -> bool:
    """True if the identifier (or, for a hex, the registration it maps to) is on
    the LADD list. Unknown = not listed; a lookup failure = treat as listed."""
    try:
        from common import db
        if kind == "hex":
            rec = db.faa_lookup_by_hex(ident)
            return bool(rec and rec.get("ladd"))
        return bool(db.faa_is_ladd(ident))
    except Exception as e:  # noqa: BLE001 -- fail closed: no external query
        log.debug("airplanes.live: LADD check failed for a %s (%s) -- not querying", kind, type(e).__name__)
        return True


def _normalize(ac: dict) -> dict:
    out = {k: ac.get(k) for k in ("hex", "r", "t", "flight", "lat", "lon", "alt_baro", "gs", "track",
                                  "squawk", "seen_pos")}
    out["hex"] = (out.get("hex") or "").lower().strip()
    out["flight"] = (out.get("flight") or "").strip()
    out["_source"] = SOURCE
    return out


def _get(path: str, key: str) -> dict | None:
    """One rate-limited, cached GET. Returns the freshest positioned aircraft or None."""
    global _last_call, _backoff_s
    now = time.monotonic()
    hit = _cache.get(key)
    if hit and now - hit[0] < CACHE_TTL_S:
        return hit[1]
    if now < _backoff_until:
        return None                      # refused/outage: not asking again yet
    kind = path.split("/")[0]
    with _lock:
        if time.monotonic() < _backoff_until:
            return None
        wait = MIN_INTERVAL_S - (time.monotonic() - _last_call)
        if wait > 0:
            time.sleep(wait)
        _last_call = time.monotonic()
        try:
            r = requests.get(f"{API_BASE}/{path}", headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT_S)
        except Exception as e:  # noqa: BLE001 -- a fallback never breaks the caller
            _enter_backoff(f"{kind} failed ({type(e).__name__})")
            return None
        status = getattr(r, "status_code", 200)
        if status in (403, 429) or (isinstance(status, int) and status >= 500):
            _enter_backoff(f"{kind} refused (HTTP {status})", _retry_after_s(r))
            return None
        try:
            r.raise_for_status()
            acs = [a for a in (r.json().get("ac") or []) if a.get("lat") is not None and a.get("lon") is not None
                   and float(a.get("seen_pos") or 0) <= MAX_POSITION_AGE_S]
        except Exception as e:  # noqa: BLE001 -- a fallback never breaks the caller
            log.info("airplanes.live fallback %s failed (non-fatal): %s (HTTP %s)", kind, type(e).__name__, status)
            return None
        _backoff_s = 0.0
    best = min(acs, key=lambda a: float(a.get("seen_pos") or 0)) if acs else None
    res = _normalize(best) if best else None
    _cache[key] = (time.monotonic(), res)
    return res


def by_hex(hex_code: str) -> dict | None:
    h = (hex_code or "").lower().strip()
    if not enabled() or not _HEX_RE.match(h) or _ladd_listed(h, "hex"):
        return None
    res = _get(f"hex/{h}", f"hex:{h}")
    if res:
        log.info("airplanes.live fallback: contact by hex")
    return res


def by_callsign(callsign: str) -> dict | None:
    cs = (callsign or "").upper().replace(" ", "")
    if not enabled() or not _CALLSIGN_RE.match(cs) or _ladd_listed(cs, "callsign"):
        return None
    res = _get(f"callsign/{cs}", f"cs:{cs}")
    if res and res.get("flight", "").upper() != cs:
        return None                                  # the API matched something else
    if res:
        log.info("airplanes.live fallback: contact by callsign")
    return res


def tracking_url(hex_code: str | None) -> str:
    """The published tracking link: hex only, never a flight number."""
    h = (hex_code or "").lower().strip()
    return f"https://globe.airplanes.live/?icao={h}" if _HEX_RE.match(h) else ""
