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
  - 2026-08-03: escalating=True has never implied a minimum interval
    between pushes -- a burst of individually-escalating SWIM messages for
    the same sector fired one push per message, which is what was
    overwhelming ntfy's per-topic long-lived connection for tfms-zdc/
    tbfm-zdc etc (thousands of pushes/day, persistent client-side
    "reconnecting"). Added a per-TOPIC (not per feed/sector -- the
    aggregate "<family>-alerts" and each "<family>-<zone>" topic are
    independently controllable) throttle/enable/sanitize override, same
    JSON-persisted-override shape as the escalate threshold above.
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
import re
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

# Per-ARTCC ntfy topic routing, added 2026-07-21 per operator direction for
# TBFM, extended 2026-08-02 to be family-aware so TFMS (nas-alerts today)
# can get the identical per-sector split without a second facility table to
# keep in sync by hand. Distinct from the _SECTOR_FACILITY_MAP grouping
# above (which feeds the escalation/silence machinery and stays keyed by
# descriptive names like "DC_LOCAL"/"NEW_YORK") -- this is a flat
# facility -> ARTCC-code map; the per-family ntfy topic is just
# "<family>-<code>" (tbfm-zdc, tfms-zdc, ...), so adding a new alert
# family that wants the same 8-sector granularity is a one-line call, not
# a new dict. Airport/TRACON codes that fall under one of these centers
# are folded into the same code. K-prefixed ICAO forms (KDCA/KIAD/KBWI)
# added to the zdc group 2026-08-02 -- ingest/parsers/tfms_parser.py's
# _DC_FACILITIES uses both K-prefixed and bare forms depending on which
# TFMS message shape it came from.
_ARTCC_GROUPS: dict[str, set[str]] = {
    "zny":  {"ZNY", "N90", "JFK", "LGA", "EWR", "KJFK", "KLGA", "KEWR"},
    "zdc":  {"ZDC", "PCT", "DCA", "IAD", "BWI", "KDCA", "KIAD", "KBWI"},
    "zid":  {"ZID", "CVG", "SDF", "KCVG", "KSDF"},
    "zob":  {"ZOB", "CLE", "PIT", "DTW", "KCLE", "KPIT", "KDTW"},
    # FIXED 2026-08-03: the real Atlanta ARTCC identifier is "ZTL", not
    # "ZATL" -- "ZATL" doesn't exist as an FAA facility code and has never
    # matched anything real. Confirmed via TBFM's own captured facility
    # values (tbfm_sequences table has real "ZTL" rows, never "ZATL").
    # This means Atlanta traffic has never actually zone-resolved for
    # TBFM (or anything else routed through this map) since the zone was
    # added -- it always fell through to "OTHER"/no zone topic. The zone
    # KEY here ("zatl", used for ntfy topic naming e.g. tbfm-zatl) is
    # unaffected by this fix, only the uppercase facility-code SET was
    # wrong.
    "zatl": {"ZTL", "ATL", "KATL"},
    "zhu":  {"ZHU", "IAH", "KIAH"},
    "zla":  {"ZLA", "LAX", "LAS", "KLAX", "KLAS"},
    "zse":  {"ZSE", "SEA", "PDX", "KSEA", "KPDX"},  # Seattle ARTCC -- covers Seattle per operator request
}

# 2026-08-03: added K-prefixed ICAO airport forms (KJFK, KATL, etc.) and
# real major-airport codes for zid/zob/zhu/zla/zse (previously these 5
# zones had ONLY their bare ARTCC code, no airport code at all) per
# operator direction to open STDDS/ITWS up to "the major transcontinental
# airports at each of the eight facilities we currently do for TBFM and
# TFMS." Airport choices are an approximation (real ARTCC boundaries are
# complex polygons this platform has no geometry for) -- these are
# well-known major hub airports conventionally associated with each named
# center, cross-checked against this platform's own captured STDDS data
# (every one of these airports has real rows in
# stdds_safety_status/surface_movement_events as of 2026-08-03) rather
# than picked from general knowledge alone. K-prefix matters because ITWS
# and STDDS both emit K-prefixed 4-letter ICAO airport codes (confirmed:
# itws_parser.py normalizes to "K" + 3-letter code), which never matched
# the bare 3-letter forms already in this map for non-DC zones -- meaning
# ITWS zone-routing has likely never worked outside zdc (zdc got its
# K-prefixed forms earlier, 2026-08-02, for an unrelated TFMS quirk).
# FDPS and TBFM pass ARTCC-style facility codes directly (already bare,
# unaffected by this K-prefix gap) -- their fix here is only the zatl
# code correction above.

_FACILITY_TO_ARTCC: dict[str, str] = {
    facility: code
    for code, facilities in _ARTCC_GROUPS.items()
    for facility in facilities
}

# Back-compat alias -- some older call sites/log lines may still reference
# this name; kept as a derived view rather than a second source of truth.
_ARTCC_NTFY_TOPIC: dict[str, str] = {
    facility: f"tbfm-{code}" for facility, code in _FACILITY_TO_ARTCC.items()
}


def sector_ntfy_topic(facility_or_center: str | None, family: str = "tbfm") -> str | None:
    """
    Map a facility/ARTCC/airport code to its dedicated per-sector ntfy
    topic ("<family>-<sector>", e.g. tbfm-zdc / tfms-zdc) for the 8 sectors
    the operator asked to track individually. Returns None if the facility
    isn't one of these -- callers should skip the extra per-sector push in
    that case rather than fall back to a generic topic (or fall back to
    their own feed's aggregate topic, e.g. tbfm-alerts/tfms-alerts, if one
    exists).

    family defaults to "tbfm" for backward compatibility with existing
    callers (tbfm_parser.py) that call this with a single positional arg.
    """
    if not facility_or_center:
        return None
    code = _FACILITY_TO_ARTCC.get(facility_or_center.strip().upper())
    if not code:
        return None
    return f"{family}-{code}"


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


# Per-(feed_name, sector) escalation threshold overrides -- added
# 2026-08-02 per operator direction: "threshold for each one of those...
# each feed and then the ... zones ... so you can set up each one however
# you need." Falls back to the global _ESCALATE_MULTIPLIER/_ESCALATE_FLOOR
# when no override is set for a given (feed, sector) pair. Persisted
# alongside silence state so overrides survive the frequent ingest
# restarts, same rationale as silence toggles.
# Keyed "feed_name:sector" -> [multiplier, floor] (list, not tuple --
# JSON doesn't have tuples, and this way _save_escalate_overrides doesn't
# need a converter).
_escalate_overrides: dict[str, list[float]] = {}

# Per-TOPIC push throttle / enable / sanitize -- added 2026-08-03 per
# operator direction: "a dynamic throttle or dynamic override for the
# throttle that ntfy normally runs on a per-topic basis... I believe that
# was actually done for the NAS alerts previously" (referring to the
# escalate-threshold override above) "...let's make sure that is now a
# per-topic parameter and directive." "Any of those can be turned off, and
# any of those can be sanitized for a source of truth for anything."
#
# Keyed by the literal ntfy topic string (e.g. "tfms-alerts", "tfms-zdc"),
# NOT by (feed, sector) -- the family-wide aggregate and each per-sector
# topic for the same feed are independently throttleable, on/off-able, and
# sanitizable. This is deliberately a separate mechanism from the
# escalate-threshold override: escalating=True/False decides whether an
# event QUALIFIES as alert-worthy; the throttle below decides how often a
# topic that keeps qualifying is actually allowed to push, which is what
# was missing.
_DEFAULT_MIN_INTERVAL_SECS = 60.0   # no topic pushes more than once/min unless overridden
_topic_min_interval: dict[str, float] = {}   # topic -> override seconds (absent = default)
_topic_enabled: dict[str, bool] = {}          # topic -> explicit False = off (absent = enabled)
_topic_sanitize: dict[str, bool] = {}         # topic -> True = mask identifiers before push (demo/source-of-truth reuse)
_last_fired: dict[str, float] = {}            # topic -> epoch of last successful push (in-process, resets on restart -- fine, same as rolling windows)

# Very small, deliberately conservative identifier masker for sanitized
# topics -- replaces N-number-shaped tokens and 6-hex-char ICAO addresses
# with a fixed placeholder. Not a general PII scrubber; only covers the
# two identifier shapes these feeds actually carry. Extend the pattern
# list if a sanitized topic starts leaking something else.
_SANITIZE_PATTERNS = [
    (re.compile(r"\bN[0-9]{1,5}[A-Z]{0,2}\b"), "N#####"),      # N-number registrations
    (re.compile(r"\b[0-9A-Fa-f]{6}\b"), "XXXXXX"),               # ICAO 24-bit hex addresses
]


def _sanitize_text(text: str) -> str:
    for pattern, repl in _SANITIZE_PATTERNS:
        text = pattern.sub(repl, text)
    return text


def set_topic_throttle(topic: str, min_interval_secs: float) -> None:
    """Override the minimum interval between pushes for one ntfy topic.
    Pass min_interval_secs<=0 to clear the override and revert to
    _DEFAULT_MIN_INTERVAL_SECS."""
    with _lock:
        if min_interval_secs <= 0:
            _topic_min_interval.pop(topic, None)
        else:
            _topic_min_interval[topic] = min_interval_secs
        _save_silence_state()


def get_topic_throttle(topic: str) -> float:
    """Resolve the effective minimum interval (seconds) between pushes for
    a topic -- the override if set, else _DEFAULT_MIN_INTERVAL_SECS."""
    return _topic_min_interval.get(topic, _DEFAULT_MIN_INTERVAL_SECS)


def set_topic_enabled(topic: str, enabled: bool) -> None:
    """Explicitly enable/disable a topic. Enabled is the default -- passing
    True just clears any prior override rather than adding a redundant one."""
    with _lock:
        if enabled:
            _topic_enabled.pop(topic, None)
        else:
            _topic_enabled[topic] = False
        _save_silence_state()


def is_topic_enabled(topic: str) -> bool:
    return _topic_enabled.get(topic, True)


def set_topic_sanitize(topic: str, sanitize: bool) -> None:
    """Mark a topic's pushes to be run through _sanitize_text() before
    firing -- for reusing a real alert stream as a demo/reporting
    source-of-truth without leaking real tail numbers/hex addresses."""
    with _lock:
        if sanitize:
            _topic_sanitize[topic] = True
        else:
            _topic_sanitize.pop(topic, None)
        _save_silence_state()


def is_topic_sanitize(topic: str) -> bool:
    return _topic_sanitize.get(topic, False)


def _throttle_allows(topic: str) -> bool:
    """True if `topic` is enabled AND outside its throttle window right
    now. Does not record anything -- call _record_fire() only after an
    actual successful push."""
    if not is_topic_enabled(topic):
        return False
    now = time.time()
    last = _last_fired.get(topic, 0.0)
    return (now - last) >= get_topic_throttle(topic)


def _record_fire(topic: str) -> None:
    _last_fired[topic] = time.time()


def get_topic_settings(topic: str) -> dict:
    """Effective settings for one topic, for the admin API / debugging."""
    return {
        "topic": topic,
        "enabled": is_topic_enabled(topic),
        "min_interval_secs": get_topic_throttle(topic),
        "sanitize": is_topic_sanitize(topic),
        "seconds_since_last_fire": (
            None if topic not in _last_fired else round(time.time() - _last_fired[topic], 1)
        ),
    }


def _load_silence_state() -> None:
    global _silenced_sectors, _silenced_feeds, _escalate_overrides
    global _topic_min_interval, _topic_enabled, _topic_sanitize
    try:
        with open(_STATE_FILE) as f:
            data = json.load(f)
        _silenced_sectors = set(data.get("silenced_sectors", []))
        _silenced_feeds = set(data.get("silenced_feeds", []))
        _escalate_overrides = {
            k: list(v) for k, v in data.get("escalate_overrides", {}).items()
        }
        _topic_min_interval = {
            k: float(v) for k, v in data.get("topic_min_interval", {}).items()
        }
        _topic_enabled = {
            k: bool(v) for k, v in data.get("topic_enabled", {}).items()
        }
        _topic_sanitize = {
            k: bool(v) for k, v in data.get("topic_sanitize", {}).items()
        }
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
                "escalate_overrides": _escalate_overrides,
                "topic_min_interval": _topic_min_interval,
                "topic_enabled": _topic_enabled,
                "topic_sanitize": _topic_sanitize,
            }, f)
    except Exception as e:
        log.warning("sector_coalesce: failed to save silence state: %s", e)


_load_silence_state()


def set_escalate_threshold(feed_name: str, sector: str, multiplier: float, floor: int) -> None:
    """Override the escalation multiplier/floor for one (feed, sector) pair.
    Pass multiplier<=0 to clear an override and revert to the global default."""
    key = f"{feed_name}:{sector}"
    with _lock:
        if multiplier <= 0:
            _escalate_overrides.pop(key, None)
        else:
            _escalate_overrides[key] = [multiplier, floor]
        _save_silence_state()


def get_escalate_threshold(feed_name: str, sector: str) -> tuple[float, int]:
    """Resolve the effective (multiplier, floor) for a (feed, sector) pair --
    the override if one is set, else the global default."""
    override = _escalate_overrides.get(f"{feed_name}:{sector}")
    if override:
        return float(override[0]), int(override[1])
    return _ESCALATE_MULTIPLIER, _ESCALATE_FLOOR


def resolve_sector(facility_or_center: str | None) -> str:
    """Map a facility/center/airport code to its named sector. Unmapped
    (including None/empty) codes fall into 'OTHER' -- still tracked, just
    unnamed, so nothing silently disappears from the aggregate counts."""
    if not facility_or_center:
        return "OTHER"
    return _FACILITY_TO_SECTOR.get(facility_or_center.strip().upper(), "OTHER")


def is_tracked_facility(facility_or_center: str | None) -> bool:
    """True if this facility/ARTCC/airport code is one of the 8 tracked
    zones (zny/zdc/zid/zob/zatl/zhu/zla/zse), regardless of which alert
    family is asking. Added 2026-08-03 so a feed can gate whether to even
    attempt per-zone alerting for a given facility BEFORE calling
    fire_family_alert, without keeping its own separate copy of the
    tracked-facility list in sync by hand (tfms_parser.py's old
    _DC_FACILITIES set was exactly this kind of drift risk -- it only
    covered zdc, silently gating TFMS to DC-only for over a week after
    the "8 sectors" design was supposedly already in place for it)."""
    if not facility_or_center:
        return False
    return facility_or_center.strip().upper() in _FACILITY_TO_ARTCC


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


def record_event(sector: str, feed_name: str, isolate: bool = False) -> dict:
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

    isolate -- added 2026-08-03. Default False preserves the original
    2026-07-20 design intent: _sector_events[sector] mixes every feed's
    events together, so "escalating" answers "is this SECTOR genuinely
    busier than usual right now, regardless of which feed is reporting
    it" -- every family wired in before this date (tbfm, tfms/tfms_aptc/
    tfms_gadv, itws, aim_fns, fdps) relies on that cross-feed pooling and
    keeps it.

    Pass isolate=True when a feed_name is a "sibling" sharing a family/
    topic with another feed_name's own alerting but must NOT
    sympathetically escalate together with it -- e.g. NOTAM-sourced
    flight-restriction events (feed_name="fdps_notam") sharing the fdps
    family/topics with fdps_parser's own proximity-tracking events
    (feed_name="fdps"): a burst of one type should not make the other
    type's classification read "escalating" just because they resolve to
    the same sector. When isolate=True, the event is recorded ONLY in the
    per-(feed_name, sector) bucket (_feed_sector_events) -- never added to
    the shared _sector_events[sector] list -- and window_count/prior_count
    are computed from that isolated bucket alone, so this feed_name's
    trend is judged purely against its own history.
    """
    now = time.time()
    with _lock:
        feed_map = _feed_sector_events.setdefault(feed_name, {})
        feed_events = feed_map.setdefault(sector, [])
        feed_events.append(now)
        _prune(feed_events, now)

        if isolate:
            count_source = feed_events
        else:
            events = _sector_events.setdefault(sector, [])
            events.append(now)
            _prune(events, now)
            count_source = events

        window_count = sum(1 for t in count_source if t >= now - _WINDOW_SECS)
        prior_count = sum(1 for t in count_source if now - 2 * _WINDOW_SECS <= t < now - _WINDOW_SECS)

        multiplier, floor = get_escalate_threshold(feed_name, sector)
        escalating = (
            window_count >= floor
            and window_count >= multiplier * max(prior_count, 0.5)
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
    escalating_only: bool = False,
) -> dict:
    """
    Single entry point for a parser to route an alert-worthy event through
    sector coalescing before it hits ntfy. Resolves the sector, records the
    event, applies silence rules, escalates priority on a genuine trend,
    and fires via shared.watchlist._fire_ntfy_dual (same push path used
    everywhere else) unless silenced.

    escalating_only -- added 2026-08-02 per operator direction ("make the
    generic alerts just for the escalating, or essentially the hot,
    alert"). When True, the event is still recorded (window tracking stays
    accurate either way) but the actual ntfy push is skipped unless
    classification["escalating"] is True. Use this on aggregate/"-alerts"
    topics meant to be a low-noise hot feed; leave False on per-sector
    topics where the operator wants to see the full stream (subject to
    that sector's own configurable threshold via set_escalate_threshold,
    which still controls what counts as "escalating" in the first place).

    Returns the classification dict from record_event (with a "fired" key
    added) so callers can log/inspect what happened without re-deriving it.
    """
    sector = resolve_sector(facility_or_center)
    classification = record_event(sector, feed_name)
    classification["fired"] = False

    if classification["sector_silenced"] or classification["feed_silenced"]:
        return classification

    if escalating_only and not classification["escalating"]:
        return classification

    if not _throttle_allows(ntfy_topic):
        return classification

    priority = base_priority
    display_title = title
    push_detail = detail
    if classification["escalating"]:
        priority = min(5, base_priority + 1)
        display_title = f"[ESCALATING/{sector}] {title}"
    else:
        display_title = f"[{sector}] {title}"
    if is_topic_sanitize(ntfy_topic):
        display_title = _sanitize_text(display_title)
        push_detail = _sanitize_text(push_detail)

    try:
        from shared.watchlist import _fire_ntfy_dual
        _fire_ntfy_dual(ntfy_topic, display_title, push_detail, dispatch, priority=priority)
        classification["fired"] = True
        _record_fire(ntfy_topic)
    except Exception as e:
        log.error("sector_coalesce: alert fire failed for sector=%s feed=%s: %s",
                  sector, feed_name, e)

    return classification


def fire_family_alert(
    family: str,
    feed_name: str,
    facility_or_center: str | None,
    title: str,
    detail: str,
    dispatch: str,
    base_priority: int = 3,
    escalating_only: bool = True,
    isolate: bool = False,
    zone_split: bool = True,
    sector_override: str | None = None,
) -> dict:
    """
    Convenience wrapper standardizing the "{family}-alerts / {family}-<zone>"
    topic pattern requested 2026-08-02: fires the aggregate ("<family>-alerts",
    any facility) AND, if the facility resolves to one of the 8 tracked ARTCC
    zones, the per-zone topic ("<family>-<zone>") -- both gated on the SAME
    classification (one record_event call, so the two pushes never disagree
    about whether this event was "escalating"). The per-zone fire uses that
    zone's own configurable threshold via get_escalate_threshold(feed_name,
    sector), so tightening/loosening one zone's sensitivity doesn't touch any
    other zone or the aggregate.

    escalating_only -- added 2026-08-03, defaults True (unchanged behavior
    for every caller wired in before this date -- tbfm/tfms/itws/aim_fns all
    genuinely want "only tell me about a burst", which is the entire reason
    this family-alert pattern exists). Pass False for a feed type where a
    single isolated event is itself alert-worthy and must not wait for a
    3x-burst pattern -- e.g. a standalone flight-restriction NOTAM, where
    silently waiting for two more before the first push would defeat the
    point. Sector/feed silence and per-topic throttle/sanitize still apply
    either way; only the escalation gate is conditional. Title/priority
    reflect the actual escalation state either way (ESCALATING tag + +1
    priority only when classification["escalating"] is True), same as
    maybe_fire_coalesced_alert()'s non-aggregate behavior.

    isolate -- added 2026-08-03, default False (unchanged behavior for
    every prior caller). Passed straight through to record_event() -- see
    its docstring. Set True when this feed_name is a sibling sharing this
    family/topic with another feed_name's own alerting (e.g. NOTAM
    flight-restrictions vs. fdps_parser's own proximity events, both under
    family="fdps") and must not sympathetically trigger, or be triggered
    by, that sibling's burst pattern.

    zone_split -- added 2026-08-03, default True (unchanged behavior for
    every prior caller). Set False for a feed whose facility_or_center is
    effectively constant or otherwise doesn't warrant the "<family>-<zone>"
    split -- e.g. stdds/TAIS, where facility is always "PCT" (a single
    TRACON, not one of the 8 tracked ARTCCs in name, though PCT itself
    happens to already be grouped under zdc in _ARTCC_GROUPS for tbfm/tfms
    purposes) -- "family-wide with no sectors" per operator direction
    2026-08-03. When False, the zone_topic lookup/fire is skipped entirely
    regardless of what sector_ntfy_topic() would have resolved.

    sector_override -- added 2026-08-03. The existing sector/zone machinery
    (resolve_sector via _SECTOR_FACILITY_MAP, sector_ntfy_topic via
    _ARTCC_GROUPS) is ARTCC-shaped: it groups facilities into one of 8
    named ARTCC-ish sectors. That's the wrong shape for a feed whose
    natural grouping is something else entirely -- e.g. stdds surface
    tracks, which group by AIRPORT (KDCA/KIAD/KBWI), not by ARTCC, and
    where all three airports already collapse into the single "zdc"
    ARTCC group, which would hide them from each other. Rather than
    contort airports into pretending to be ARTCCs, pass sector_override
    to use it DIRECTLY as both the escalation-classification sector name
    (bypassing resolve_sector(facility_or_center) entirely) and the zone
    topic name (f"{family}-{sector_override.lower()}", bypassing
    sector_ntfy_topic() entirely) when zone_split is True. This generalizes
    to any future feed with a non-ARTCC natural grouping without adding a
    second zone-lookup table to keep in sync.

    Intended as the single entry point for any feed adopting the
    tbfm/tfms/itws/aim_fns/fdps/fids/stdds -alerts family pattern -- callers
    should prefer this over calling maybe_fire_coalesced_alert() directly
    with a hand-built topic string, so the naming stays consistent as more
    feeds are wired in.
    """
    sector = sector_override if sector_override else resolve_sector(facility_or_center)
    classification = record_event(sector, feed_name, isolate=isolate)
    classification["fired"] = False
    classification["zone_fired"] = False

    if classification["sector_silenced"] or classification["feed_silenced"]:
        return classification
    if escalating_only and not classification["escalating"]:
        return classification

    if classification["escalating"]:
        priority = min(5, base_priority + 1)
        display_title = f"[ESCALATING/{sector}] {title}"
    else:
        priority = base_priority
        display_title = f"[{sector}] {title}"

    aggregate_topic = f"{family}-alerts"
    if _throttle_allows(aggregate_topic):
        agg_title, agg_detail = display_title, detail
        if is_topic_sanitize(aggregate_topic):
            agg_title = _sanitize_text(agg_title)
            agg_detail = _sanitize_text(agg_detail)
        try:
            from shared.watchlist import _fire_ntfy_dual
            _fire_ntfy_dual(aggregate_topic, agg_title, agg_detail, dispatch, priority=priority)
            classification["fired"] = True
            _record_fire(aggregate_topic)
        except Exception as e:
            log.error("sector_coalesce: aggregate fire failed for family=%s feed=%s: %s",
                      family, feed_name, e)

    if not zone_split:
        zone_topic = None
    elif sector_override:
        zone_topic = f"{family}-{sector_override.lower()}"
    else:
        zone_topic = sector_ntfy_topic(facility_or_center, family=family)
    if zone_topic and _throttle_allows(zone_topic):
        zone_title, zone_detail = display_title, detail
        if is_topic_sanitize(zone_topic):
            zone_title = _sanitize_text(zone_title)
            zone_detail = _sanitize_text(zone_detail)
        try:
            from shared.watchlist import _fire_ntfy_dual
            _fire_ntfy_dual(zone_topic, zone_title, zone_detail, dispatch, priority=priority)
            classification["zone_fired"] = True
            _record_fire(zone_topic)
        except Exception as e:
            log.error("sector_coalesce: zone fire failed for topic=%s feed=%s: %s",
                      zone_topic, feed_name, e)

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
