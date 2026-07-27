"""
shared.sector_coalesce — sector/corridor-based alert coalescing.

Built 2026-07-20 per operator direction: group live NAS congestion events
(TFMS restrictions/GDP/GS/advisories, and eventually other feeds) by ARTCC
sector/corridor rather than only by individual program/facility, detect
when a sector's event rate is genuinely escalating (vs. routine background
noise), and let an operator query either direction -- "what's happening in
this sector" or "which sectors is this feed/incident-type affecting" --
plus optionally silence routine traffic per-sector or per-feed without
losing the ability to see a real escalating trend.

Design, deliberately kept lightweight (no new heavyweight dependency, no
new always-on background thread):
  - Sector resolution is a static facility->sector lookup (see
    _SECTOR_FACILITY_MAP). Unmapped facilities fall into "OTHER" rather
    than being dropped -- still counted, still queryable, just not named.
  - Trend detection is two-window comparison, not a full time-series
    model: current 15-min rolling window vs. the window immediately
    before it. If current >= _ESCALATE_MULTIPLIER x prior (and prior
    exceeded a floor, or current alone crosses an absolute floor when
    prior was empty), the sector is "escalating" -- events get priority+1
    (capped at 5) and an ESCALATING tag on the alert title. This is a
    deliberately simple two-sample comparison rather than a proper EWMA/
    stats model: cheap, dependency-free, and easy for an operator to
    reason about ("more than 3x the last 15 minutes' worth, in the last
    15 minutes").
  - Silence is opt-in and OFF by default for every sector/feed -- toggling
    it never happens automatically. State persists to a small JSON file
    (survives the frequent ingest restarts from the memory-leak mitigation
    timer) so an operator's silence choice isn't lost on the next
    preventive restart.
  - All state is in-process (module-level dicts) except the silence
    toggles, which are the only piece worth surviving a restart -- the
    rolling event counts are inherently short-window and naturally reset
    to a fresh baseline on restart, which is fine (a 15-min window mostly
    self-heals within one window anyway).

Known gap, documented rather than guessed at: the operator specifically
named "Oceanic Atlantic" and "Gulf" as sectors of interest, but no real
captured message this session carried a confirmed oceanic/Gulf-of-Mexico
ARTCC facility code (ZWY/oceanic sector designators, or ZHU for Gulf) --
_SECTOR_FACILITY_MAP intentionally leaves those two sector NAMES defined
with an empty facility set rather than guessing, so they show up in
get_sector_summary() (as zero-activity) but won't silently misattribute
some other facility's events to them. Populate the facility sets once a
real oceanic/Gulf-tagged program is captured and confirmed.
"""
from __future__ import annotations

import json
import logging
import os
import time
import threading

log = logging.getLogger("shared.sector_coalesce")

_STATE_FILE = "/var/lib/corporatetraveldc/sector_coalesce_silence.json"
_WINDOW_SECS = 900          # 15-minute rolling window
_ESCALATE_MULTIPLIER = 3.0  # current window >= this x prior window -> escalating
_ESCALATE_FLOOR = 3         # minimum current-window count to ever escalate off an empty prior window

# Named sectors/corridors per operator direction. Facility codes are ARTCC
# identifiers (and a few TRACON/airport codes for the DC-local group, which
# already carries mixed facility/airport values across the codebase).
_SECTOR_FACILITY_MAP: dict[str, set[str]] = {
    "DC_LOCAL":         {"ZDC", "PCT", "DCA", "IAD", "BWI"},
    "NEW_YORK":         {"ZNY", "N90", "JFK", "LGA", "EWR"},
    "BOSTON":           {"ZBW", "BOS"},
    "ST_LOUIS":         {"ZKC", "STL"},   # St. Louis is worked by Kansas City ARTCC (ZKC)
    "ATLANTA":          {"ZTL", "ATL"},
    "OCEANIC_ATLANTIC": set(),  # TODO: populate once a real oceanic-tagged sample is confirmed
    "GULF":             set(),  # TODO: populate once a real Gulf/ZHU-tagged sample is confirmed
}

# Reverse lookup, built once: facility code -> sector name.
_FACILITY_TO_SECTOR: dict[str, str] = {
    facility: sector
    for sector, facilities in _SECTOR_FACILITY_MAP.items()
    for facility in facilities
}

# Per-ARTCC ntfy topic routing, added 2026-07-21 per operator direction:
# distinct from the _SECTOR_FACILITY_MAP grouping above (which feeds the
# escalation/silence machinery and stays keyed by descriptive names like
# "DC_LOCAL"/"NEW_YORK"), this is a flat facility -> dedicated-topic map
# for the 8 ARTCCs the operator explicitly asked to track separately, so
# metering/congestion trend per sector can be watched in isolation rather
# than only inside the aggregate tbfm-alerts feed. Airport/TRACON codes
# that fall under one of these centers are folded into the same topic.
_ARTCC_NTFY_TOPIC: dict[str, str] = {
    "ZNY": "tbfm-zny", "N90": "tbfm-zny", "JFK": "tbfm-zny", "LGA": "tbfm-zny", "EWR": "tbfm-zny",
    "ZDC": "tbfm-zdc", "PCT": "tbfm-zdc", "DCA": "tbfm-zdc", "IAD": "tbfm-zdc", "BWI": "tbfm-zdc",
    "ZID": "tbfm-zid",
    "ZOB": "tbfm-zob",
    "ZATL": "tbfm-zatl", "ATL": "tbfm-zatl",
    "ZHU": "tbfm-zhu",
    "ZLA": "tbfm-zla",
    "ZSE": "tbfm-zse",  # Seattle ARTCC -- covers Seattle per operator request
}


def sector_ntfy_topic(facility_or_center: str | None) -> str | None:
    """
    Map a facility/ARTCC/airport code to its dedicated per-sector ntfy
    topic (tbfm-<sector>) for the 8 sectors the operator asked to track
    individually. Returns None if the facility isn't one of these --
    callers should skip the extra per-sector push in that case rather
    than fall back to a generic topic; the aggregate tbfm-alerts fire
    already covers it.
    """
    if not facility_or_center:
        return None
    return _ARTCC_NTFY_TOPIC.get(facility_or_center.strip().upper())


_lock = threading.Lock()

# sector -> list of event epoch timestamps in the current+prior window
# (pruned lazily on each record_event call; kept as a flat list per sector,
# not per-feed, since the window math is on the sector as a whole).
_sector_events: dict[str, list[float]] = {}

# feed_name -> sector -> list of event epoch timestamps (same window,
# additionally sliced by feed so get_feed_summary() can answer "which
# sectors is this feed touching" without re-deriving it from raw events).
_feed_sector_events: dict[str, dict[str, list[float]]] = {}

# Silenced sectors / feeds -- opt-in, persisted.
_silenced_sectors: set[str] = set()
_silenced_feeds: set[str] = set()


def _load_silence_state() -> None:
    global _silenced_sectors, _silenced_feeds
    try:
        with open(_STATE_FILE) as f:
            data = json.load(f)
        _silenced_sectors = set(data.get("silenced_sectors", []))
        _silenced_feeds = set(data.get("silenced_feeds", []))
    except FileNotFoundError:
        pass
    except Exception as e:
        log.warning("sector_coalesce: failed to load silence state: %s", e)


def _save_silence_state() -> None:
    try:
        os.makedirs(os.path.dirname(_STATE_FILE), exist_ok=True)
        with open(_STATE_FILE, "w") as f:
            json.dump({
                "silenced_sectors": sorted(_silenced_sectors),
                "silenced_feeds": sorted(_silenced_feeds),
            }, f)
    except Exception as e:
        log.warning("sector_coalesce: failed to save silence state: %s", e)


_load_silence_state()


def resolve_sector(facility_or_center: str | None) -> str:
    """Map a facility/center/airport code to its named sector. Unmapped
    (including None/empty) codes fall into 'OTHER' -- still tracked, just
    unnamed, so nothing silently disappears from the aggregate counts."""
    if not facility_or_center:
        return "OTHER"
    return _FACILITY_TO_SECTOR.get(facility_or_center.strip().upper(), "OTHER")


def set_sector_silence(sector: str, silenced: bool) -> None:
    with _lock:
        if silenced:
            _silenced_sectors.add(sector)
        else:
            _silenced_sectors.discard(sector)
        _save_silence_state()


def set_feed_silence(feed_name: str, silenced: bool) -> None:
    with _lock:
        if silenced:
            _silenced_feeds.add(feed_name)
        else:
            _silenced_feeds.discard(feed_name)
        _save_silence_state()


def _prune(events: list[float], now: float) -> None:
    cutoff = now - (2 * _WINDOW_SECS)
    while events and events[0] < cutoff:
        events.pop(0)


def record_event(sector: str, feed_name: str) -> dict:
    """
    Record one event for (sector, feed_name) and return the current
    classification:
        {"sector": ..., "window_count": int, "prior_count": int,
         "escalating": bool, "sector_silenced": bool, "feed_silenced": bool}

    window_count = events in the last _WINDOW_SECS.
    prior_count  = events in the _WINDOW_SECS before that.
    escalating   = window_count >= _ESCALATE_MULTIPLIER * prior_count
                   (with a floor so a single event against an empty prior
                   window doesn't trivially count as "3x zero").
    """
    now = time.time()
    with _lock:
        events = _sector_events.setdefault(sector, [])
        events.append(now)
        _prune(events, now)

        feed_map = _feed_sector_events.setdefault(feed_name, {})
        feed_events = feed_map.setdefault(sector, [])
        feed_events.append(now)
        _prune(feed_events, now)

        window_count = sum(1 for t in events if t >= now - _WINDOW_SECS)
        prior_count = sum(1 for t in events if now - 2 * _WINDOW_SECS <= t < now - _WINDOW_SECS)

        escalating = (
            window_count >= _ESCALATE_FLOOR
            and window_count >= _ESCALATE_MULTIPLIER * max(prior_count, 0.5)
        )

        return {
            "sector": sector,
            "window_count": window_count,
            "prior_count": prior_count,
            "escalating": escalating,
            "sector_silenced": sector in _silenced_sectors,
            "feed_silenced": feed_name in _silenced_feeds,
        }


def maybe_fire_coalesced_alert(
    ntfy_topic: str,
    feed_name: str,
    facility_or_center: str | None,
    title: str,
    detail: str,
    dispatch: str,
    base_priority: int = 3,
) -> dict:
    """
    Single entry point for a parser to route an alert-worthy event through
    sector coalescing before it hits ntfy. Resolves the sector, records the
    event, applies silence rules, escalates priority on a genuine trend,
    and fires via shared.watchlist._fire_ntfy_dual (same push path used
    everywhere else) unless silenced.

    Returns the classification dict from record_event (with a "fired" key
    added) so callers can log/inspect what happened without re-deriving it.
    """
    sector = resolve_sector(facility_or_center)
    classification = record_event(sector, feed_name)
    classification["fired"] = False

    if classification["sector_silenced"] or classification["feed_silenced"]:
        return classification

    priority = base_priority
    display_title = title
    if classification["escalating"]:
        priority = min(5, base_priority + 1)
        display_title = f"[ESCALATING/{sector}] {title}"
    else:
        display_title = f"[{sector}] {title}"

    try:
        from shared.watchlist import _fire_ntfy_dual
        _fire_ntfy_dual(ntfy_topic, display_title, detail, dispatch, priority=priority)
        classification["fired"] = True
    except Exception as e:
        log.error("sector_coalesce: alert fire failed for sector=%s feed=%s: %s",
                  sector, feed_name, e)

    return classification


def get_sector_summary() -> dict:
    """Per-sector rollup: window/prior counts, escalating flag, silence
    state, and which feeds have contributed events to that sector in the
    current window ('the reverse of that, the feeds themselves' -- from
    the sector side)."""
    now = time.time()
    with _lock:
        result = {}
        all_sectors = set(_sector_events) | set(_SECTOR_FACILITY_MAP)
        for sector in sorted(all_sectors):
            events = _sector_events.get(sector, [])
            window_count = sum(1 for t in events if t >= now - _WINDOW_SECS)
            prior_count = sum(1 for t in events if now - 2 * _WINDOW_SECS <= t < now - _WINDOW_SECS)
            escalating = (
                window_count >= _ESCALATE_FLOOR
                and window_count >= _ESCALATE_MULTIPLIER * max(prior_count, 0.5)
            )
            contributing_feeds = sorted(
                feed for feed, sectors in _feed_sector_events.items()
                if any(t >= now - _WINDOW_SECS for t in sectors.get(sector, []))
            )
            result[sector] = {
                "window_count": window_count,
                "prior_count": prior_count,
                "escalating": escalating,
                "silenced": sector in _silenced_sectors,
                "contributing_feeds": contributing_feeds,
                "known_facilities": sorted(_SECTOR_FACILITY_MAP.get(sector, set())),
            }
        return result


def get_feed_summary() -> dict:
    """Per-feed rollup: which sectors this feed has contributed events to
    in the current window, and how many. 'The reverse of that' -- from the
    feed side."""
    now = time.time()
    with _lock:
        result = {}
        for feed_name, sectors in _feed_sector_events.items():
            per_sector = {}
            for sector, events in sectors.items():
                window_count = sum(1 for t in events if t >= now - _WINDOW_SECS)
                if window_count:
                    per_sector[sector] = window_count
            result[feed_name] = {
                "silenced": feed_name in _silenced_feeds,
                "sectors": per_sector,
            }
        return result
