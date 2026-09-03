"""
src/shared/watchlist.py

Shared watchlist management. Used by both the ingest container
(FDPS/STDDS event matching) and the poller (Amtrak, REST-polled flight data).

ntfy topic routing (canonical):
  Flights:  fire "flight-alerts" + "dispatch" simultaneously
  Trains:   fire "train-alerts"  + "dispatch" simultaneously
  Both:     domain topic = full detail; dispatch = concise bottom line
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait as futures_wait
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Literal

import requests

from common import db
from common.acars import get_latest_phase as _acars_phase
from common.push_dedup import PushDedup, content_hash

log = logging.getLogger("shared.watchlist")

PERMANENT_WATCHLIST_DIR = Path("/opt/corporatetraveldc/watchlists")
NTFY_BASE = os.environ.get("NTFY_URL", "http://host.containers.internal:2586")
NTFY_USER = os.environ.get("NTFY_USER", "")
NTFY_PASS = os.environ.get("NTFY_PASS", "")
NTFY_TOKEN = os.environ.get("NTFY_TOKEN", "")

# "drone" added 2026-08-30 (night pass): Part 107 UAS tracked via Remote ID
# (identifier = the broadcast UAS ID -- serial number or session ID), the
# entry type src/utm_watcher/utm_watcher.py syncs against, mirroring how
# acars_watcher syncs entry_type=="flight". Deliberately does NOT
# participate in the 5-phase OOOI machine -- see common/db.py's
# update_watchlist_uas_phase() for the collapsed launched/landed status
# columns (dedicated-column pattern, same as TBFM's last_tbfm_status).
EntryType = Literal["flight", "train", "vessel", "drone"]

# 2026-08-27 (operator directive: "everything is meant to be local" --
# reinforced a second time after this box hit a live 429 from
# api.airplanes.live while already under load): every live-position/
# identity lookup in this module now comes from either (a) this box's own
# local ADS-B receiver via ultrafeeder's local aircraft.json, or (b) FAA
# SWIM data already ingested and stored locally (FDPS, via
# common.db.get_flight_plan_by_callsign -- see that function and
# common/db.py's _find_flight_element/_extract_aircraft_* for the
# per-flight-scoped extraction). No third-party API is ever queried for
# position/identity data. The globe.airplanes.live/?icao= tracking-URL
# links fired in notifications below are kept -- those are just a
# click-through convenience link for the operator's own phone, not a
# lookup this box performs.
# Base only (host:port, no path) -- matches ingest/local_airspace.py and
# pusher/main.py's existing convention for this same env var, not
# runner/main.py's outlier full-path default.
ULTRAFEEDER_BASE = os.environ.get("ULTRAFEEDER_URL", "http://100.x.x.x:8080").rstrip("/")

_ntfy_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="ntfy")

# Watchlist event dedup -- FORWARD-ONLY as of the 2026-09-03 push_dedup
# redesign: an event fires when its (per-sub-identity) content hash first
# appears or genuinely changes, and an unchanged rebroadcast stays
# suppressed indefinitely (up to PushDedup's retention horizon), NOT just
# for a 5-minute window. The old 300s window was the last leg of the
# UAL1573/UAL1369 duplicate-TMI saga: the 2026-07-21/22 content-hash +
# timestamp-bucketing + per-fca-key fixes below correctly made unchanged
# rebroadcasts hash identically, but should_push() still re-fired purely
# because the window had elapsed -- and TFMS rebroadcasts an active,
# unchanged TMI assignment every ~5-8 min as routine SWIM chatter, which
# straddles 300s, so UAL1369's three concurrent constraints each re-paged
# 7 times in 42 minutes (2026-09-02, byte-identical event_detail rows in
# watchlist_history). dedup_secs is retained only as PushDedup's periodic/
# retention parameter; it no longer forces a re-fire.
# Persisted (not in-memory) — survives container restarts and is shared between
# the ingest container and the poller, which both call watchlist_event_hit() for
# the same entities via different paths (push-primary vs. REST fallback). An
# in-memory-only cache would let both fire independently during handoff windows
# or after either process restarts.
_DEDUP_WINDOW_SECS = 300
_watchlist_dedup = PushDedup("watchlist-event", dedup_secs=_DEDUP_WINDOW_SECS)

# Idempotency guard added 2026-07-21 -- narrowly targets the 401/403
# false-negative pattern documented in _fire_ntfy_dual()'s docstring below:
# ntfy intermittently returns 401/403 on this path while its own
# messages_published counter climbs healthily through the same window,
# meaning the request almost certainly reached the server and was
# published despite the client-visible error. The old retry loop treated
# every exception (including these) as "did not deliver" and resent the
# identical message, which is how a single takeoff event ended up firing
# "OFF -- airborne" 14 times in 90 minutes. This guard is content-hash +
# short-TTL (90s -- comfortably covers the 0.5s/1s retry backoff plus
# network round-trip, short enough not to interfere with the unrelated
# 5-minute _watchlist_dedup window above) and is checked ONLY on 401/403,
# not on genuine failure signals (timeouts, connection errors, 5xx),
# which still retry-and-resend exactly as before since those really do
# mean the message didn't get through.
# retention_secs pinned to the old 10x-TTL eviction: this is a pure TTL
# guard (key == content hash, so forward-only semantics would never apply)
# and its state has zero value minutes after the retry window closes --
# checked via should_push_periodic() below, which keeps the exact
# pre-2026-09-03 TTL behavior.
_NTFY_AMBIGUOUS_STATUS_TTL_SECS = 90
_ntfy_ambiguous_dedup = PushDedup("ntfy-ambiguous-status",
                                  dedup_secs=_NTFY_AMBIGUOUS_STATUS_TTL_SECS,
                                  retention_secs=_NTFY_AMBIGUOUS_STATUS_TTL_SECS * 10)


# 2026-07-22: content-aware dedup. Previously ck was content_hash(event_type)
# -- a CONSTANT per trigger type -- so this was a pure blind timer: any
# tfms_tmi (or any other trigger) message that arrived after the 5-minute
# window elapsed re-fired a push no matter what it said. Root-caused via
# UAL1573's IAD_ZID/IAD_OUT TMI churn: TFMS continuously retransmits the
# SAME flow-constraint assignment (identical fca_id) with its boundary-
# crossing ETA nudged forward a minute or two each time -- completely
# routine SWIM chatter, not a real reassignment -- and every SWIM parser
# (tfms, fdps, ACARS/local_airspace) funnels through this same function,
# so any handler that fires on routine/refresh traffic showed the same
# push every ~5-15 min forever pattern.
#
# Fix: hash the actual event payload instead of the constant trigger name,
# with ISO-8601 timestamp values coarse-bucketed (_TS_BUCKET_MINUTES) so
# continuous small ETA/boundary-time refinement still hashes identically
# and gets suppressed, while a genuine identity or value change (different
# fca_id, a real reassignment, a schedule change beyond the bucket width)
# changes the hash and pushes immediately even inside the window.
_TS_RE = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?')
_TS_BUCKET_MINUTES = 10

# 2026-09-03 (forward-only dedup redesign, content-hash quality audit):
# per-trigger continuous-telemetry keys excluded from the dedup hash.
# Timestamp bucketing above already stops ISO-time churn from reading as
# "changed content", but several triggers spread raw position telemetry
# into event_detail (fdps TH handlers do {**parsed, ...}), so
# latitude/longitude/altitude/speed/distance churned the hash on every
# single position update and the dedup never suppressed anything -- under
# the old semantics OR the new. These triggers are episode notifications
# ("getting close" / "in local range"): the aircraft's exact coordinates
# are incidental, so they're dropped from the HASH only (event_detail
# itself, and what lands in watchlist_history, is untouched). Deliberately
# per-trigger, NOT global: vessel_position's meaningful content IS its
# lat/lon/speed (a moving vessel should re-fire), so a blanket drop would
# silence real movement updates.
_PROX_TELEMETRY_KEYS = frozenset({
    "latitude", "longitude", "altitude_ft", "ground_speed", "dist_nm",
})
_TRIGGER_NOISE_KEYS: dict[str, frozenset[str]] = {
    "fdps_th_approach": _PROX_TELEMETRY_KEYS,
    "fdps_th_meterfix_approach": _PROX_TELEMETRY_KEYS,
    "tfms_track_approach": frozenset({"latitude", "longitude", "minutes_out"}),
    "watchlist_proximity": frozenset({"distance_nm", "altitude_ft"}),
}
# Raw message payloads are dropped from the hash for EVERY trigger:
# fdps_parser's TH/FH/CL handlers spread {**parsed} into event_detail, and
# parsed carries raw_xml -- the whole broadcast message, unique per
# transmission -- so any dedup hash that includes it can never match twice
# and the dedup silently never suppressed those triggers at all. Raw
# payloads are provenance, not alert content.
_ALWAYS_NOISE_KEYS = frozenset({"raw_xml", "raw_json"})


def _bucket_timestamp(value: str, bucket_minutes: int = _TS_BUCKET_MINUTES) -> str:
    """Round an ISO-8601 timestamp string to a coarse bucket so continuous
    small refinements (e.g. TFMS re-sending the same constraint with its
    ETA ticked forward a minute or two) hash identically, while a genuine
    jump still changes the hash."""
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    epoch_min = int(dt.timestamp() // 60)
    bucket = epoch_min - (epoch_min % bucket_minutes)
    return str(bucket)


def _normalize_detail_for_hash(event_detail: dict) -> dict:
    """Drop the redundant trigger key plus any per-trigger continuous
    telemetry (see _TRIGGER_NOISE_KEYS), and coarse-bucket any ISO-8601
    timestamp values so continuous ETA/boundary-time refinement doesn't
    read as a new event -- only genuine identity/value changes do."""
    trigger = (event_detail or {}).get("watchlist_trigger", "")
    noise_keys = _TRIGGER_NOISE_KEYS.get(trigger, frozenset()) | _ALWAYS_NOISE_KEYS
    out = {}
    for k, v in sorted((event_detail or {}).items()):
        if k == "watchlist_trigger" or k in noise_keys:
            continue
        if isinstance(v, str) and _TS_RE.match(v):
            out[k] = _bucket_timestamp(v)
        else:
            out[k] = v
    return out


def _check_dedup(entry_id: str, event_type: str, event_detail: dict) -> bool:
    """Return True if we should suppress (already fired with this content).
    Content-aware and forward-only (2026-09-03): same entry_id +
    event_type + sub-identity + same normalized payload stays suppressed
    for as long as PushDedup remembers it; only a real content change
    pushes again -- elapsed time alone never does.

    2026-07-22 follow-up fix: a single flight can carry MULTIPLE concurrent
    tfms_tmi assignments at once (e.g. IAD_ZID + IAD_OUT + -ZOB all active
    simultaneously -- confirmed real via UAL357/UAL1573 watchlist_history,
    these are genuinely distinct flow-constraint areas, not duplicates).
    The dedup key was previously just entry_id:event_type -- ONE slot per
    trigger TYPE, shared across every distinct fca. Since the three fca's
    round-robin through that single slot, each one always looks "different
    from whatever was stored last" (because the last write was a *different*
    fca), so NONE of them ever actually got suppressed even after the
    2026-07-21 content-hash fix -- that fix was necessary but not
    sufficient. Now folds a stable per-constraint identity (fca_id, falling
    back to fca_name) into the key itself so each concurrent constraint
    gets its own independent dedup slot and history."""
    sub_id = event_detail.get("fca_id") or event_detail.get("fca_name") or ""
    key = f"{entry_id}:{event_type}:{sub_id}" if sub_id else f"{entry_id}:{event_type}"
    normalized = _normalize_detail_for_hash(event_detail)
    ck = content_hash(json.dumps(normalized, sort_keys=True, default=str))
    if _watchlist_dedup.should_push(key, ck):
        _watchlist_dedup.record(key, ck)
        return False
    return True


def get_active_entries(entry_type: EntryType | None = None) -> list[dict]:
    """Return all non-expired watchlist entries from DB."""
    return db.get_watchlist_entries(entry_type=entry_type)


_REGISTRY_STALENESS_DAYS = 30  # FAA refreshes weekly, OpenSky ~monthly -- generous margin either way


def _local_registry_hex_lookup(ident_clean: str) -> str | None:
    """Try the local FAA (US N-numbers) then OpenSky (any registration)
    registry tables for a hex mapping, skipping either source if its own
    last_full_import is older than _REGISTRY_STALENESS_DAYS. Never raises --
    best-effort, not a hard dependency. Moved here from poller/main.py
    2026-08-22 alongside resolve_flight_identity below -- see that
    function's docstring for why."""
    from datetime import datetime, timezone

    def _is_fresh(meta_get) -> bool:
        try:
            raw = meta_get("last_full_import")
            if not raw:
                return False
            last = datetime.fromisoformat(raw)
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            age_days = (datetime.now(timezone.utc) - last).total_seconds() / 86400
            return age_days <= _REGISTRY_STALENESS_DAYS
        except Exception:
            return False

    try:
        if _is_fresh(db.faa_registry_meta_get):
            row = db.faa_lookup_by_n_number(ident_clean)
            if row and row.get("mode_s_hex"):
                return row["mode_s_hex"].lower()
    except Exception as e:
        log.debug("local FAA registry lookup for %s failed (non-fatal): %s", ident_clean, e)

    try:
        if _is_fresh(db.opensky_registry_meta_get):
            row = db.opensky_lookup_by_registration(ident_clean)
            if row and row.get("icao24"):
                return row["icao24"].lower()
    except Exception as e:
        log.debug("local OpenSky registry lookup for %s failed (non-fatal): %s", ident_clean, e)

    return None


_ULTRAFEEDER_TAILNET_FALLBACK = "http://100.x.x.x:8080"


def _fetch_local_adsb() -> list:
    """One fetch of this box's own ADS-B receiver (ultrafeeder/readsb local
    aircraft.json -- same field schema as airplanes.live's `ac` records:
    hex/flight/lat/lon/alt_baro/gs/track/squawk/r) -- never a third-party
    call. Never raises.

    2026-08-27: `host.containers.internal` (this env var's value in at
    least the poller container's shared dispatch.env) is unreachable
    (connection refused) from containers using this box's `pasta`
    network mode, even though the same host:port over the tailnet IP
    (100.x.x.x) answers fine -- confirmed live. Rather than touch the
    shared env file (used by many other containers/network modes where it
    may be correct), fall back to the known-working tailnet address on any
    connection failure against the configured base."""
    for base in (ULTRAFEEDER_BASE, _ULTRAFEEDER_TAILNET_FALLBACK):
        try:
            resp = requests.get(f"{base}/data/aircraft.json", timeout=5)
            resp.raise_for_status()
            return resp.json().get("aircraft") or []
        except Exception as e:
            log.debug("local ADS-B fetch via %s failed (non-fatal): %s", base, e)
        if base == ULTRAFEEDER_BASE == _ULTRAFEEDER_TAILNET_FALLBACK:
            break
    return []


def _local_ac_by_hex(hex_code: str) -> dict | None:
    hex_code = hex_code.lower().strip()
    for ac in _fetch_local_adsb():
        if (ac.get("hex") or "").lower().strip() == hex_code:
            return ac
    return None


def _local_ac_by_callsign(callsign: str) -> dict | None:
    callsign = callsign.upper().strip()
    for ac in _fetch_local_adsb():
        if (ac.get("flight") or "").strip().upper() == callsign:
            return ac
    return None


def _local_fdps_ac(callsign: str) -> dict | None:
    """FDPS-backed fallback when local ADS-B has no contact (out of local
    receiver range) -- SWIM data already ingested and stored locally, see
    common.db.get_flight_plan_by_callsign()/_find_flight_element() for the
    per-flight-scoped extraction this depends on. Returns an ac-shaped dict
    (hex/r/lat/lon/alt_baro/gs/dst) so callers need no branching on which
    local source actually answered, or None if FDPS has nothing for this
    callsign or nothing beyond a bare flight plan (no hex/position yet)."""
    try:
        plan = db.get_flight_plan_by_callsign(callsign)
    except Exception as e:
        log.debug("local FDPS lookup for %s failed (non-fatal): %s", callsign, e)
        return None
    if not plan or not plan.get("hex"):
        return None
    return {
        "hex": plan["hex"].lower(),
        "r": plan.get("registration"),
        "lat": plan.get("position_lat"),
        "lon": plan.get("position_lon"),
        "alt_baro": plan.get("altitude_ft"),
        "gs": plan.get("ground_speed_kt"),
        "dst": plan.get("destination"),
    }


def resolve_flight_identity(entry: dict, ident: str, source: str = "sweep") -> dict | None:
    """Resolve hex_id/registration for a flight watchlist entry from LOCAL
    sources only (this box's own ADS-B receiver, then already-ingested FDPS
    SWIM data -- see _fetch_local_adsb/_local_fdps_ac above; no third-party
    API is ever queried), hex-locking the entry (db.set_watchlist_identity)
    on first live contact and firing a watchlist_event_hit notification with
    the resolved hex/tail + a click-through airplanes.live tracking URL (a
    convenience link for the operator's own phone, not a lookup this box
    performs) -- but only the FIRST time this entry gets a hex, never on
    repeat calls against an already-resolved entry.

    Extracted 2026-08-22 from poller's _check_flight_airplanes_live (that
    function's identity-resolution portion, unchanged in behavior) so
    ingest-side TFMS OUT-transition handling (ingest/parsers/tfms_parser.py)
    can force an immediate resolve attempt the moment a flight pushes back,
    instead of waiting up to poller's own FLIGHT_SWEEP_INTERVAL (120s) --
    operator directive: "the second the OUT gets pushed, it forces a
    resolve." Both callers share this one resolution path (and its
    notification), so the false-positive protections baked into the
    lookup-priority order below (hex-authoritative-once-known, callsign
    only during bootstrap) live in exactly one place, not two.

    2026-08-27: rewired off airplanes.live entirely (operator directive,
    "everything is meant to be local") -- was a live third-party HTTP call
    per lookup; now two local reads (ADS-B JSON, already-ingested FDPS).
    Local ADS-B only has range for aircraft near this box; FDPS covers the
    whole NAS but doesn't always carry a hex/position (see common/db.py's
    _extract_aircraft_hex_registration/_extract_aircraft_position
    docstrings) -- a flight genuinely out of range of both sources returns
    None here, same as airplanes.live returning no contact used to.

    `source` is a free-text tag recorded in the fired notification's detail
    (e.g. "tfms_out" vs "sweep") -- purely informational, does not change
    matching behavior.

    Returns an ac-shaped dict (hex/r/alt_baro/gs/lat/lon/dst -- see
    _local_fdps_ac's docstring for the exact shape all local sources are
    normalized to), PLUS an injected "_resolved_via_hex" bool key (see the
    resolved_via_hex comment below -- callers doing an identity-mismatch
    check must pop/check this before comparing observed vs. expected hex),
    if a live contact was found, else None. Never raises -- best-effort,
    same discipline as _local_registry_hex_lookup; callers that need the
    entry's identity fields after calling this should re-read the entry or
    use the returned hex/reg directly rather than assuming this always
    succeeds."""
    import re as _re

    # Resolve ICAO hex: explicit hex identifier > callsign > known hex
    # (structured hex_id column, then legacy notes text). See the docstring
    # above -- this ordering is deliberate and incident-tested, do not
    # reorder without reading poller/main.py's original
    # _check_flight_airplanes_live history (2026-07-23, 2026-08-13
    # comments) first.
    expected_hex = (entry.get("hex_id") or "").lower().strip() or None
    notes_hex: str | None = None
    m = _re.search(r'\bHex:\s*([0-9a-fA-F]{6})\b', entry.get("notes") or "", _re.IGNORECASE)
    if m:
        notes_hex = m.group(1).lower()

    # resolved_via_hex tracks when a hex-keyed lookup was actually used
    # (bare-hex identifier, or callsign not broadcasting so a known hex was
    # queried directly) -- callers doing an identity-mismatch check (observed
    # hex != expected hex) must skip that comparison when this is True, since
    # querying BY the expected hex makes such a comparison tautological.
    resolved_via_hex = False
    callsign_live_confirmed = False

    # 2026-08-27: a 6-character flight identifier composed only of letters
    # a-f and digits (IATA/ICAO carrier code + flight number, e.g. "AA5265")
    # is ALSO a syntactically valid Mode-S hex address by pure coincidence
    # -- confirmed live, AA5265 (American 5265, PHL-DCA) got hex-locked to
    # whatever unrelated airframe actually carries Mode-S address AA5265,
    # because the bare-hex fast path below fired before callsign resolution
    # ever got a chance to run. Exclude identifiers with the standard
    # callsign shape (2-3 leading letters, then 1-4 digits, optional
    # trailing letter) from the bare-hex fast path -- a genuine bare-hex
    # identifier (entered directly with no known flight number) essentially
    # never has this exact shape, so this only changes behavior for the
    # ambiguous collision case, not real hex lookups (e.g. "A835F2").
    _looks_like_callsign = bool(_re.fullmatch(r'[A-Za-z]{2,3}\d{1,4}[A-Za-z]?', ident))
    ident_clean = ident.upper().replace(" ", "")
    if _re.fullmatch(r'[0-9a-f]{6}', ident.lower()) and not _looks_like_callsign:
        ac = _local_ac_by_hex(ident.lower())
        resolved_via_hex = True
    elif expected_hex:
        # 2026-08-28 (operator directive, guardrail against a confirmed
        # incident -- see common/db.py SCHEMA_V39's docstring for the full
        # writeup): querying BY expected_hex here, unconditionally, is
        # exactly the bug. Once ANY hex is set this way, resolved_via_hex
        # is always True, which is precisely what the identity-mismatch
        # check below is gated to SKIP -- so a hex that was never actually
        # right (operator typo, stale registry data) could never be
        # independently caught by a later real local sighting. An
        # operator-supplied hex that hasn't yet been corroborated by an
        # actual local CALLSIGN match gets one here: same discovery path
        # as the bootstrap branch below, so a genuine match marks it
        # corroborated, and a genuine mismatch flows into the SAME
        # identity_mismatch alert an auto-resolved flight would get
        # (resolved_via_hex stays False on this path, deliberately, so
        # that check isn't skipped).
        needs_corroboration = (
            entry.get("hex_source") == "operator"
            and not entry.get("hex_corroborated_at")
        )
        if needs_corroboration:
            ac = _local_ac_by_callsign(ident_clean) or _local_fdps_ac(ident_clean)
            if ac:
                observed_hex = (ac.get("hex") or "").lower().strip()
                if observed_hex == expected_hex:
                    try:
                        db.mark_watchlist_hex_corroborated(entry["id"])
                    except Exception as e:
                        log.debug("%s: hex corroboration mark failed (non-fatal): %s", ident, e)
                # else: leave resolved_via_hex False -- downstream
                # identity-mismatch check handles a genuine disagreement.
        else:
            ac = _local_ac_by_hex(expected_hex)
            resolved_via_hex = True
    else:
        # Bootstrap phase only -- no confirmed hex exists yet, callsign is
        # the only way to discover one.
        ac = _local_ac_by_callsign(ident_clean)
        if ac:
            callsign_live_confirmed = True
        if not ac and notes_hex:
            ac = _local_ac_by_hex(notes_hex)
            resolved_via_hex = True
        if not ac:
            local_hex = _local_registry_hex_lookup(ident_clean)
            if local_hex:
                ac = _local_ac_by_hex(local_hex)
                resolved_via_hex = True
        if not ac:
            # Local ADS-B has nothing (likely out of receiver range) --
            # fall back to already-ingested FDPS SWIM data, still local.
            ac = _local_fdps_ac(ident_clean)
            if ac:
                callsign_live_confirmed = True
    ac_list = [ac] if ac else []

    if not ac_list:
        return None

    ac = ac_list[0]
    ac["_resolved_via_hex"] = resolved_via_hex
    hex_id = (ac.get("hex") or "").lower().strip()
    reg = ac.get("r") or ""

    # Auto hex-lock + notify: first genuine live contact under the callsign
    # (as opposed to a reg-fallback guess) permanently anchors this entry
    # to that hex for every future lookup, AND fires the "identity
    # resolved" push -- entries created without a confirmed hex_id (the
    # common, correct case when the operating tail is still unconfirmed at
    # add-time) graduate to hex-locked automatically the moment we
    # actually see the aircraft.
    if callsign_live_confirmed and hex_id and not expected_hex:
        try:
            db.set_watchlist_identity(entry["id"], hex_id=hex_id, registration=reg or None)
            log.info("%s: hex-locked to %s (%s) on first live contact (source=%s)",
                     ident, hex_id, reg or "no reg", source)
            tracking_url = f"https://globe.airplanes.live/?icao={hex_id}"
            reg_str = f" ({reg})" if reg else ""
            watchlist_event_hit(
                entry["id"],
                f"Identity resolved: hex {hex_id}{reg_str}",
                {
                    "watchlist_trigger": "identity_resolved",
                    "hex_id": hex_id,
                    "registration": reg or None,
                    "tracking_url": tracking_url,
                    "source": source,
                },
                priority=3,
            )
        except Exception as e:
            log.warning("%s: hex-lock/notify failed: %s", ident, e)

    return ac


def extend_auto_remove_for_delay(entry: dict, actual_off_iso: str,
                                 original_departure_iso: str | None,
                                 original_arrival_iso: str | None) -> None:
    """Operator directive 2026-08-23: the moment a watchlisted flight goes
    airborne, extend its auto_remove_at by however late it actually
    departed versus its real published schedule -- a flight that pushed
    back 2h late still needs ~2h more runway on the back end before its
    now-later actual arrival, not just the estimate computed at add-time.
    Example the operator gave: scheduled departure 14:00, scheduled
    arrival 18:30 (auto_remove_at = 18:30+6h = 00:30). Actual OFF at
    16:00 (2h late) -> auto_remove_at extends by 2h to 02:30, i.e. 8h
    past the *originally* scheduled arrival, not the delayed one --
    deliberately: the base (scheduled_arrival+6h) already covers the
    normal case, this only adds back the specific overrun a real delay
    introduces.

    Called from ingest/parsers/tfms_parser.py::_handle_flight_times() the
    moment airlineOffTime is newly present on an OOOI update -- same
    "fires off a real ingest-side event, best-effort, never blocks"
    discipline as resolve_flight_identity() above.

    Two things happen here, in order, both covered by ONE once-only guard
    (db.extend_watchlist_auto_remove_for_delay()'s
    `departure_delay_min IS NULL`, since the same airlineOffTime can be
    resent on a later TFMS message):

    1. If scheduled_arrival wasn't known at add-time (most same-day adds
       -- see _default_auto_remove_at() in web/routes/watchlist.py, which
       falls back to added_at+24h when it isn't), and TFMS's
       originalArrival is now known, recompute auto_remove_at onto the
       real scheduled_arrival+6h basis first. Skipping this would extend
       off the wrong (arbitrary added-time) base.
    2. If original_departure is known and the real OFF time is later
       than it, add that delay on top. Never shrinks the window -- an
       early departure isn't grounds for cutting it short.

    delay_min may legitimately be 0 (on-time or early) -- still persisted
    (not skipped) so a resent OOOI message is recognized as already
    processed rather than silently doing nothing forever.
    Best-effort throughout: never raises into the caller."""
    if entry.get("departure_delay_min") is not None:
        return  # already applied once for this entry

    auto_remove_at = entry.get("auto_remove_at")

    # Step 1: correct the base off the added-time fallback, if this entry
    # never had a scheduled_arrival at add-time and TFMS now supplies one.
    if not entry.get("scheduled_arrival") and original_arrival_iso:
        try:
            base = datetime.fromisoformat(original_arrival_iso.replace("Z", "+00:00"))
            auto_remove_at = (base + timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            pass

    if not auto_remove_at:
        return  # nothing to extend from

    # Step 2: add the real departure delay, if any, on top of that base.
    delay_min = 0
    if original_departure_iso:
        try:
            actual = datetime.fromisoformat(actual_off_iso.replace("Z", "+00:00"))
            scheduled = datetime.fromisoformat(original_departure_iso.replace("Z", "+00:00"))
            delay_min = max(0, int((actual - scheduled).total_seconds() // 60))
        except ValueError:
            delay_min = 0

    new_expiry = auto_remove_at
    if delay_min > 0:
        try:
            current = datetime.fromisoformat(auto_remove_at.replace("Z", "+00:00"))
            new_expiry = (current + timedelta(minutes=delay_min)).strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            new_expiry = auto_remove_at

    try:
        applied = db.extend_watchlist_auto_remove_for_delay(
            entry["id"], new_expiry, delay_min,
            scheduled_departure=original_departure_iso,
            scheduled_arrival=original_arrival_iso,
        )
        if applied:
            log.info(
                "watchlist: %s auto_remove_at -> %s (departure delay=%dmin)",
                entry.get("identifier"), new_expiry, delay_min,
            )
    except Exception as e:
        log.error("watchlist: extend_auto_remove_for_delay failed for %s: %s",
                  entry.get("id"), e)


def watchlist_event_hit(entry_id: str, event_summary: str,
                        event_detail: dict,
                        priority: int = 3) -> None:
    """
    Called when a watched entity has a status event.
    Fires dual ntfy push (domain topic + dispatch) and writes to watchlist_history.
    Deduplicates forward-only: the same entry_id + event_type +
    sub-identity with unchanged (normalized) content will not fire again;
    a genuine content change fires immediately (see _check_dedup).
    """
    entries = db.get_watchlist_entries()
    entry = next((e for e in entries if e["id"] == entry_id), None)
    if entry is None:
        log.warning("watchlist_event_hit: entry %s not found", entry_id)
        return

    event_type = event_detail.get("watchlist_trigger", "status_change")
    if _check_dedup(entry_id, event_type, event_detail):
        log.debug("watchlist dedup suppressed: %s / %s", entry_id, event_type)
        return

    fired_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    ident = entry["identifier"]
    etype = entry["entry_type"]

    if etype == "flight":
        domain_topic = "flight-alerts"
        origin = entry.get("origin") or ""
        dest = entry.get("destination") or ""
        route = f"{origin}→{dest}" if origin or dest else ""
        detail_body = f"{ident} {route}\n{event_summary}"
        dispatch_body = f"Flight {ident}: {event_summary}"
        # 2026-08-22: append a click-through tracking URL when the caller
        # provided one (identity_resolved from resolve_flight_identity
        # above; any future alert type that has one) -- appended as its
        # own line in the BODY, never the title, since a raw newline in
        # the title breaks ntfy's HTTP header (see the collapse-before-
        # truncate fix a few lines below for the exact prior incident).
        tracking_url = event_detail.get("tracking_url")
        if tracking_url:
            detail_body += f"\n{tracking_url}"
            dispatch_body += f" {tracking_url}"
        title_prefix = "FLT "
    elif etype == "vessel":
        # 2026-08-11: was falling through to the `else` (train) branch --
        # a vessel position/status event fired under "train-alerts" with
        # train-shaped copy ("route_name #{MMSI}"). No dedicated name
        # column exists for vessels (see watchlist_entries schema);
        # `notes` is the closest thing populated at add-time.
        domain_topic = "vessel-alerts"
        name = entry.get("notes") or ""
        detail_body = f"{name} (MMSI {ident})\n{event_summary}" if name else f"MMSI {ident}\n{event_summary}"
        dispatch_body = f"Vessel {ident}: {event_summary}"
        title_prefix = "VSL "
    else:
        domain_topic = "train-alerts"
        route_name = entry.get("route_name") or ""
        detail_body = f"{route_name} #{ident}\n{event_summary}"
        dispatch_body = f"Train {ident}: {event_summary}"
        title_prefix = "TRN "

    # 2026-08-17: event_summary is frequently multi-line (a tracking URL
    # and/or ACARS context appended on their own \n-separated lines, see
    # the OOOI block in poller/main.py) -- slicing it to [:60] before
    # stripping newlines let an embedded \n survive into the title whenever
    # the first line was under ~60 chars, and ntfy's HTTP client correctly
    # rejects a raw LF/CR in a header value, so the send failed outright
    # (silent alert loss, root-caused live for DAL926's OFF alert: title
    # came out as "...OFF - airborne\nhttps://globe.air", 3/3 retries
    # failed with "Invalid ... character(s) in header value"). Collapse to
    # single-line BEFORE truncating, not after.
    title_summary = " ".join(event_summary.split())
    title = title_prefix + ident + ": " + title_summary[:60]

    _fire_ntfy_dual(domain_topic, title, detail_body, dispatch_body, priority)

    db.insert_watchlist_history(
        entry_id=entry_id,
        entry_type=etype,
        identifier=ident,
        event_type=event_type,
        event_summary=event_summary,
        event_detail=event_detail,
        fired_at=fired_at,
    )
    db.update_watchlist_last_event(entry_id, event_summary, fired_at)


def sweep_expired_transient(db_conn=None) -> int:
    """
    Remove transient entries where auto_remove_at < now.
    Writes "auto_expired" record to watchlist_history for each.
    Returns count removed. Called by poller every 60s.
    db_conn param accepted but unused (uses common.db connection pool).
    """
    expired = db.sweep_expired_watchlist_entries()
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for entry in expired:
        db.insert_watchlist_history(
            entry_id=entry["id"],
            entry_type=entry["entry_type"],
            identifier=entry["identifier"],
            event_type="auto_expired",
            event_summary=f"Auto-expired at {entry.get('auto_remove_at', now_iso)}",
            event_detail={"auto_remove_at": entry.get("auto_remove_at")},
            fired_at=now_iso,
        )
        log.info("watchlist: auto-expired %s %s", entry["entry_type"], entry["identifier"])
    return len(expired)


def sweep_landed_flights() -> int:
    """
    Remove transient flight entries that are confirmed landed, or confirmed
    dead-with-no-anomaly, ahead of the auto_remove_at timer.

    Operator directive 2026-07-21: "ACARS+ADSB cross check as hex is landed
    == sweep, OR both dead after 1 hr of scheduled arrival without any
    other anomalies." Landed is read from oooi_phase == 'in', which is
    already populated by the ACARS-primary/ADS-B-fallback pipeline in
    poller._check_flight_airplanes_live (ACARS checked first, ADS-B used
    when ACARS has nothing -- effectively the cross-check the directive
    asks for) and by the schedule-inference fallback for ADS-B-dark legs.
    "Dead" entries (oooi_phase never left pre_departure -- never seen on
    either source at all) are swept once genuinely stale (1hr past
    scheduled_arrival) UNLESS an anomaly (identity/registration mismatch,
    diversion) was ever logged against them -- an anomaly means a human
    should look at it, not that it should quietly vanish. Called every
    FLIGHT_SWEEP_INTERVAL tick, right after the status-check pass that
    freshens oooi_phase/last_event_summary for the same entries.
    """
    removed = 0
    now = datetime.now(timezone.utc)
    entries = db.get_watchlist_entries(entry_type="flight", tier="transient")
    for entry in entries:
        entry_id = entry["id"]
        phase = entry.get("oooi_phase")
        reason = None
        event_type = None

        if phase == "in":
            reason = "landed (oooi_phase=in, ACARS/ADS-B confirmed)"
            event_type = "auto_swept_landed"
        else:
            sched_arr = entry.get("scheduled_arrival")
            if sched_arr and phase in (None, "pre_departure"):
                try:
                    arr_dt = datetime.fromisoformat(sched_arr.replace("Z", "+00:00"))
                except ValueError:
                    arr_dt = None
                # 2026-08-16 drift audit: a zone-LESS scheduled_arrival (e.g.
                # "2026-08-16 14:00:00" -- db.py documents that callers have
                # historically sent these) parses to a NAIVE datetime, and
                # `now` (line 265) is tz-aware UTC. `now > naive` raises
                # TypeError, which was uncaught here and only trapped at the
                # poller-tick level -- so one bad entry aborted
                # sweep_landed_flights() for EVERY entry, every tick, until
                # it expired. Fail safe: a naive timestamp is unusable for
                # this comparison (we can't know if it's UTC or ET -- and
                # guessing UTC would sweep ET-sourced FIDS arrivals hours
                # early, the known premature-sweep failure mode), so treat
                # it exactly like a parse failure and skip the time-based
                # sweep for this entry rather than crash or guess.
                if arr_dt is not None and arr_dt.tzinfo is None:
                    arr_dt = None
                if arr_dt and now > arr_dt + timedelta(hours=1):
                    history = db.get_watchlist_history(entry_id=entry_id, limit=50)
                    has_anomaly = any(
                        h.get("event_type") in ("identity_mismatch",
                                                 "identity_mismatch_reg",
                                                 "diversion")
                        for h in history
                    )
                    if not has_anomaly:
                        # Operator directive 2026-07-23: don't sweep an
                        # entry as dead/canceled/diverted purely because
                        # the periodic per-tick check never happened to
                        # catch a positive OOOI hit -- do one more live
                        # ACARS aggregator query right here, at the moment
                        # of deletion, as the actual validation gate. This
                        # is what UAL2160 needed 2026-07-22 (swept "dead",
                        # never got ADS-B or ACARS contact, cause never
                        # confirmed) -- a fresh check at sweep time, not
                        # just trusting the absence of earlier signal.
                        acars_check = None
                        try:
                            acars_check = _acars_phase(
                                entry["identifier"],
                                registration=entry.get("registration"),
                            )
                        except Exception as e:
                            log.debug("sweep_landed_flights ACARS check %s: %s",
                                     entry["identifier"], e)

                        if acars_check:
                            acars_phase_found, acars_msg = acars_check
                            if acars_phase_found == "in":
                                reason = "landed (ACARS confirmed at sweep time)"
                                event_type = "auto_swept_landed"
                            else:
                                # ACARS shows real activity the periodic
                                # sweep missed -- do NOT delete. Persist
                                # the phase found and let normal tracking
                                # continue from here instead of losing the
                                # entry.
                                db.update_watchlist_oooi_phase(
                                    entry_id, acars_phase_found,
                                    now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                )
                                log.warning(
                                    "watchlist: %s NOT swept -- ACARS shows "
                                    "phase=%s at sweep time despite no prior "
                                    "oooi progression (source=%s)",
                                    entry["identifier"], acars_phase_found,
                                    acars_msg.get("_source", "ACARS"),
                                )
                        else:
                            reason = ("dead -- no ACARS/ADS-B contact "
                                      "(confirmed via live aggregator check "
                                      "at sweep time), 1hr+ past scheduled "
                                      "arrival, no anomalies")
                            event_type = "auto_swept_dead"

        if reason:
            db.delete_watchlist_entry(entry_id)
            db.insert_watchlist_history(
                entry_id=entry_id,
                entry_type="flight",
                identifier=entry["identifier"],
                event_type=event_type,
                event_summary=f"Auto-swept: {reason}",
                event_detail={"reason": reason, "oooi_phase": phase,
                              "scheduled_arrival": entry.get("scheduled_arrival")},
                fired_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            )
            log.info("watchlist: auto-swept flight %s -- %s", entry["identifier"], reason)
            removed += 1
    return removed


def _amtraker_still_live(train_number: str) -> bool:
    """Minimal live Amtraker existence check for the sweep-time dead
    validation in sweep_landed_trains() -- deliberately does not fire any
    watchlist event itself (that remains _check_train_amtraker's job on
    the normal per-tick sweep path); this only answers "does Amtraker
    currently know about this train number at all," as the last check
    before permanently deleting the entry."""
    base_url = os.environ.get("AMTRAKER_API_URL", "https://api.amtraker.com/v3")
    try:
        resp = requests.get(f"{base_url}/trains/{train_number}", timeout=15)
        if resp.status_code == 404:
            return False
        resp.raise_for_status()
        trains = resp.json()
        return bool(trains)
    except Exception as e:
        log.debug("_amtraker_still_live %s: %s", train_number, e)
        return False  # can't confirm either way -- fall through to existing dead logic


def sweep_landed_trains() -> int:
    """
    Train counterpart to sweep_landed_flights() -- same operator directive,
    applied via Amtraker status instead of ACARS/ADS-B. "Arrived" is read
    from last_event_summary, which for train entries is written ONLY by
    poller._check_train_amtraker's state text (unlike flights, no other
    event type shares that field for trains, so text matching is safe
    here). "Dead" entries (amtraker never returned a match, so
    last_event_summary was never set) are swept once 1hr+ past scheduled
    arrival, unless an anomaly was logged. Called every TRAIN_SWEEP_INTERVAL
    tick, right after the status-check pass.
    """
    removed = 0
    now = datetime.now(timezone.utc)
    entries = db.get_watchlist_entries(entry_type="train", tier="transient")
    for entry in entries:
        entry_id = entry["id"]
        last_summary = (entry.get("last_event_summary") or "").lower()
        reason = None
        event_type = None

        if "arrived" in last_summary:
            reason = "arrived (amtraker confirmed)"
            event_type = "auto_swept_landed"
        else:
            sched_arr = entry.get("scheduled_arrival")
            if sched_arr and not last_summary:
                try:
                    arr_dt = datetime.fromisoformat(sched_arr.replace("Z", "+00:00"))
                except ValueError:
                    arr_dt = None
                # 2026-08-16 drift audit: same naive-vs-aware TypeError as
                # sweep_landed_flights() -- see the full comment there. A
                # zone-less scheduled_arrival must not crash the sweep or be
                # guessed as UTC; treat it as unusable and skip.
                if arr_dt is not None and arr_dt.tzinfo is None:
                    arr_dt = None
                if arr_dt and now > arr_dt + timedelta(hours=1):
                    history = db.get_watchlist_history(entry_id=entry_id, limit=50)
                    has_anomaly = any(
                        h.get("event_type") in ("identity_mismatch",
                                                 "identity_mismatch_reg",
                                                 "diversion")
                        for h in history
                    )
                    if not has_anomaly:
                        # Operator directive 2026-07-23 (train counterpart
                        # of the ACARS re-check above): one more live
                        # Amtraker query right at sweep time before
                        # deleting, rather than trusting that
                        # last_event_summary being empty means truly dead.
                        found_live = _amtraker_still_live(entry["identifier"])
                        if found_live:
                            log.warning(
                                "watchlist: train #%s NOT swept -- Amtraker "
                                "shows live data at sweep time despite no "
                                "prior last_event_summary",
                                entry["identifier"],
                            )
                        else:
                            reason = ("dead -- no amtraker contact "
                                      "(confirmed via live re-check at sweep "
                                      "time), 1hr+ past scheduled arrival, "
                                      "no anomalies")
                            event_type = "auto_swept_dead"

        if reason:
            db.delete_watchlist_entry(entry_id)
            db.insert_watchlist_history(
                entry_id=entry_id,
                entry_type="train",
                identifier=entry["identifier"],
                event_type=event_type,
                event_summary=f"Auto-swept: {reason}",
                event_detail={"reason": reason,
                              "scheduled_arrival": entry.get("scheduled_arrival")},
                fired_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            )
            log.info("watchlist: auto-swept train #%s -- %s", entry["identifier"], reason)
            removed += 1
    return removed


_NTFY_RETRY_ATTEMPTS = 3
_NTFY_RETRY_BACKOFF_SECS = 0.5  # doubles each retry: 0.5s, 1s


def _fire_ntfy_dual(domain_topic: str, title: str, detail_body: str,
                    dispatch_body: str, priority: int) -> None:
    """
    Fire two ntfy pushes in parallel (non-blocking via thread pool):
      1. domain_topic with full detail_body
      2. "dispatch" with concise dispatch_body
    Both use the same title and priority.

    Retry added 2026-07-20: observed intermittent 403 Forbidden responses
    on this exact code path across a 90+ minute window (dozens of
    occurrences, no fixed interval, spread across many different topics
    and call sites) with NO corresponding denial/error visible in ntfy's
    own server logs for the same windows, and ntfy's messages_published
    counter climbing steadily and healthily throughout. A manual replay
    of an identical request (same token, same topic, moments after a
    logged failure) succeeded immediately. This points to a transient
    client/network-path hiccup (rootless podman port-forwarding under
    concurrent load is the leading suspect, given six SWIM feed threads
    plus the poller/pusher/web containers all potentially hitting ntfy at
    once) rather than a deterministic auth/config problem -- root cause
    not yet fully confirmed, but real alerts were silently dropping on
    every one of these occurrences with no prior retry. Mitigated here
    with a short bounded retry (3 attempts, exponential backoff starting
    at 0.5s) so a transient failure doesn't cost a real alert; still logs
    at ERROR (with the response body, previously missing) if all retries
    are exhausted, so a genuine persistent failure remains visible.
    """
    def _push(topic: str, body: str) -> None:
        url = f"{NTFY_BASE}/{topic}"
        # HTTP headers must be ASCII and single-line — strip/replace
        # non-ASCII chars, then collapse any embedded CR/LF (a raw
        # newline in a header value is rejected by requests before the
        # request ever leaves the process). Defense-in-depth: the known
        # multi-line-summary case is fixed at the caller (watchlist_event_hit),
        # but this is the actual HTTP boundary and other callers pass
        # `title` directly.
        safe_title = title.encode("ascii", "replace").decode("ascii")
        safe_title = " ".join(safe_title.split())
        headers = {
            "Content-Type": "text/plain",
            "X-Priority": str(priority),
            "X-Title": safe_title,
        }
        auth = None
        if NTFY_TOKEN:
            # Strip label suffix (token stored as "token:label" in secrets.env)
            headers["Authorization"] = f"Bearer {NTFY_TOKEN.split(':')[0]}"
        elif NTFY_USER:
            auth = (NTFY_USER, NTFY_PASS)

        idem_key = content_hash(f"{topic}|{safe_title}|{body}|{priority}")
        last_exc: Exception | None = None
        last_body: str | None = None
        backoff = _NTFY_RETRY_BACKOFF_SECS
        for attempt in range(1, _NTFY_RETRY_ATTEMPTS + 1):
            try:
                resp = requests.post(url, data=body.encode("utf-8"),
                                     headers=headers, auth=auth, timeout=10)
                resp.raise_for_status()
                if attempt > 1:
                    log.info("ntfy push OK on retry %d/%d: topic=%s priority=%d",
                             attempt, _NTFY_RETRY_ATTEMPTS, topic, priority)
                else:
                    log.debug("ntfy push OK: topic=%s priority=%d", topic, priority)
                return
            except requests.exceptions.HTTPError as e:
                status = e.response.status_code if e.response is not None else None
                if status in (401, 403):
                    # Documented false-negative pattern -- don't resend, mark
                    # as probable-delivery and stop instead of risking a
                    # confirmed duplicate for a message that likely already went out.
                    if _ntfy_ambiguous_dedup.should_push_periodic(idem_key, idem_key):
                        _ntfy_ambiguous_dedup.record(idem_key, idem_key)
                        log.warning(
                            "ntfy %s on topic=%s -- known false-negative pattern "
                            "(publish counter climbs healthily through these), "
                            "treating as delivered, NOT resending: %s",
                            status, topic, e,
                        )
                    else:
                        log.warning(
                            "ntfy %s on topic=%s -- already marked probable-delivery "
                            "within %ds window, suppressing resend",
                            status, topic, _NTFY_AMBIGUOUS_STATUS_TTL_SECS,
                        )
                    return
                last_exc = e
                last_body = getattr(getattr(e, "response", None), "text", None)
                if attempt < _NTFY_RETRY_ATTEMPTS:
                    log.warning(
                        "ntfy push attempt %d/%d failed (topic=%s): %s -- retrying in %.1fs",
                        attempt, _NTFY_RETRY_ATTEMPTS, topic, e, backoff,
                    )
                    time.sleep(backoff)
                    backoff *= 2
            except Exception as e:
                last_exc = e
                last_body = getattr(getattr(e, "response", None), "text", None)
                if attempt < _NTFY_RETRY_ATTEMPTS:
                    log.warning(
                        "ntfy push attempt %d/%d failed (topic=%s): %s -- retrying in %.1fs",
                        attempt, _NTFY_RETRY_ATTEMPTS, topic, e, backoff,
                    )
                    time.sleep(backoff)
                    backoff *= 2

        log.error("ntfy push FAILED after %d attempts: topic=%s error=%s body=%s",
                  _NTFY_RETRY_ATTEMPTS, topic, last_exc, last_body)

    f1 = _ntfy_pool.submit(_push, domain_topic, detail_body)
    f2 = _ntfy_pool.submit(_push, "dispatch", dispatch_body)
    futures_wait([f1, f2], timeout=15)


# ── Permanent watchlist file watcher ─────────────────────────────────────────

class WatchlistFileWatcher:
    """
    Reads permanent watchlist JSON files at startup and re-reads on mtime change.
    Upserts entries into watchlist_entries with tier="permanent".
    Detects removals and writes "permanent_removed" history records.
    Run by the poller — NOT by the ingest container.
    """
    POLL_INTERVAL = 60  # seconds between mtime checks

    _FILE_MAP: dict[str, str] = {
        "permanent_flights.json": "flight",
        "permanent_trains.json": "train",
        "permanent_vessels.json": "vessel",  # yachts/cruise ships, identifier=MMSI
        # 2026-08-30: Part 107 UAS, identifier = Remote ID UAS ID (serial
        # or session ID). Same hot-reload/permanent-tier semantics as the
        # other three; consumed by utm_watcher via /api/v1/watchlist.
        "permanent_drones.json": "drone",
    }

    def __init__(self) -> None:
        self._mtimes: dict[str, float] = {}
        self._loaded_ids: dict[str, set[str]] = {}  # filename → set of entry IDs

    def start(self, stop_event: threading.Event) -> None:
        """Load files immediately, then poll in background thread."""
        self._load_all()
        t = threading.Thread(target=self._poll_loop, args=(stop_event,),
                             daemon=True, name="watchlist-file-watcher")
        t.start()

    def _poll_loop(self, stop: threading.Event) -> None:
        while not stop.is_set():
            stop.wait(self.POLL_INTERVAL)
            if stop.is_set():
                break
            self._check_for_changes()

    def _check_for_changes(self) -> None:
        for filename in self._FILE_MAP:
            path = PERMANENT_WATCHLIST_DIR / filename
            try:
                mtime = path.stat().st_mtime if path.exists() else 0.0
            except OSError:
                mtime = 0.0
            if mtime != self._mtimes.get(filename, -1):
                self._load_file(filename, path)

    def _load_all(self) -> None:
        for filename in self._FILE_MAP:
            path = PERMANENT_WATCHLIST_DIR / filename
            self._load_file(filename, path)

    def _load_file(self, filename: str, path: Path) -> None:
        entry_type = self._FILE_MAP[filename]
        if not path.exists():
            log.warning("watchlist: %s not found, skipping", path)
            self._mtimes[filename] = 0.0
            return

        try:
            data = json.loads(path.read_text())
            entries = data.get("watchlist", [])
        except (json.JSONDecodeError, OSError) as e:
            log.error("watchlist: failed to parse %s: %s — keeping existing DB entries", path, e)
            return

        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = 0.0

        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        new_ids: set[str] = set()

        for raw in entries:
            entry_id = raw.get("id")
            ident = raw.get("identifier")
            if not entry_id or not ident:
                log.warning("watchlist: skipping entry missing id/identifier in %s", filename)
                continue

            new_ids.add(entry_id)
            db.upsert_watchlist_entry({
                "id": entry_id,
                "entry_type": entry_type,
                "tier": "permanent",
                "identifier": ident,
                "origin": raw.get("origin"),
                "destination": raw.get("destination"),
                "route_name": raw.get("route_name"),
                "scheduled_departure": None,
                "scheduled_arrival": None,
                "auto_remove_at": None,
                "added_at": raw.get("added", now_iso),
                "added_by": raw.get("added_by", "operator"),
                "notes": raw.get("notes"),
                "last_event_at": None,
                "last_event_summary": None,
                # subsection/show_national/show_regional/days_active/
                # sister_flight -- added 2026-07-21 alongside DB schema v19.
                # These were already present in the JSON files (from the
                # train-roster and flight day-pattern rebuild earlier this
                # session) but never threaded through to the DB until now.
                "subsection": raw.get("subsection"),
                "show_national": raw.get("show_national"),
                "show_regional": raw.get("show_regional"),
                "days_active": raw.get("days_active"),
                "sister_flight": raw.get("sister_flight"),
            })

        # Remove entries that were in the last load but are gone from the file.
        old_ids = self._loaded_ids.get(filename, set())
        removed = old_ids - new_ids
        for removed_id in removed:
            entry = db.delete_watchlist_entry(removed_id)
            if entry:
                db.insert_watchlist_history(
                    entry_id=removed_id,
                    entry_type=entry_type,
                    identifier=entry.get("identifier", removed_id),
                    event_type="permanent_removed",
                    event_summary="Removed from permanent watchlist file",
                    event_detail={"filename": filename},
                    fired_at=now_iso,
                )
                log.info("watchlist: permanent entry %s removed (not in %s)", removed_id, filename)

        self._loaded_ids[filename] = new_ids
        self._mtimes[filename] = mtime
        if new_ids:
            log.info("watchlist: loaded %d permanent %s entries from %s",
                     len(new_ids), entry_type, filename)
