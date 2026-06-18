"""
Amtrak fetcher — polls amtraker.com v3 API for NEC corridor train status.

Formerly polled a local Amtrak tracker container; now queries amtraker.com
directly since that container is not deployed. Falls back gracefully if the
feed is unreachable.

Config (dispatch.env):
  AMTRAK_FEED_URL           — base URL for amtraker v3, defaults to
                               https://api.amtraker.com/v3/trains
  AMTRAK_LOCAL_URL          — set to non-empty to use a local container instead
                               (legacy path; leave unset to use amtraker.com)
  AMTRAK_PRIMARY_STATION    — IATA/Amtrak station code for the operator's hub
                               (default: WAS). Used for map centering.
  AMTRAK_REGIONAL_STATIONS  — comma-separated Amtrak station codes to gate
                               long-distance trains on (default: WAS area set).
                               Example: CHI,MKE,GBD,NPV for Chicago metro.
  AMTRAK_REGIONAL_ROUTES    — comma-separated route names always included
                               regardless of station filter (default: NEC list).
                               Example: Empire Builder,California Zephyr
  AMTRAK_CORE_ROUTES        — comma-separated route names shown in the top
                               "always-on" panel section (default: Acela,
                               Northeast Regional). Set to your key services.

Polled every 5 minutes by the poller scheduler.
"""

import hashlib
import json
import logging
import time
from datetime import datetime, timezone

import requests

from common import config, db

log = logging.getLogger(__name__)

FETCH_TIMEOUT = 12

# ── Default regional config (DC / NEC) ───────────────────────────────────────
# These are the fallback values used when no AMTRAK_* env vars are set.
# Operators deploying the public version should override via dispatch.env.

_DEFAULT_ROUTES = [
    "Acela", "Northeast Regional", "Palmetto", "Carolinian",
    "Vermonter", "Keystone", "Empire Service", "Empire State",
    "Silver Star", "Silver Meteor",
]
_DEFAULT_STATIONS     = frozenset({"WAS", "BWI", "NCR", "ALX", "BAL", "ABE", "WIL", "NPN"})
_DEFAULT_CORE_ROUTES  = ["Acela", "Northeast Regional"]
_DEFAULT_PRIMARY      = "WAS"

# Kept for backwards-compat — use the helper functions below in new code.
NEC_ROUTES      = _DEFAULT_ROUTES
DC_STATIONS     = _DEFAULT_STATIONS
DC_STATION_CODE = _DEFAULT_PRIMARY


def regional_routes() -> list[str]:
    raw = config.get("AMTRAK_REGIONAL_ROUTES", "").strip()
    return [r.strip() for r in raw.split(",") if r.strip()] if raw else _DEFAULT_ROUTES


def regional_stations() -> frozenset:
    raw = config.get("AMTRAK_REGIONAL_STATIONS", "").strip()
    return frozenset(s.strip().upper() for s in raw.split(",") if s.strip()) if raw else _DEFAULT_STATIONS


def core_routes() -> list[str]:
    raw = config.get("AMTRAK_CORE_ROUTES", "").strip()
    return [r.strip() for r in raw.split(",") if r.strip()] if raw else _DEFAULT_CORE_ROUTES


def primary_station() -> str:
    return config.get("AMTRAK_PRIMARY_STATION", _DEFAULT_PRIMARY).strip().upper() or _DEFAULT_PRIMARY


def _use_local() -> bool:
    """Return True if a local container URL is explicitly configured."""
    return bool(config.get("AMTRAK_LOCAL_URL", "").strip())


def _local_url() -> str:
    return config.get("AMTRAK_LOCAL_URL", "").rstrip("/")


def _feed_url() -> str:
    return config.get("AMTRAK_FEED_URL", "https://api.amtraker.com/v3/trains").rstrip("/")


def _disabled() -> bool:
    return False  # Always enabled; gracefully skips on network error


def _delay_minutes(train: dict) -> int:
    """Compute delay in minutes from the first Enroute station's schArr vs arr."""
    for s in train.get("stations", []):
        if s.get("status") == "Enroute":
            sch = s.get("schArr", "")
            act = s.get("arr", "")
            if sch and act and sch != act:
                try:
                    ds = datetime.fromisoformat(sch.replace("Z", "+00:00"))
                    da = datetime.fromisoformat(act.replace("Z", "+00:00"))
                    return int((da - ds).total_seconds() / 60)
                except Exception:
                    pass
            break
    return 0


def _normalize(raw_trains: dict) -> list:
    """
    Convert amtraker v3 dict-of-trains to a normalised list compatible with
    _summarize(). Filters to configured regional routes or station stops.
    Deduplicates by trainID, keeping the entry with the highest absolute delay.
    """
    seen: dict[str, dict] = {}  # trainID → best entry
    _routes   = regional_routes()
    _stations = regional_stations()

    for _num, v in raw_trains.items():
        entries = v if isinstance(v, list) else [v]
        for t in entries:
            route = t.get("routeName", "")
            orig  = t.get("origCode", "")
            dest  = t.get("destCode", "")
            station_codes = {s.get("code", "") for s in t.get("stations", [])}

            is_regional = any(r.lower() in route.lower() for r in _routes)
            touches_hub = bool(station_codes & _stations) or orig in _stations or dest in _stations

            if not (is_regional or touches_hub):
                continue

            delay = _delay_minutes(t)
            train_id = t.get("trainID") or t.get("trainNum", _num)
            entry = {
                "train_number": t.get("trainNum", _num),
                "train_name":   f"{t.get('routeName','?')} {t.get('trainNum','?')}",
                "delay_minutes": delay,
                "train_state":  t.get("trainState", ""),
                "orig_code":    orig,
                "dest_code":    dest,
                "event_name":   t.get("eventName", ""),
                "_raw":         t,
            }
            # Dedup by trainNum (amtraker uses trainID with date suffix like "19-12"
            # which can vary across runs for the same physical train)
            dedup_key = t.get("trainNum", _num)
            if dedup_key not in seen or abs(delay) > abs(seen[dedup_key]["delay_minutes"]):
                seen[dedup_key] = entry

    return list(seen.values())


def fetch() -> list:
    """
    Fetch DC-area/NEC Amtrak train status.
    Returns a normalised list of train dicts.
    Raises on connection error.
    """
    if _use_local():
        # Legacy local container path
        url = f"{_local_url()}/api/trains/{DC_STATION_CODE}"
        resp = requests.get(url, timeout=FETCH_TIMEOUT,
                            headers={"User-Agent": "corporatetraveldc/1.0"})
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else data.get("trains", [])

    # amtraker.com v3 — full train dict, filter + normalise locally
    resp = requests.get(_feed_url(), timeout=FETCH_TIMEOUT,
                        headers={"User-Agent": "corporatetraveldc/1.0"})
    resp.raise_for_status()
    return _normalize(resp.json())


def _summarize(trains: list) -> str:
    """Build a short human-readable delay summary from normalised train list."""
    if not trains:
        return "No DC-area NEC train data available."

    delayed = [t for t in trains
               if isinstance(t, dict) and int(t.get("delay_minutes", 0)) > 15]

    if not delayed:
        return f"All {len(trains)} DC-area NEC trains on time."

    delay_lines = []
    for t in delayed[:5]:  # Cap at 5 for ntfy message length
        name = t.get("train_name") or str(t.get("train_number", "Unknown"))
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
        trains = fetch()
        summary = _summarize(trains)

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

    except requests.exceptions.ConnectionError as e:
        msg = f"Amtrak feed unreachable: {e}"
        log.warning(msg)
        db.upsert_feed(feed_name, fetched_at, error=msg)
        return {"skipped": True, "reason": "unreachable"}

    except Exception as e:
        msg = str(e)
        log.error("Amtrak fetch FAILED: %s", msg)
        db.upsert_feed(feed_name, fetched_at, error=msg)
        return {"error": msg}
