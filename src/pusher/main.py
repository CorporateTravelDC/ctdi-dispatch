"""
corporatetraveldc pusher — ntfy alert sender.

Runs as a separate service. Polls DB for unnotified VIP TFRs
and fires ntfy priority 5 alerts.
Also handles test alerts from admin trigger.

ntfy topics:
  tfr-alert   — VIP/POTUS TFR — priority 5 (max)
  cps         — CPS score change — priority 3
  ops-brief   — daily/weekly brief — priority 3
  ops-health  — freshness audit / test alerts — priority 3
"""

import asyncio
import hashlib
import json
import logging
import pathlib
import signal
import time

import requests

from common import config, db
from common import ntfy_push
from common import pushover
from common.acars import check_oooi_event as _acars_oooi, get_latest_phase as _acars_phase
from common.push_dedup import PushDedup, content_hash
from poller.fetchers.metar import parse_wind_dir

log = logging.getLogger(__name__)

PUSH_INTERVAL = 30  # Check every 30 seconds.


def send_ntfy(topic: str, message: str, priority: int = 3, *, title: str) -> bool:
    """Send a push notification via ntfy. Delegates to common.ntfy_push.

    title required, no default (2026-08-11) -- matches common.ntfy_push.send's
    own hardening; every real call site here already passed one explicitly,
    this just closes off a future accidental omission. See that module's
    docstring for why (title = email Subject:, the only client-side filter
    ntfy supports)."""
    return ntfy_push.send(topic, message, title=title, priority=priority)


def send_test_alert(message: str, topic: str = "ops-health",
                    title: str | None = None, priority: int = 3) -> bool:
    """Admin-triggered test alert.
    topic/title/priority come from the request body; defaults preserve legacy behavior."""
    return send_ntfy(topic, message, priority=priority,
                     title=title or "corporatetraveldc test")



def hot_push(topic: str, message: str, title: str) -> bool:
    """Co-fire ntfy priority 5 + Pushover Emergency for any max-priority event.

    Both sends are attempted regardless of the other's outcome.
    Returns True if at least one delivery succeeded.
    """
    ntfy_ok = send_ntfy(topic, message, priority=5, title=title)
    po_ok   = pushover.send_emergency(title=title, message=message)
    return ntfy_ok or po_ok


# Dedup instances -- one per logical alert channel
_tfr_dedup   = PushDedup("tfr")
_wx_dedup    = PushDedup("wx")
_route_dedup = PushDedup("route")

# Wind-change thresholds
_WX_SPEED_THRESHOLD_KT  = 10   # alert on >= 10kt speed change
_WX_DIR_THRESHOLD_DEG   = 45   # alert on >= 45 degree direction shift
_WX_HOT_PUSH_KT         = 30   # hot push (bypass dedup) at CPS NO-GO limit

# Feed freshness gates for wx-alerts.
# If the METAR (ITWS) feed is older than this or has consecutive failures,
# suppress wx-alerts rather than pushing stale data.
_METAR_MAX_AGE_SEC      = 1200  # 20 min -- METAR feeds every ~5 min nominally
_METAR_MAX_FAILURES     = 3     # suppress after 3 consecutive fetch failures
_NWS_MAX_AGE_SEC        = 1800  # 30 min -- NWS alert poll cadence is slower


def push_vip_tfrs() -> int:
    """
    Check for VIP TFRs not yet notified. Fire ntfy priority 5 for each.
    Dedup: same TFR suppressed for 1 hour unless content changed.
    Returns count pushed.
    """
    tfrs = db.get_active_tfrs()
    pushed = 0

    for t in tfrs:
        if not t["is_vip"]:
            continue

        narrative = t["enriched_text"] or (
            f"VIP/POTUS TFR active: {t['tfr_id']}. "
            "Check dispatch for routing impact."
        )
        message = narrative[:1000]
        key = t["tfr_id"]
        h = content_hash(message)

        # 2026-08-16 drift audit: this passed hot=True ("VIP = always hot"),
        # but PushDedup's contract says hot=True BYPASSES dedup entirely --
        # should_push always returned True, so with PUSH_INTERVAL=30s an
        # active VIP TFR re-fired 2x ntfy p5 + a Pushover Emergency (siren,
        # auto-retrying) every 30 seconds for its whole active window. The
        # `hot` flag exists for callers that conditionally skip dedup (e.g.
        # wx >=30kt); "the pushes themselves are hot/priority-5" was never a
        # reason to pass it. Docstring contract ("same TFR suppressed for 1
        # hour unless content changed") is exactly plain should_push, so the
        # per-TFR slot + enrichment-text content key now actually gate:
        # first sighting fires immediately, changed enrichment fires
        # immediately, otherwise one re-push per hour while active.
        if not _tfr_dedup.should_push(key, h):
            continue

        # Hot push: ntfy (tfr-alert + hot-alerts) + Pushover Emergency co-fire.
        success = hot_push("tfr-alert", message, title=f"VIP TFR: {t['tfr_id']}")
        hot_push("hot-alerts", message, title=f"VIP TFR: {t['tfr_id']}")
        if success:
            db.mark_tfr_notified(t["tfr_id"])
            _tfr_dedup.record(key, h)
            pushed += 1

    return pushed

def push_cps_update() -> None:
    """Push CPS score if it has changed since last push."""
    cps = db.get_latest_cps()
    if not cps:
        return

    # Track last-pushed CPS in a state file to avoid duplicate pushes.
    state_path = pathlib.Path(config.state_dir()) / "pusher-last-cps.txt"
    last_label = ""
    if state_path.exists():
        last_label = state_path.read_text().strip()

    current_label = f"{cps['score']}/{cps['label']}"
    if current_label == last_label:
        return

    emoji = {"GREEN": "🟢", "YELLOW": "🟡", "RED": "🔴"}.get(cps["score"], "⚪")
    message = (
        f"{emoji} CPS: {cps['label']}\n"
        + (cps["narrative"] or "")
    )

    priority = {"GREEN": 3, "YELLOW": 4, "RED": 5}.get(cps["score"], 3)
    success = send_ntfy("cps", message, priority=priority,
                        title=f"CPS: {cps['score']}/{cps['label']}")
    if success:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(current_label)



# ---------------------------------------------------------------------------
# Flight watchlist monitor
# Sources (priority order):
#   1. Local UltraFeeder tar1090  — ULTRAFEEDER_URL/data/aircraft.json
#      Fast, local, zero rate-limit. Used when UltraFeeder container is up.
#   2. airplanes.live API         — https://api.airplanes.live/v2/callsign/
#      Free, no key, same JSON schema as adsb.lol. Used as fallback.
#   3. ACARS/VDL2 WOW confirmation — acarshub messages DB (authoritative bypass)
#      Weight-on-Wheels ON event from aircraft avionics overrides ADS-B guardrail.
# ---------------------------------------------------------------------------
AIRPLANES_LIVE_URL = "https://api.airplanes.live/v2/callsign/{callsign}"
FLIGHT_MONITOR_INTERVAL = 60  # seconds between checks per flight

# How long a flight must be absent from ALL feeds before declaring "presumed landed".
# 2 minutes was too short — high-altitude aircraft drop off ADS-B coverage routinely.
GONE_FROM_FEED_TIMEOUT_SEC = 600   # 10 minutes

# If last known altitude was above this, never fire from a feed dropout alone.
# N757AF at FL400 disappearing for 10 min is coverage loss, not a landing.
HIGH_ALT_GATE_FT = 10_000

# Must be below this altitude for a reading to count toward landing confirmation.
# 500ft is at or below established DME/ILS minimums for all US airports — any
# aircraft below this is either on final approach or on the ground.
MIN_LANDING_ALT_FT = 500

# Number of consecutive below-threshold readings required before firing "landed".
# Guards against single bad transponder messages (alt_baro=0 / "ground" at cruise).
MIN_LOW_READINGS = 3

# State: callsign -> {last_seen, airborne, notified, last_alt_ft, low_count}
_flight_state: dict = {}

# Persisted "already notified this landing" gate, independent of _flight_state.
# _flight_state["notified"] alone is NOT restart-safe: if this container restarts
# while a landed aircraft is still a live watchlist entry, _flight_state resets to
# empty and _check_flight_landing() will happily re-confirm "landed" a few poll
# cycles later (on-ground/low-alt readings persist at the gate), re-firing the
# same notification. This gate survives restarts via the shared state volume.
#
# Window depends on confirmation source: ACARS is authoritative (avionics report
# wheels-on-ground directly), so a 1h re-arm is safe. ADS-B/absence-based
# confirmation is corroborative/inferred, so it gets a longer 2h window to guard
# against a restart re-chasing stale ground/low-alt readings before they age out.
_landing_dedup_acars = PushDedup("flight-landing-acars", dedup_secs=3600)   # 1h
_landing_dedup_adsb  = PushDedup("flight-landing-adsb", dedup_secs=7200)    # 2h


def _landing_dedup_for(result: str) -> PushDedup:
    # landed_fids is poller.py's own corroborated (FIDS/FDPS/ACARS) phase --
    # same authoritative tier as landed_acars, not an ADS-B guess.
    return _landing_dedup_acars if result in ("landed_acars", "landed_fids") else _landing_dedup_adsb


def _ultrafeeder_url() -> str:
    """Return local UltraFeeder base URL, or empty string if not configured."""
    return config.get("ULTRAFEEDER_URL", "").rstrip("/")


def _fetch_aircraft_callsign(callsign: str) -> list:
    """
    Return list of matching aircraft dicts. Tries UltraFeeder first,
    then airplanes.live. Each dict has at least: alt_baro, gnd/ground.
    """
    cs = callsign.strip().upper()

    # 1 — Local UltraFeeder (tar1090 aircraft.json)
    uf_base = _ultrafeeder_url()
    if uf_base:
        try:
            r = requests.get(f"{uf_base}/data/aircraft.json", timeout=4)
            r.raise_for_status()
            all_ac = r.json().get("aircraft", [])
            matched = [
                a for a in all_ac
                if (a.get("flight", "") or "").strip().upper() == cs
            ]
            if matched:
                log.debug("%s: found via UltraFeeder (%d match)", cs, len(matched))
                return matched
            # UltraFeeder up but callsign not local — fall through to airplanes.live
            log.debug("%s: UltraFeeder up but callsign not in feed", cs)
        except Exception as e:
            log.debug("UltraFeeder fetch failed: %s", e)

    # 2 — airplanes.live
    try:
        r = requests.get(AIRPLANES_LIVE_URL.format(callsign=cs), timeout=8)
        r.raise_for_status()
        return r.json().get("ac", [])
    except Exception as e:
        log.debug("airplanes.live fetch failed for %s: %s", cs, e)
        return []


_OOOI_PHASE_STALE_SEC = 1800  # 30 min -- matches the same order-of-magnitude
                              # freshness bar poller.py's own corroboration
                              # checks use elsewhere; an old phase write is
                              # not authoritative for "confirmed landed now"


def _check_flight_landing(
    callsign: str,
    oooi_phase: str | None = None,
    oooi_phase_updated_at: str | None = None,
) -> str | None:
    """
    Returns "landed" if the aircraft is confirmed on the ground.

    Source priority:
      0. poller.py's own oooi_phase (watchlist_entries.oooi_phase) — 2026-08-13.
         poller.py already runs the full "ACARS/FDPS/FIDS/VDL IS the sole
         authority" corroboration chain (2026-07-28 directive) for every
         permanent/transient entry it sweeps -- FIDS's Landed/InGate status in
         particular confirms ON/IN independent of ACARS. This function used to
         re-derive everything from scratch using ONLY ACARS+ADS-B, meaning
         pusher never saw FIDS-confirmed landings poller had already
         established -- exactly the gap that let AS506's predecessor flight go
         un-pushed with ACARS hardware offline. Only trusted if fresh
         (_OOOI_PHASE_STALE_SEC); callers with no entry context (the
         session-based path below) simply omit these params and this check
         is skipped, unchanged from before.
      1. ACARS/VDL2 — avionics are authoritative; checked first for every call.
         - ACARS OFF  → aircraft is airborne; update state regardless of ADS-B.
         - ACARS ON/IN → aircraft is on ground; fire immediately regardless of
                         how many ADS-B readings we have seen.
      2. ADS-B — corroborating source; requires MIN_LOW_READINGS consecutive
                 readings at or below MIN_LANDING_ALT_FT before firing.
                 Not used if ACARS has already resolved the state.
    """
    cs = callsign.strip().upper()
    state = _flight_state.setdefault(cs, {
        "last_seen": 0.0,
        "airborne": False,
        "notified": False,
        "last_alt_ft": None,
        "low_count": 0,
    })

    if state["notified"]:
        return None

    # ── 0. poller.py's already-corroborated FIDS/FDPS/ACARS phase ───────────
    if oooi_phase in ("on", "in") and oooi_phase_updated_at:
        try:
            from datetime import datetime, timezone
            updated = datetime.fromisoformat(oooi_phase_updated_at.replace("Z", "+00:00"))
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=timezone.utc)
            age_sec = (datetime.now(timezone.utc) - updated).total_seconds()
            if age_sec <= _OOOI_PHASE_STALE_SEC:
                log.info(
                    "%s: poller-confirmed oooi_phase=%s (%.0fs old) -- landed, "
                    "independent of ACARS/ADS-B state below",
                    cs, oooi_phase, age_sec,
                )
                state["airborne"] = False
                state["notified"] = True
                return "landed_fids"
        except Exception as e:
            log.debug("%s: oooi_phase freshness check failed (non-fatal): %s", cs, e)

    # ── 1. ACARS is authoritative ────────────────────────────────────────────
    acars = _acars_phase(cs, not_before_epoch=state["last_seen"])
    if acars:
        phase, msg = acars
        label = msg.get("label", "?")
        msg_time = msg.get("msg_time")

        if phase in ("on", "in"):
            # Avionics confirm wheels on ground — no ADS-B corroboration needed.
            log.info(
                "%s: ACARS %s confirms on-ground — label=%s msg_time=%s",
                cs, phase.upper(), label, msg_time,
            )
            state["airborne"] = False
            state["notified"] = True
            return "landed_acars"

        if phase == "off":
            # Avionics confirm wheels up — set airborne regardless of ADS-B.
            if not state["airborne"]:
                log.info("%s: ACARS OFF confirms airborne — label=%s", cs, label)
            state["airborne"] = True
            if msg_time:
                state["last_seen"] = max(state["last_seen"], float(msg_time))
            state["low_count"] = 0
            # Fall through so ADS-B can still update last_alt_ft this cycle.

        # phase == "out" (pushback) — no state change needed here.

    # ── 2. ADS-B corroboration ───────────────────────────────────────────────
    aircraft = _fetch_aircraft_callsign(cs)

    if aircraft:
        ac = aircraft[0]
        on_ground = bool(ac.get("ground") or ac.get("gnd") or ac.get("on_ground"))
        alt_baro = ac.get("alt_baro", 99999)
        truly_airborne = (
            not on_ground
            and alt_baro != "ground"
            and isinstance(alt_baro, (int, float))
            and alt_baro > 500
        )

        if truly_airborne:
            state["airborne"] = True
            state["last_seen"] = time.time()
            state["last_alt_ft"] = int(alt_baro)
            state["low_count"] = 0
            log.debug("%s airborne alt=%s", cs, alt_baro)
            return None

        if not state["airborne"]:
            return None

        current_alt = int(alt_baro) if isinstance(alt_baro, (int, float)) else 0
        last_alt = state["last_alt_ft"]

        if last_alt is not None and last_alt > HIGH_ALT_GATE_FT and not on_ground:
            log.debug(
                "%s: non-airborne reading (alt=%s) but last alt was %dft — noise",
                cs, alt_baro, last_alt,
            )
            state["low_count"] += 1
        elif on_ground or alt_baro == "ground" or current_alt <= MIN_LANDING_ALT_FT:
            state["low_count"] += 1
        else:
            state["low_count"] = 0

        # 2026-07-28 operator directive: local DC-metro ADS-B receiver
        # coverage is not reliable enough to independently confirm landed --
        # low/ground readings are logged for visibility but no longer fire
        # "landed_adsb" on their own. ACARS (checked earlier in this
        # function) is the only source that can return a landed result here.
        if state["low_count"] >= MIN_LOW_READINGS:
            log.info(
                "%s: %d consecutive low/ground ADS-B readings (alt=%s last_alt=%s) "
                "-- ADS-B alone no longer confirms landed, awaiting ACARS",
                cs, state["low_count"], alt_baro, last_alt,
            )
        else:
            log.debug("%s: low/ground reading %d/%d — waiting for confirmation",
                      cs, state["low_count"], MIN_LOW_READINGS)
        return None

    else:
        # Aircraft absent from all ADS-B feeds. Absence is not confirmation
        # of anything -- it's exactly the "ADS-B dark" condition that was
        # producing landed pushes 15-20 min before actual arrival, most
        # often attributable to the local receiver's limited DC-metro sky
        # coverage rather than the aircraft actually being down. No longer
        # fires "landed_adsb" from a timeout; ACARS is required.
        if not state["airborne"]:
            return None
        elapsed = time.time() - state["last_seen"]
        last_alt = state["last_alt_ft"]

        if elapsed > GONE_FROM_FEED_TIMEOUT_SEC:
            log.info(
                "%s absent from feed %ds (last alt=%s) -- ADS-B-dark alone no "
                "longer confirms landed, awaiting ACARS",
                cs, int(elapsed), last_alt,
            )

    return None


def push_flight_watchlist_landings() -> int:
    """
    Check active flight watchlist sessions AND permanent watchlist entries for landings.
    Sessions (watchlist_sessions) cover transient trip-day watches.
    Entries (watchlist_entries) cover permanent flights that the poller tracks daily.
    """
    pushed = 0

    # Legacy session-based landings (transient trip watches)
    sessions = db.get_active_watchlists()
    flight_sessions = [s for s in sessions if s.get("session_type") == "flight"]
    for session in flight_sessions:
        callsign = session.get("subject", "").strip().upper()
        if not callsign:
            continue
        result = _check_flight_landing(callsign)
        if result and _landing_dedup_for(result).should_push(callsign, content_hash("landed")):
            message = f"✈️ {callsign} has landed.\nWatchlist monitoring complete."
            success = send_ntfy(
                topic="flight-alerts",
                message=message,
                priority=4,
                title=f"{callsign} — Landed",
            )
            if success:
                _landing_dedup_for(result).record(callsign, content_hash("landed"))
                try:
                    db.terminate_watchlist_session(
                        session["id"],
                        f"{callsign} landed — auto-terminated by pusher."
                    )
                except Exception as e:
                    log.warning("Could not terminate watchlist session %s: %s",
                                session["id"], e)
                pushed += 1

    # Permanent entry landings (watchlist_entries — what the poller's flight sweep tracks)
    entries = db.get_watchlist_entries(entry_type="flight")
    for entry in entries:
        callsign = entry.get("identifier", "").strip().upper()
        if not callsign:
            continue
        result = _check_flight_landing(
            callsign,
            oooi_phase=entry.get("oooi_phase"),
            oooi_phase_updated_at=entry.get("oooi_phase_updated_at"),
        )
        if result and _landing_dedup_for(result).should_push(callsign, content_hash("landed")):
            message = f"✈️ {callsign} has landed."
            success = send_ntfy(
                topic="flight-alerts",
                message=message,
                priority=4,
                title=f"{callsign} — Landed",
            )
            if success:
                _landing_dedup_for(result).record(callsign, content_hash("landed"))
                pushed += 1

    return pushed


def push_watchlist_retries() -> int:
    """
    Retry watchlist_history events where ntfy_fired=0 within the last 15 minutes.
    Covers train delay alerts and flight OOOI events that failed on first attempt
    from the poller (e.g. ntfy momentarily unreachable at event time).
    Returns count of successfully retried rows.
    """
    pending = db.get_watchlist_history_unfired(max_age_seconds=900)
    if not pending:
        return 0

    retried = 0
    for row in pending:
        etype = row.get("entry_type", "")
        ident = row.get("identifier", "")
        summary = row.get("event_summary") or ""
        priority = int(row.get("ntfy_priority") or 3)

        topic = "flight-alerts" if etype == "flight" else "train-alerts"
        prefix = "FLT " if etype == "flight" else "TRN "
        title = prefix + ident + ": " + summary[:60]
        dispatch_body = ("Flight " if etype == "flight" else "Train ") + ident + ": " + summary

        ok1 = send_ntfy(topic, summary, priority=priority, title=title)
        ok2 = send_ntfy("dispatch", dispatch_body, priority=priority, title=title)
        if ok1 or ok2:
            db.mark_watchlist_history_fired(row["id"])
            retried += 1
            log.info("pusher retry OK: %s %s (row %s)", etype, ident, row["id"])
        else:
            log.debug("pusher retry FAILED: %s %s (row %s)", etype, ident, row["id"])

    return retried


def _feed_is_fresh(feed_name: str, max_age: float, max_failures: int = 3) -> bool:
    """Return True if the named feed has a recent, non-failed row in feed_state."""
    states = {s["feed_name"]: s for s in db.get_feed_states()}
    state = states.get(feed_name)
    if state is None:
        log.warning("feed_gate: no feed_state row for %r -- treating as stale", feed_name)
        return False
    age = time.time() - (state.get("fetched_at") or 0)
    if age > max_age:
        log.warning("feed_gate: %s stale (%.0fs > %.0fs)", feed_name, age, max_age)
        return False
    failures = state.get("consecutive_failures") or 0
    if failures >= max_failures:
        log.warning("feed_gate: %s has %d consecutive failures", feed_name, failures)
        return False
    return True


# Primary DC-area stations for wind-change monitoring
_WX_STATIONS = ("KDCA", "KIAD", "KBWI")


def push_wx_change() -> bool:
    """
    Push a weather alert when wind changes meaningfully since last push.

    Thresholds (any primary station):
      >= 10kt speed delta  -> standard alert, topic "cps", priority 3
      >= 45deg direction shift -> standard alert, topic "cps", priority 3
      >= 30kt speed (CPS NO-GO limit) -> hot push, topic "hot-alerts", priority 5

    1-hour dedup on non-hot pushes. Hot push bypasses dedup entirely.
    """
    metars = db.get_metar_snapshot()
    if not metars:
        return False

    primaries = {m["station"]: m for m in metars if m["station"] in _WX_STATIONS}
    if not primaries:
        return False

    # Feed freshness gate: suppress wx-alerts when METAR source is stale/degraded.
    # METAR is the ITWS proxy (ADDS terminal weather). If it has gone stale or is
    # repeatedly failing, the wind data is unreliable -- suppress rather than push noise.
    if not _feed_is_fresh("metar", _METAR_MAX_AGE_SEC, _METAR_MAX_FAILURES):
        log.info("push_wx_change: metar feed not fresh -- suppressing alert")
        return False

    now = time.time()
    triggered = False
    hot = False
    trigger_reason = []

    for station, m in primaries.items():
        curr_speed = m.get("wind_kt") or 0
        curr_dir = parse_wind_dir(m.get("raw_metar", ""))
        last = _wx_dedup.get_raw(station)
        last_speed = last.get("wind_kt", 0) or 0
        last_dir = last.get("wind_dir_deg")  # None on first run

        # Hot push threshold -- CPS NO-GO
        if curr_speed >= _WX_HOT_PUSH_KT:
            hot = True
            triggered = True
            trigger_reason.append(f"{station} {curr_speed}kt (CPS limit)")
            continue

        # Speed delta
        if abs(curr_speed - last_speed) >= _WX_SPEED_THRESHOLD_KT:
            triggered = True
            delta = curr_speed - last_speed
            sign = "+" if delta > 0 else ""
            trigger_reason.append(f"{station} wind {sign}{delta}kt ({last_speed}->{curr_speed}kt)")
            continue

        # Direction delta (circular)
        if curr_dir is not None and last_dir is not None:
            dir_delta = min(abs(curr_dir - last_dir), 360 - abs(curr_dir - last_dir))
            if dir_delta >= _WX_DIR_THRESHOLD_DEG:
                triggered = True
                trigger_reason.append(
                    f"{station} wind shift {dir_delta}deg ({last_dir}->{curr_dir}deg)")

        # 1-hour routine update if no other trigger
        if not triggered:
            last_ts = max(
                (_wx_dedup.get_raw(s).get("ts", 0) for s in _WX_STATIONS),
                default=0
            )
            if (now - last_ts) >= 3600 and any(
                    (m.get("wind_kt") or 0) > 0 for m in primaries.values()):
                triggered = True
                trigger_reason.append("1hr routine wx update")

    if not triggered:
        return False

    # Build message
    wx_lines = []
    for st in _WX_STATIONS:
        m = primaries.get(st)
        if not m:
            continue
        speed = m.get("wind_kt", 0) or 0
        wd = parse_wind_dir(m.get("raw_metar", ""))
        dir_str = f"/{wd:03d}deg" if wd is not None else "/VRB"
        ceil_str = f"{m['ceiling_ft']}ft" if m.get("ceiling_ft") else "CLR"
        wx_lines.append(
            f"{st}: {speed}kt{dir_str} ceil={ceil_str} vis={m.get('visibility_sm','?')}SM"
        )

    reason_str = "; ".join(trigger_reason)
    message = (
        f"{'WIND ALERT' if hot else 'WX UPDATE'}: {reason_str}\n"
        + "\n".join(wx_lines)
    )
    priority = 5 if hot else 3
    topic = "hot-alerts" if hot else "wx-alerts"
    title = "Wind Alert -- CPS Threshold" if hot else "WX Change"

    # Hot wx path: co-fire Pushover Emergency on priority-5 (CPS NO-GO) alerts.
    if hot:
        success = hot_push(topic, message, title=title)
    else:
        success = send_ntfy(topic, message, priority=priority, title=title)
    if success:
        for station, m in primaries.items():
            _wx_dedup.set_raw(station, {
                "wind_kt": m.get("wind_kt") or 0,
                "wind_dir_deg": parse_wind_dir(m.get("raw_metar", "")),
                "ceiling_ft": m.get("ceiling_ft"),
                "visibility_sm": m.get("visibility_sm"),
            })
        log.info("push_wx_change: %s (hot=%s)", reason_str, hot)

    return success


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    shutdown = asyncio.Event()
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, shutdown.set)

    db.init_db_v8()
    db.init_db_v9()
    db.init_db_v16()
    db.init_db_v18()
    db.init_db_v19()
    db.init_db_v20()
    log.info("corporatetraveldc pusher started")

    while not shutdown.is_set():
        try:
            vip_count = push_vip_tfrs()
            if vip_count:
                log.info("Pushed %d VIP TFR alerts", vip_count)
            push_cps_update()
            push_wx_change()
            flight_count = push_flight_watchlist_landings()
            if flight_count:
                log.info("Pushed %d flight landing alerts", flight_count)
            retry_count = push_watchlist_retries()
            if retry_count:
                log.info("Retried %d watchlist ntfy events", retry_count)
        except Exception as e:
            log.error("Pusher loop error: %s", e)

        await asyncio.sleep(PUSH_INTERVAL)

    log.info("corporatetraveldc pusher stopped")


if __name__ == "__main__":
    asyncio.run(main())
