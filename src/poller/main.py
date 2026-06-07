"""
corporatetraveldc poller — async scheduler.

Runs the mechanical ingest fetchers on their schedules.
Skills are invoked as separate systemd service units (not in-process)
so each skill's --force flag, SR-1 log, and SR-2 gate work independently.

Poller also watches the trigger directory for admin-issued manual refresh commands.
"""

import asyncio
import json
import logging
import pathlib
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from common import config, db
from ingest import failover
from shared.watchlist import WatchlistFileWatcher, sweep_expired_transient

log = logging.getLogger(__name__)

# ── Schedule definitions ───────────────────────────────────────────────────────
# interval_seconds: how often to run the fetcher.
# Each fetcher is also independently triggerable via trigger files.

FETCH_SCHEDULE: list[dict] = [
    {"name": "tfr",           "module": "poller.fetchers.tfr",           "interval": 300,  "push_feed": "stdds"},
    {"name": "metar",         "module": "poller.fetchers.metar",         "interval": 300},
    {"name": "nas",           "module": "poller.fetchers.nas",           "interval": 300,  "push_feed": "tfms"},
    {"name": "nws",           "module": "poller.fetchers.nws",           "interval": 300,  "push_feed": "nws"},
    {"name": "notam",         "module": "poller.fetchers.notam",         "interval": 300,  "push_feed": "fns"},
    {"name": "runsheet",      "module": "poller.fetchers.runsheet",      "interval": 300},
    {"name": "atcscc_opsplan","module": "poller.fetchers.atcscc_opsplan","interval": 3600},
]

# Skills invoked as subprocesses (own SR-1/SR-2 state, own log entries).
SKILL_SCHEDULE: list[dict] = [
    {"name": "tfr-enrichment",  "script": "poller/skills/tfr_enrichment.py",  "interval": 300},
    {"name": "route-impact",    "script": "poller/skills/route_impact.py",     "interval": 300},
    {"name": "cps-recompute",   "script": "poller/skills/cps_recompute.py",    "interval": 3600},
    {"name": "train-impact",    "script": "poller/skills/train_impact.py",
     "interval": 900, "active_interval": 300, "active_check": "train"},
    {"name": "flight-impact",   "script": "poller/skills/flight_impact.py",
     "interval": 900, "active_interval": 300, "active_check": "flight"},
]

# Daily/weekly skills are handled by systemd timers, not this scheduler.
# daily-brief: 05:00 ET
# freshness-audit: 06:00 ET
# weekly-summary: Sun 18:00 ET


# A push heartbeat fresher than this means push owns the data; poller skips REST.
# Must exceed ingest heartbeat interval (30s) with margin to avoid flapping.
FALLBACK_MAX_AGE = 90  # seconds


class FetchLoop:
    """Runs a fetcher function on a fixed interval.

    Optional active_interval + active_check: when a watchlist session of type
    active_check ('train' | 'flight') is live, the loop uses active_interval
    instead of interval. This lets ustrains and future trip-aware fetchers
    poll faster during active legs without a code change.
    """

    def __init__(self, name: str, module: str, interval: int,
                 push_feed: str | None = None,
                 active_interval: int | None = None,
                 active_check: str | None = None):
        self.name = name
        self.module_name = module
        self.interval = interval
        self.push_feed = push_feed
        self.active_interval = active_interval  # faster cadence when trip leg active
        self.active_check = active_check        # watchlist session_type: 'train'|'flight'
        self._last_run = 0.0

    def _effective_interval(self) -> int:
        """Return active_interval if a matching watchlist session is live, else interval."""
        if self.active_interval and self.active_check:
            try:
                sessions = db.get_active_watchlists()
                if any(s["session_type"] == self.active_check for s in sessions):
                    return self.active_interval
            except Exception:
                pass
        return self.interval

    async def maybe_run(self) -> None:
        now = time.time()
        if now - self._last_run < self._effective_interval():
            return
        if self.push_feed and failover.push_is_healthy(self.push_feed, FALLBACK_MAX_AGE):
            self._last_run = now
            log.debug("Fetcher %s: deferring to healthy push source", self.name)
            return
        self._last_run = now
        try:
            import importlib
            mod = importlib.import_module(self.module_name)
            result = await asyncio.get_event_loop().run_in_executor(None, mod.run)
            log.info("Fetcher %s: %s", self.name, result)
        except Exception as e:
            log.error("Fetcher %s failed: %s", self.name, e)


class SkillLoop:
    """Invokes a skill script as a subprocess on a fixed interval.

    Optional active_interval + active_check: when a watchlist session of type
    active_check ('train' | 'flight') is live, the loop uses active_interval.
    """

    def __init__(self, name: str, script: str, interval: int,
                 active_interval: int | None = None,
                 active_check: str | None = None):
        self.name = name
        self.script = script
        self.interval = interval
        self.active_interval = active_interval
        self.active_check = active_check
        self._last_run = 0.0

    def _effective_interval(self) -> int:
        if self.active_interval and self.active_check:
            try:
                sessions = db.get_active_watchlists()
                if any(s["session_type"] == self.active_check for s in sessions):
                    return self.active_interval
            except Exception:
                pass
        return self.interval

    async def maybe_run(self, src_dir: Path) -> None:
        now = time.time()
        if now - self._last_run < self._effective_interval():
            return
        self._last_run = now
        script_path = src_dir / self.script
        if not script_path.exists():
            log.warning("Skill script not found: %s", script_path)
            return
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, str(script_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
            if proc.returncode != 0 and proc.returncode is not None:
                log.error("Skill %s exited %d: %s",
                          self.name, proc.returncode, stderr.decode()[:200])
            else:
                log.info("Skill %s: ok (rc=%s)", self.name, proc.returncode)
        except asyncio.TimeoutError:
            log.error("Skill %s timed out after 120s", self.name)
        except Exception as e:
            log.error("Skill %s error: %s", self.name, e)


class TriggerReactor:
    """
    Watches the trigger directory for JSON files dropped by the admin REST API.
    Each file is processed once, then its outcome is written to the DB.
    """

    def __init__(self, trigger_dir: Path, src_dir: Path):
        self.trigger_dir = trigger_dir
        self.src_dir = src_dir

    async def process(self) -> None:
        self.trigger_dir.mkdir(parents=True, exist_ok=True)
        for path in sorted(self.trigger_dir.glob("*.json")):
            try:
                data = json.loads(path.read_text())
                trigger_id = data.get("id")
                trigger_type = data.get("type")
                payload = data.get("payload", {})
                path.unlink(missing_ok=True)  # Consume trigger file.

                log.info("Trigger %s: %s", trigger_id, trigger_type)
                await self._dispatch(trigger_id, trigger_type, payload)
            except Exception as e:
                log.error("Trigger processing error for %s: %s", path.name, e)

    async def _dispatch(self, trigger_id: str, trigger_type: str, payload: dict) -> None:
        try:
            if trigger_type == "refresh_feed":
                feed = payload.get("feed_name")
                await self._run_fetcher(feed, trigger_id)
            elif trigger_type == "force_recompute_cps":
                await self._run_skill("poller/skills/cps_recompute.py", trigger_id, force=True)
            elif trigger_type == "force_opsplan_snapshot":
                await self._run_skill("poller/fetchers/atcscc_opsplan.py",
                                      trigger_id, force=True)
            elif trigger_type == "push_test_alert":
                from pusher import main as pusher_main
                await asyncio.get_event_loop().run_in_executor(
                    None, lambda: pusher_main.send_test_alert(
                        payload.get("message", "Test alert from admin"))
                )
                db.resolve_trigger(trigger_id, "success")
            else:
                log.warning("Unknown trigger type: %s", trigger_type)
                db.resolve_trigger(trigger_id, "failed", f"unknown type: {trigger_type}")
        except Exception as e:
            log.error("Trigger %s dispatch error: %s", trigger_id, e)
            db.resolve_trigger(trigger_id, "failed", str(e))

    async def _run_fetcher(self, feed_name: str, trigger_id: str) -> None:
        polled_feeds = {s["name"]: s["module"] for s in FETCH_SCHEDULE}
        if feed_name not in polled_feeds:
            db.resolve_trigger(trigger_id, "failed",
                               f"{feed_name} is not a polled feed")
            return
        try:
            import importlib
            mod = importlib.import_module(polled_feeds[feed_name])
            await asyncio.get_event_loop().run_in_executor(None, mod.run)
            db.resolve_trigger(trigger_id, "success")
        except Exception as e:
            db.resolve_trigger(trigger_id, "failed", str(e))

    async def _run_skill(self, script: str, trigger_id: str, force: bool = False) -> None:
        script_path = self.src_dir / script
        args = [sys.executable, str(script_path)]
        if force:
            args.append("--force")
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.communicate(), timeout=120)
            db.resolve_trigger(trigger_id, "success" if proc.returncode == 0 else "failed")
        except Exception as e:
            db.resolve_trigger(trigger_id, "failed", str(e))


class WatchlistSweep:
    """Periodic watchlist maintenance tasks run by the poller."""

    EXPIRY_INTERVAL = 60          # sweep expired transient entries
    FLIGHT_SWEEP_INTERVAL = 120   # check active flight entries via AeroAPI / FDPS
    TRAIN_SWEEP_INTERVAL = 300    # check active train entries via amtraker
    LOCAL_AC_SWEEP_INTERVAL = 60  # cross-ref local_aircraft against watchlist

    def __init__(self) -> None:
        self._last_expiry = 0.0
        self._last_flight = 0.0
        self._last_train = 0.0
        self._last_local_ac = 0.0

    async def run_all(self) -> None:
        now = time.time()
        if now - self._last_expiry >= self.EXPIRY_INTERVAL:
            self._last_expiry = now
            await asyncio.get_event_loop().run_in_executor(None, self._do_expiry_sweep)
        if now - self._last_flight >= self.FLIGHT_SWEEP_INTERVAL:
            self._last_flight = now
            await asyncio.get_event_loop().run_in_executor(None, self._do_flight_sweep)
        if now - self._last_train >= self.TRAIN_SWEEP_INTERVAL:
            self._last_train = now
            await asyncio.get_event_loop().run_in_executor(None, self._do_train_sweep)
        if now - self._last_local_ac >= self.LOCAL_AC_SWEEP_INTERVAL:
            self._last_local_ac = now
            await asyncio.get_event_loop().run_in_executor(
                None, self._do_local_aircraft_sweep)

    @staticmethod
    def _do_expiry_sweep() -> None:
        try:
            removed = sweep_expired_transient()
            if removed:
                log.info("watchlist: swept %d expired transient entries", removed)
        except Exception as e:
            log.error("watchlist expiry sweep error: %s", e)

    @staticmethod
    def _do_flight_sweep() -> None:
        """
        Check active flight watchlist entries for OOOI events and delays.
        Data source priority:
          1. FlightAware AeroAPI  (if FLIGHTAWARE_API_KEY set)
          2. airplanes.live       (free, no key needed — primary live source)
          3. FDPS flight_events   (SWIM cache — when NMS provisioned)
        Triggers: OUT, OFF, ON, IN, delay >15min, delay >30min, diversion.
        Standing directive: all watchlist flights use this trigger set.
        """
        import os as _os
        try:
            from shared.watchlist import get_active_entries, watchlist_event_hit
            entries = get_active_entries(entry_type="flight")
            if not entries:
                return

            api_key = _os.environ.get("FLIGHTAWARE_API_KEY", "")
            for entry in entries:
                ident = entry["identifier"]
                try:
                    if api_key:
                        _check_flight_aeroapi(entry, ident, api_key)
                    else:
                        # airplanes.live first; fall back to FDPS cache
                        hit = _check_flight_airplanes_live(entry, ident)
                        if not hit:
                            _check_flight_fdps_cache(entry, ident)
                        if not hit:
                            # ADS-B dark — check schedule-based arrival inference
                            _check_flight_schedule_inference(entry, ident)
                except Exception as e:
                    log.debug("flight sweep %s: %s", ident, e)
        except Exception as e:
            log.error("flight sweep error: %s", e)

    @staticmethod
    def _do_local_aircraft_sweep() -> None:
        """
        Belt-and-suspenders cross-reference of local_aircraft against watchlist.
        Catches entries that started while ingest.local_airspace was restarting.
        Skips cleanly if local_aircraft table is empty (UltraFeeder not deployed).
        Match: callsign or registration (case-insensitive) vs identifier.
        Only fires if aircraft seen within 120s and no alert in last 10 minutes.
        """
        try:
            from shared.watchlist import get_active_entries, watchlist_event_hit
            aircraft = db.get_local_aircraft(max_age_seconds=120)
            if not aircraft:
                return  # UltraFeeder not deployed or no aircraft in range

            entries = get_active_entries(entry_type="flight")
            if not entries:
                return

            now_iso = __import__("datetime").datetime.now(
                __import__("datetime").timezone.utc
            ).strftime("%Y-%m-%dT%H:%M:%SZ")

            for ac in aircraft:
                icao_hex = ac.get("icao_hex", "")
                cs = (ac.get("callsign") or "").upper()
                reg = (ac.get("registration") or "").upper()
                dist = ac.get("distance_nm")

                for entry in entries:
                    ident = entry["identifier"].upper()
                    if (cs and cs == ident) or (reg and reg == ident):
                        # Check 10-minute dedup via local_airspace_alerts table
                        recent = db.get_local_airspace_alerts_recent(
                            entry["id"], "watchlist_proximity", max_age_seconds=600)
                        if recent:
                            continue
                        alt = ac.get("altitude_ft")
                        alt_str = f"{alt:,}ft" if alt is not None else "alt unknown"
                        dist_str = f"{dist:.1f}nm" if dist is not None else "dist unknown"
                        summary = (f"{ident} in local range (poller sweep): "
                                   f"{dist_str} | {alt_str}")
                        tracking = f"https://globe.airplanes.live/?icao={icao_hex}"
                        watchlist_event_hit(
                            entry["id"], summary,
                            {"watchlist_trigger": "watchlist_proximity",
                             "identifier": ident, "icao_hex": icao_hex,
                             "distance_nm": dist, "altitude_ft": alt,
                             "tracking_url": tracking,
                             "source": "poller_sweep"},
                            priority=4,
                        )
                        db.insert_local_airspace_alert(
                            fired_at=now_iso,
                            alert_type="watchlist_proximity",
                            icao_hex=icao_hex, callsign=ac.get("callsign"),
                            registration=ac.get("registration"),
                            distance_nm=dist, altitude_ft=alt,
                            squawk=ac.get("squawk"),
                            watchlist_entry_id=entry["id"],
                            payload={"identifier": ident, "source": "poller_sweep",
                                     "tracking_url": tracking},
                            ntfy_fired=1,
                        )
                        log.info("local_ac sweep: %s %.1fnm (poller sweep)",
                                 ident, dist or 0)
        except Exception as e:
            log.error("local aircraft sweep error: %s", e)

    @staticmethod
    def _do_train_sweep() -> None:
        """Check active train watchlist entries against amtraker."""
        import os as _os
        try:
            from shared.watchlist import get_active_entries, watchlist_event_hit
            entries = get_active_entries(entry_type="train")
            if not entries:
                return

            amtraker_url = _os.environ.get("AMTRAKER_API_URL",
                                            "https://api.amtraker.com/v3")
            for entry in entries:
                ident = entry["identifier"]
                try:
                    _check_train_amtraker(entry, ident, amtraker_url,
                                          watchlist_event_hit)
                except Exception as e:
                    log.debug("train sweep %s: %s", ident, e)
        except Exception as e:
            log.error("train sweep error: %s", e)


def _check_flight_aeroapi(entry: dict, ident: str, api_key: str) -> None:
    """Query FlightAware AeroAPI for current flight position/status."""
    import requests as _req
    url = f"https://aeroapi.flightaware.com/aeroapi/flights/{ident}/position"
    try:
        resp = _req.get(url, headers={"x-apikey": api_key}, timeout=10)
        if resp.status_code == 404:
            return
        resp.raise_for_status()
        data = resp.json()
        _evaluate_flight_status(entry, ident, data)
    except Exception as e:
        log.debug("aeroapi %s: %s", ident, e)


# ── Flight phase state machine ─────────────────────────────────────────────
# Phases: pre_departure → out → off → on → in
# Stored in watchlist_entries.last_event_summary for persistence across restarts.

_OOOI_PHASES = ("pre_departure", "out", "off", "on", "in")

def _phase_from_summary(summary: str) -> str:
    """Extract last known phase from last_event_summary string."""
    s = (summary or "").lower()
    for phase in _OOOI_PHASES:
        if phase in s:
            return phase
    return "pre_departure"


def _check_flight_airplanes_live(entry: dict, ident: str) -> bool:
    """
    Query airplanes.live free API for live ADS-B position.
    Returns True if data found (even if no new event fired), False if no data.
    Derives OOOI phase from position/altitude/speed.
    Also captures ICAO hex ID and updates watchlist notes.

    URL: https://api.airplanes.live/v2/callsign/{CALLSIGN}
    Hex-based tracking link: https://globe.airplanes.live/?icao={HEX}
    Standing directive: always use hex ID for tracking URL, never tail/flight number.
    """
    import re as _re
    import requests as _req
    from datetime import datetime, timezone
    from shared.watchlist import watchlist_event_hit

    # Route to hex endpoint for 6-char hex identifiers, callsign endpoint otherwise.
    if _re.fullmatch(r'[0-9a-f]{6}', ident.lower()):
        url = f"https://api.airplanes.live/v2/hex/{ident.lower()}"
    else:
        url = f"https://api.airplanes.live/v2/callsign/{ident.upper().replace(' ', '')}"
    try:
        resp = _req.get(url, timeout=10)
        if resp.status_code == 404:
            return False
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        log.debug("airplanes.live %s: %s", ident, e)
        return False

    ac_list = data.get("ac") or []
    if not ac_list:
        return False

    ac = ac_list[0]
    hex_id   = (ac.get("hex") or "").lower().strip()
    reg      = ac.get("r") or ""
    alt      = ac.get("alt_baro")   # int ft, or "ground"
    gs       = float(ac.get("gs") or 0)
    lat      = ac.get("lat")
    lon      = ac.get("lon")
    squawk   = ac.get("squawk") or ""
    dest_icao = ac.get("dst") or ""          # destination from FMS if available

    # Determine current phase from ADS-B data
    on_ground = (alt == "ground") or (isinstance(alt, (int, float)) and alt < 100 and gs < 80)
    airborne  = not on_ground and isinstance(alt, (int, float)) and alt >= 100

    last_phase = _phase_from_summary(entry.get("last_event_summary") or "")

    # Phase detection — designed to be sticky (never revert to earlier phase)
    if airborne and gs > 50:
        # Airborne and moving — wheels up
        current_phase = "off"
    elif on_ground and last_phase in ("off", "on"):
        # Post-landing ground state
        current_phase = "in" if gs <= 8 else "on"
    elif on_ground and last_phase == "out":
        # Still in departure sequence — hold "out" even when stopped (runway hold)
        current_phase = "out"
    elif on_ground and gs > 2 and last_phase not in ("off", "on"):
        # Moving on ground in departure direction — gs>2 catches slow pushback
        current_phase = "out"
    elif on_ground and last_phase in ("in",):
        current_phase = "in"
    elif on_ground:
        current_phase = "pre_departure"
    else:
        # Liminal state (takeoff roll <100ft, high gs) — preserve last phase
        # rather than reverting; if last was "out" keep "out" so OFF can fire next
        current_phase = last_phase if last_phase == "out" else "pre_departure"

    # Detect diversion: FMS dest differs from watchlist destination
    expected_dest = (entry.get("destination") or "").upper().replace("K", "", 1)
    if dest_icao and expected_dest and dest_icao.upper() not in (expected_dest, "K" + expected_dest):
        divert_summary = f"{ident} DIVERTED to {dest_icao} (expected {entry.get('destination','')})"
        tracking = f"https://globe.airplanes.live/?icao={hex_id}" if hex_id else ""
        detail = (divert_summary + "\nTrack: " + tracking) if tracking else divert_summary
        watchlist_event_hit(entry["id"], divert_summary,
                            {"watchlist_trigger": "diversion", "identifier": ident,
                             "hex": hex_id, "diverted_to": dest_icao,
                             "tracking_url": tracking},
                            priority=5)

    # Fire OOOI event if phase changed
    tracking_url = f"https://globe.airplanes.live/?icao={hex_id}" if hex_id else ""

    event_map = {
        ("pre_departure", "out"): (f"{ident} OUT — gate departure / pushback", 4),
        ("out",           "off"): (f"{ident} OFF — wheels up", 5),
        ("pre_departure", "off"): (f"{ident} OFF — wheels up (airborne)", 5),
        ("off",           "on"):  (f"{ident} ON — wheels down / landed", 5),
        ("on",            "in"):  (f"{ident} IN — at gate (arrived)", 4),
        ("off",           "in"):  (f"{ident} IN — at gate (arrived)", 4),
    }

    event_key = (last_phase, current_phase)
    if event_key in event_map and current_phase != last_phase:
        summary, priority = event_map[event_key]
        if tracking_url:
            summary_full = summary + "\n" + tracking_url
        else:
            summary_full = summary
        watchlist_event_hit(entry["id"], summary,
                            {"watchlist_trigger": f"oooi_{current_phase}",
                             "identifier": ident, "hex": hex_id, "reg": reg,
                             "alt_ft": alt, "gs_kt": gs, "lat": lat, "lon": lon,
                             "phase": current_phase,
                             "tracking_url": tracking_url},
                            priority=priority)
        log.info("flight OOOI: %s %s→%s", ident, last_phase, current_phase)

    return True



def _check_flight_schedule_inference(entry: dict, ident: str) -> None:
    """
    Fallback when ADS-B is dark (transponder off at gate).
    If the last known phase was 'off' (confirmed airborne) and current time
    is past scheduled_arrival + 10 min, infer the flight has arrived (IN).
    Also handles OFF inference: if scheduled_departure + 90 min has passed
    and last phase was pre_departure, infer the flight departed.
    Fires at priority 4 with "(schedule inferred)" note.
    """
    from shared.watchlist import watchlist_event_hit
    from datetime import datetime, timezone, timedelta

    last_phase = _phase_from_summary(entry.get("last_event_summary") or "")
    now = datetime.now(timezone.utc)

    # Post-arrival inference: ADS-B dark after confirmed departure.
    # Trigger on dep+45min OR arr-15min, whichever comes first.
    # Covers early arrivals where transponder goes dark before scheduled arr time.
    if last_phase in ("off", "on"):
        sched_arr = entry.get("scheduled_arrival")
        sched_dep = entry.get("scheduled_departure")
        fire = False
        reason = ""
        if sched_arr:
            try:
                arr_dt = datetime.fromisoformat(sched_arr.replace("Z", "+00:00"))
                if now > arr_dt - timedelta(minutes=15):
                    fire, reason = True, f"ADS-B dark, past arr-15min ({sched_arr})"
            except Exception:
                pass
        if not fire and sched_dep:
            try:
                dep_dt = datetime.fromisoformat(sched_dep.replace("Z", "+00:00"))
                if now > dep_dt + timedelta(minutes=45):
                    fire, reason = True, f"ADS-B dark 45min+ after departure ({sched_dep})"
            except Exception:
                pass
        if fire:
            summary = f"{ident} IN — at gate (schedule inferred, ADS-B dark)"
            watchlist_event_hit(
                entry["id"], summary,
                {"watchlist_trigger": "oooi_in_inferred",
                 "identifier": ident,
                 "note": reason},
                priority=4,
            )
            log.info("flight schedule infer: %s IN (%s)", ident, reason)

    # Departure inference: past sched_dep+90min with no OFF seen yet
    if last_phase == "pre_departure":
        sched_dep = entry.get("scheduled_departure")
        if sched_dep:
            try:
                dep_dt = datetime.fromisoformat(sched_dep.replace("Z", "+00:00"))
                if now > dep_dt + timedelta(minutes=90):
                    summary = f"{ident} OFF — departed (schedule inferred, ADS-B not seen)"
                    watchlist_event_hit(
                        entry["id"], summary,
                        {"watchlist_trigger": "oooi_off_inferred",
                         "identifier": ident, "scheduled_departure": sched_dep,
                         "note": "No ADS-B contact — departure inferred from schedule"},
                        priority=4,
                    )
                    log.info("flight schedule infer: %s OFF (past dep+90m)", ident)
            except Exception as e:
                log.debug("schedule infer dep %s: %s", ident, e)

def _check_flight_fdps_cache(entry: dict, ident: str) -> None:
    """Fall back to recent FDPS data in flight_events table (used when NMS provisioned)."""
    try:
        rows = db.get_active_flight_events(max_age_seconds=600)
        match = next(
            (r for r in rows
             if (r.get("flight_id") or "").upper() == ident.upper()
             or (r.get("flight_num") or "").upper() == ident.upper()),
            None,
        )
        if match:
            _evaluate_flight_status_fdps(entry, ident, match)
    except Exception as e:
        log.debug("fdps cache %s: %s", ident, e)


def _evaluate_flight_status_fdps(entry: dict, ident: str, data: dict) -> None:
    """Evaluate FDPS-sourced flight event for status changes (NMS path)."""
    from shared.watchlist import watchlist_event_hit
    status = (data.get("status") or "").lower()
    if not status:
        return
    last = entry.get("last_event_summary") or ""
    if status == last.lower():
        return
    summary = f"{ident} FDPS: {status}"
    watchlist_event_hit(entry["id"], summary,
                        {"watchlist_trigger": "fdps_status", "status": status,
                         "identifier": ident},
                        priority=3)


def _check_train_amtraker(entry: dict, ident: str, base_url: str,
                          watchlist_event_hit) -> None:
    """Query amtraker API for current train status and fire delay alerts."""
    import requests as _req
    from datetime import datetime, timezone
    url = f"{base_url}/trains/{ident}"
    try:
        resp = _req.get(url, timeout=15)
        if resp.status_code == 404:
            return
        resp.raise_for_status()
        trains = resp.json()
    except Exception as e:
        log.debug("amtraker %s: %s", ident, e)
        return

    if not trains:
        return
    train = trains[0] if isinstance(trains, list) else trains

    # Calculate delay from scheduled vs predicted arrival.
    sched_str = entry.get("scheduled_arrival")
    pred_str = (train.get("estimatedArrival")
                or train.get("predicted_arrival")
                or train.get("arrivalTime"))

    delay_min = None
    if sched_str and pred_str:
        try:
            sched = datetime.fromisoformat(sched_str.replace("Z", "+00:00"))
            pred = datetime.fromisoformat(str(pred_str).replace("Z", "+00:00"))
            delay_min = int((pred - sched).total_seconds() / 60)
        except ValueError:
            pass

    state = (train.get("trainState") or train.get("status") or "").lower()
    last_event = entry.get("last_event_summary") or ""

    if delay_min is not None:
        if delay_min >= 30:
            priority, label = 5, f"LATE {delay_min}min"
        elif delay_min >= 15:
            priority, label = 4, f"late {delay_min}min"
        elif delay_min <= 0 and "arrived" in state:
            priority, label = 2, "arrived on time"
        else:
            return
        summary = f"#{ident} {label}"
    elif "arrived" in state:
        priority, label = 2, "arrived"
        summary = f"#{ident} arrived"
    else:
        return

    if summary == last_event:
        return
    watchlist_event_hit(
        entry["id"], summary,
        {"watchlist_trigger": "train_sweep", "state": state,
         "delay_min": delay_min, "identifier": ident},
        priority=priority,
    )


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    db.init_db()
    db.init_db_v2()
    db.init_db_v3()
    db.init_db_v4()
    db.init_db_v5()
    db.init_db_v6()
    db.init_db_v7()

    src_dir = Path(__file__).parent.parent
    trigger_dir = Path(config.trigger_dir())
    trigger_dir.mkdir(parents=True, exist_ok=True)

    fetchers = [FetchLoop(**s) for s in FETCH_SCHEDULE]
    skills = [SkillLoop(**s) for s in SKILL_SCHEDULE]
    reactor = TriggerReactor(trigger_dir, src_dir)
    watchlist_sweep = WatchlistSweep()

    # Start permanent watchlist file watcher.
    watcher_stop = threading.Event()
    watcher = WatchlistFileWatcher()
    watcher.start(watcher_stop)

    shutdown = asyncio.Event()

    def _signal_handler():
        log.info("Poller shutdown requested")
        shutdown.set()
        watcher_stop.set()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _signal_handler)

    log.info("corporatetraveldc poller started")

    while not shutdown.is_set():
        # Run fetchers.
        for f in fetchers:
            await f.maybe_run()

        # Run skills.
        for s in skills:
            await s.maybe_run(src_dir)

        # Process any pending triggers.
        await reactor.process()

        # Watchlist sweeps.
        await watchlist_sweep.run_all()

        await asyncio.sleep(10)  # Tight loop with 10s tick.

    watcher_stop.set()
    log.info("corporatetraveldc poller stopped")


if __name__ == "__main__":
    asyncio.run(main())
