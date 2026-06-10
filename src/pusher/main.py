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
from common.push_dedup import PushDedup, content_hash
from poller.fetchers.metar import parse_wind_dir

log = logging.getLogger(__name__)

PUSH_INTERVAL = 30  # Check every 30 seconds.


def send_ntfy(topic: str, message: str, priority: int = 3,
              title: str = "corporatetraveldc") -> bool:
    """Send a push notification via ntfy. Delegates to common.ntfy_push."""
    return ntfy_push.send(topic, message, title=title, priority=priority)


def send_test_alert(message: str) -> bool:
    """Admin-triggered test alert. Priority 3 (default) — generates popup on phone.
    Intentionally below VIP priority (5) and high-priority alerts (4)."""
    return send_ntfy("ops-health", f"[TEST] {message}", priority=3,
                     title="corporatetraveldc test")


# Dedup instances -- one per logical alert channel
_tfr_dedup   = PushDedup("tfr")
_wx_dedup    = PushDedup("wx")
_route_dedup = PushDedup("route")

# Wind-change thresholds
_WX_SPEED_THRESHOLD_KT  = 10   # alert on >= 10kt speed change
_WX_DIR_THRESHOLD_DEG   = 45   # alert on >= 45 degree direction shift
_WX_HOT_PUSH_KT         = 30   # hot push (bypass dedup) at CPS NO-GO limit


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

        if not _tfr_dedup.should_push(key, h, hot=True):  # VIP = always hot
            continue

        success = send_ntfy(
            topic="tfr-alert",
            message=message,
            priority=5,
            title=f"VIP TFR: {t['tfr_id']}",
        )
        send_ntfy(
            topic="hot-alerts",
            message=message,
            priority=5,
            title=f"VIP TFR: {t['tfr_id']}",
        )
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
# ---------------------------------------------------------------------------
AIRPLANES_LIVE_URL = "https://api.airplanes.live/v2/callsign/{callsign}"
FLIGHT_MONITOR_INTERVAL = 60  # seconds between checks per flight

# State: callsign -> {"last_seen": timestamp, "airborne": bool, "notified": bool}
_flight_state: dict = {}


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


def _check_flight_landing(callsign: str) -> str | None:
    """
    Returns "landed" if aircraft was airborne and is now on ground or gone
    from feed; returns None otherwise.
    """
    import time
    cs = callsign.strip().upper()
    state = _flight_state.setdefault(cs, {"last_seen": 0.0, "airborne": False, "notified": False})

    if state["notified"]:
        return None

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
            log.debug("%s airborne alt=%s", cs, alt_baro)
            return None
        elif state["airborne"]:
            log.info("%s on ground — landing detected", cs)
            state["notified"] = True
            return "landed"
    else:
        # Not in feed — if was airborne and gone > 2 min, presume landed
        if state["airborne"] and (time.time() - state["last_seen"]) > 120:
            log.info("%s gone from feed after airborne — presumed landed", cs)
            state["notified"] = True
            return "landed"

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
        if result == "landed":
            message = f"✈️ {callsign} has landed.\nWatchlist monitoring complete."
            success = send_ntfy(
                topic="flight-alerts",
                message=message,
                priority=4,
                title=f"{callsign} — Landed",
            )
            if success:
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
        result = _check_flight_landing(callsign)
        if result == "landed":
            message = f"✈️ {callsign} has landed."
            success = send_ntfy(
                topic="flight-alerts",
                message=message,
                priority=4,
                title=f"{callsign} — Landed",
            )
            if success:
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
    topic = "hot-alerts" if hot else "cps"
    title = "Wind Alert -- CPS Threshold" if hot else "WX Change"

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
