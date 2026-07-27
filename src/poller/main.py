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
from common.acars import get_latest_phase as _acars_phase
from ingest import failover
from shared.watchlist import WatchlistFileWatcher, sweep_expired_transient

log = logging.getLogger(__name__)

# ── Schedule definitions ───────────────────────────────────────────────────────
# interval_seconds: how often to run the fetcher.
# Each fetcher is also independently triggerable via trigger files.

FETCH_SCHEDULE: list[dict] = [
    # push_feed removed 2026-07-23: was "stdds" (STDDS/TAIS surface surveillance --
    # unrelated to TFRs, a leftover from the 2026-06-07 POC commit that was never
    # corrected once real per-feed push sources existed). This silently suppressed
    # every real TFR REST poll for 2+ days since STDDS push has been reliably
    # healthy -- zero Marine One / POTUS TFR alerts could fire in that window.
    # No genuine push:tfr source exists; TFR has no push-primary path, restore
    # independent REST polling of tfr.faa.gov/tfrapi/getTfrList.
    {"name": "tfr",           "module": "poller.fetchers.tfr",           "interval": 300},
    {"name": "metar",         "module": "poller.fetchers.metar",         "interval": 300},
    # push_feed removed 2026-07-23: was "tfms" (Traffic Flow Management -- carries
    # GDP/GS/TMI data, a real but partial overlap with NAS status, not a full
    # substitute -- nasstatus.faa.gov also carries airport closures TFMS doesn't).
    # Also a 2026-06-07 POC leftover, never corrected. Silently suppressed every
    # real NAS-status REST poll for 2.8+ days since TFMS push has been reliably
    # healthy. No push:nas source exists. Restore independent REST polling of
    # nasstatus.faa.gov/api/airport-status-information.
    {"name": "nas",           "module": "poller.fetchers.nas",           "interval": 300},
    {"name": "nws",           "module": "poller.fetchers.nws",           "interval": 300,  "push_feed": "nws"},
    {"name": "notam",         "module": "poller.fetchers.notam",         "interval": 300,  "push_feed": "fns"},
    {"name": "runsheet",      "module": "poller.fetchers.runsheet",      "interval": 300},
    {"name": "atcscc_opsplan","module": "poller.fetchers.atcscc_opsplan","interval": 3600},
    {"name": "dca_fids",         "module": "poller.fetchers.dca_fids",         "interval": 300},
    {"name": "iad_fids",         "module": "poller.fetchers.iad_fids",         "interval": 300},
    {"name": "eurocontrol",    "module": "poller.fetchers.eurocontrol",    "interval": 900},
    {"name": "jasdat",         "module": "poller.fetchers.jasdat",         "interval": 900},
]

# Skills invoked as subprocesses (own SR-1/SR-2 state, own log entries).
# Ollama-backed skills get a 950s subprocess timeout -- comfortably past the
# skill's own internal 900s Ollama call timeout, so a call that's merely queued
# behind a thermally-paused Ollama (see ollama_governor) gets waited out instead
# of the subprocess being killed and losing the run. Non-LLM skills keep 120s.
_OLLAMA_SKILL_TIMEOUT = 950

SKILL_SCHEDULE: list[dict] = [
    {"name": "tfr-enrichment",  "script": "poller/skills/tfr_enrichment.py",  "interval": 300,
     "timeout": _OLLAMA_SKILL_TIMEOUT},
    {"name": "route-impact",    "script": "poller/skills/route_impact.py",     "interval": 300,
     "timeout": _OLLAMA_SKILL_TIMEOUT},
    {"name": "cps-recompute",   "script": "poller/skills/cps_recompute.py",    "interval": 3600},
    {"name": "train-impact",    "script": "poller/skills/train_impact.py",
     "interval": 900, "active_interval": 300, "active_check": "train",
     "timeout": _OLLAMA_SKILL_TIMEOUT},
    {"name": "flight-impact",   "script": "poller/skills/flight_impact.py",
     "interval": 900, "active_interval": 300, "active_check": "flight",
     "timeout": _OLLAMA_SKILL_TIMEOUT},
    {"name": "osint-monitor",   "script": "poller/skills/osint_monitor.py",  "interval": 900,
     "timeout": _OLLAMA_SKILL_TIMEOUT},
    {"name": "flight-cleanup",  "script": "poller/skills/flight_events_cleanup.py", "interval": 3600},
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

    Runs as a background asyncio task rather than an inline await: an
    Ollama-backed skill that's queued behind a thermally-paused/slow Ollama
    call (see ollama_governor.py) can take up to `timeout` seconds without
    blocking fetchers, other skills, or watchlist sweeps from running on
    schedule in the meantime. An in-flight guard skips re-triggering a skill
    whose previous run hasn't finished yet.
    """

    def __init__(self, name: str, script: str, interval: int,
                 active_interval: int | None = None,
                 active_check: str | None = None,
                 timeout: int = 120):
        self.name = name
        self.script = script
        self.interval = interval
        self.active_interval = active_interval
        self.active_check = active_check
        self.timeout = timeout
        self._last_run = 0.0
        self._task: asyncio.Task | None = None

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
        if self._task is not None and not self._task.done():
            log.debug("Skill %s: previous run still in flight (queued behind "
                      "Ollama?) -- skipping this tick", self.name)
            return
        self._last_run = now
        script_path = src_dir / self.script
        if not script_path.exists():
            log.warning("Skill script not found: %s", script_path)
            return
        self._task = asyncio.create_task(self._run(script_path))

    async def _run(self, script_path: Path) -> None:
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, str(script_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=self.timeout)
            if proc.returncode != 0 and proc.returncode is not None:
                log.error("Skill %s exited %d: %s",
                          self.name, proc.returncode, stderr.decode()[:200])
            else:
                log.info("Skill %s: ok (rc=%s)", self.name, proc.returncode)
        except asyncio.TimeoutError:
            log.error("Skill %s timed out after %ds", self.name, self.timeout)
            if proc is not None:
                try:
                    proc.kill()
                except Exception:
                    pass
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
            elif trigger_type == "force_osint_scrape":
                await self._run_skill("poller/skills/osint_monitor.py",
                                      trigger_id, force=True)
            elif trigger_type == "push_test_alert":
                from pusher import main as pusher_main
                _msg      = payload.get("message", "Test alert from admin")
                _topic    = payload.get("topic", "ops-health")
                _title    = payload.get("title")
                _priority = int(payload.get("priority", 3))
                await asyncio.get_event_loop().run_in_executor(
                    None, lambda: pusher_main.send_test_alert(
                        _msg, topic=_topic, title=_title, priority=_priority)
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
        # Ollama-backed skills (currently just osint-monitor's manual trigger)
        # get the same queued-not-killed timeout as the scheduled loop.
        skill_timeout = 950 if "osint_monitor" in script else 120
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.communicate(), timeout=skill_timeout)
            db.resolve_trigger(trigger_id, "success" if proc.returncode == 0 else "failed")
        except Exception as e:
            db.resolve_trigger(trigger_id, "failed", str(e))


class WatchlistSweep:
    """Periodic watchlist maintenance tasks run by the poller."""

    EXPIRY_INTERVAL = 60          # sweep expired transient entries
    FLIGHT_SWEEP_INTERVAL = 120   # check active flight entries via AeroAPI / FDPS
    TRAIN_SWEEP_INTERVAL = 300    # check active train entries via amtraker
    VESSEL_SWEEP_INTERVAL = 300   # check active vessel entries via AISHub/Kpler
    LOCAL_AC_SWEEP_INTERVAL = 60  # cross-ref local_aircraft against watchlist
    FAA_REGISTRY_INTERVAL = 1 * 86400  # daily — FAA actually refreshes
    # ReleasableAircraft.zip daily at 23:30 CT; the registry itself only
    # changes that once/day regardless, so this mostly buys faster recovery
    # if an import fails rather than more frequent real data. (Was weekly;
    # changed 2026-07-13 per operator request.)
    OPENSKY_FRESHNESS_INTERVAL = 30 * 86400  # monthly HEAD-only freshness probe
    # Added 2026-07-21. OpenSky's bulk aircraft metadata CSV is a frozen
    # snapshot (confirmed stale since Nov 2024, "on hold" per their own
    # site) -- this only does a HEAD request unless the source has actually
    # changed, see poller/fetchers/opensky_registry.py module docstring.
    # NOT a full-download interval like FAA_REGISTRY_INTERVAL above.

    def __init__(self) -> None:
        self._last_expiry = 0.0
        self._last_flight = 0.0
        self._last_train = 0.0
        self._last_vessel = 0.0
        self._last_local_ac = 0.0
        # Delay first FAA registry pull by 60s so startup I/O settles, then
        # check whether an import has already happened this week.
        self._last_faa_registry = self._faa_registry_last_import_epoch()
        self._last_opensky_freshness = self._opensky_last_import_epoch()

    @staticmethod
    def _opensky_last_import_epoch() -> float:
        """Return epoch of last OpenSky freshness check/import, or 0 if never run."""
        try:
            from common.db import opensky_registry_meta_get, init_db_v17
            init_db_v17()
            ts = opensky_registry_meta_get("last_full_import")
            if ts:
                from datetime import datetime, timezone
                return datetime.fromisoformat(ts).timestamp()
        except Exception:
            pass
        return 0.0

    @staticmethod
    def _faa_registry_last_import_epoch() -> float:
        """Return epoch of last FAA registry import, or 0 if never imported."""
        try:
            from common.db import faa_registry_meta_get, init_db_v11
            init_db_v11()
            ts = faa_registry_meta_get("last_full_import")
            if ts:
                from datetime import datetime, timezone
                return datetime.fromisoformat(ts).timestamp()
        except Exception:
            pass
        return 0.0

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
        if now - self._last_vessel >= self.VESSEL_SWEEP_INTERVAL:
            self._last_vessel = now
            await asyncio.get_event_loop().run_in_executor(None, self._do_vessel_sweep)
        if now - self._last_local_ac >= self.LOCAL_AC_SWEEP_INTERVAL:
            self._last_local_ac = now
            await asyncio.get_event_loop().run_in_executor(
                None, self._do_local_aircraft_sweep)
        if now - self._last_faa_registry >= self.FAA_REGISTRY_INTERVAL:
            self._last_faa_registry = now
            await asyncio.get_event_loop().run_in_executor(
                None, self._do_faa_registry_import)
        if now - self._last_opensky_freshness >= self.OPENSKY_FRESHNESS_INTERVAL:
            self._last_opensky_freshness = now
            await asyncio.get_event_loop().run_in_executor(
                None, self._do_opensky_freshness_check)

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
          1. FlightAware AeroAPI  (if FLIGHTAWARE_AEROAPI_KEY set)
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

            api_key = _os.environ.get("FLIGHTAWARE_AEROAPI_KEY", "")
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

            # Landed/dead auto-sweep, right after status-check freshens
            # oooi_phase for this tick's entries. See sweep_landed_flights()
            # docstring for the operator directive behind this (2026-07-21).
            from shared.watchlist import sweep_landed_flights
            swept = sweep_landed_flights()
            if swept:
                log.info("watchlist: landed/dead-swept %d flight entries", swept)
        except Exception as e:
            log.error("flight sweep error: %s", e)

    @staticmethod
    def _do_vessel_sweep() -> None:
        """
        Check active vessel watchlist entries (MMSI) via AISHub / Kpler
        Maritime 2.0, mirroring _do_flight_sweep / _do_train_sweep. Stub
        added 2026-07-21 alongside the vessel entry_type itself -- see
        shared/watchlist.py's _FILE_MAP comment and web/routes/watchlist.py's
        add_vessel_watchlist.

        Source: AISHub only (free data-sharing cooperative, AIS_AISHUB_ID --
        empty until the local receiver is up and registered). Kpler Maritime
        2.0 was evaluated and dropped 2026-07-21 per operator direction --
        MarineTraffic/Kpler access is a sales-gated enterprise product with
        no working credential in hand, not worth chasing when AISHub already
        works. This method runs every tick and no-ops cleanly (does nothing)
        until AIS_AISHUB_ID is set.
        """
        import os as _os
        try:
            from shared.watchlist import get_active_entries, watchlist_event_hit
            entries = get_active_entries(entry_type="vessel")
            if not entries:
                return

            aishub_id = _os.environ.get("AIS_AISHUB_ID", "")
            if not aishub_id:
                return  # no source configured yet -- see docstring

            for entry in entries:
                mmsi = entry["identifier"]
                try:
                    _check_vessel_aishub(entry, mmsi, aishub_id)
                except Exception as e:
                    log.debug("vessel sweep %s: %s", mmsi, e)
        except Exception as e:
            log.error("vessel sweep error: %s", e)

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

            # Landed/dead auto-sweep, right after status-check freshens
            # last_event_summary for this tick's entries. See
            # sweep_landed_trains() docstring for the operator directive
            # behind this (2026-07-21).
            from shared.watchlist import sweep_landed_trains
            swept = sweep_landed_trains()
            if swept:
                log.info("watchlist: landed/dead-swept %d train entries", swept)
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

# Last reliable altitude per identifier — guards against single "ground" readings
# at cruise altitude triggering false ON/IN phase transitions.
_last_known_alt: dict[str, int] = {}

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

    def _al_fetch(url: str) -> list:
        """Fetch airplanes.live endpoint, return ac list or empty."""
        try:
            resp = _req.get(url, timeout=10)
            if resp.status_code == 404:
                return []
            resp.raise_for_status()
            return resp.json().get("ac") or []
        except Exception as e:
            log.debug("airplanes.live %s: %s", url, e)
            return []

    # Resolve ICAO hex: explicit hex identifier > callsign > known hex
    # (structured hex_id column, then legacy notes text) > reg fallback.
    # Military serials (82-8000, 98-0002 etc.) and civil regs (N757AF) are stored under
    # identifier -- their known hex used to live only as free text ("Hex: xxxxxx")
    # in notes; schema v18 (2026-07-21) added a real hex_id column, backfilled from
    # that same notes text.
    #
    # IMPORTANT: callsign must stay the PRIMARY resolution path for anything
    # that isn't already a bare hex identifier. Querying airplanes.live BY a
    # known/expected hex, instead of by callsign, would make the identity-
    # mismatch check below tautological -- you'd only ever get back the
    # aircraft you already told the API to find, so "observed hex != expected
    # hex" could never fire. resolved_via_hex tracks when a hex-keyed lookup
    # was actually used (bare-hex identifier, or callsign not broadcasting)
    # so that no-op comparison gets skipped instead of silently "always
    # passing".
    expected_hex = (entry.get("hex_id") or "").lower().strip() or None
    notes_hex: str | None = None
    m = _re.search(r'\bHex:\s*([0-9a-fA-F]{6})\b', entry.get("notes") or "", _re.IGNORECASE)
    if m:
        notes_hex = m.group(1).lower()

    # Operator directive 2026-07-23: once a flight/tail has been resolved
    # to a hex, that hex is the ONLY thing treated as authoritative for
    # all further sweeps -- identifier/reservation becomes notification
    # text only, never a resolution key again. So: an entry with a
    # confirmed hex_id is queried by hex directly, exclusively, from here
    # on -- never re-resolved via callsign. An entry with no hex_id yet is
    # still in bootstrap mode and must use callsign to discover one (see
    # callsign_live_confirmed below, which locks it in on first hit).
    resolved_via_hex = False
    callsign_live_confirmed = False

    if _re.fullmatch(r'[0-9a-f]{6}', ident.lower()):
        ac_list = _al_fetch(f"https://api.airplanes.live/v2/hex/{ident.lower()}")
        resolved_via_hex = True
    elif expected_hex:
        ac_list = _al_fetch(f"https://api.airplanes.live/v2/hex/{expected_hex}")
        resolved_via_hex = True
    else:
        # Bootstrap phase only -- no confirmed hex exists yet, callsign is
        # the only way to discover one.
        ident_clean = ident.upper().replace(" ", "")
        ac_list = _al_fetch(f"https://api.airplanes.live/v2/callsign/{ident_clean}")
        if ac_list:
            callsign_live_confirmed = True
        if not ac_list and notes_hex:
            ac_list = _al_fetch(f"https://api.airplanes.live/v2/hex/{notes_hex}")
            resolved_via_hex = True
        if not ac_list:
            ac_list = _al_fetch(f"https://api.airplanes.live/v2/reg/{ident_clean}")

    if not ac_list:
        return False

    ac = ac_list[0]
    hex_id   = (ac.get("hex") or "").lower().strip()
    reg      = ac.get("r") or ""

    # Auto hex-lock: first genuine live contact under the callsign (as
    # opposed to a reg-fallback guess, which may not even be this specific
    # leg's aircraft) permanently anchors this entry to that hex for every
    # future sweep. This is what makes the directive self-enforcing --
    # entries created without a confirmed hex_id (the common, correct case
    # per flight-hifi-track Step 3 when the operating tail is still
    # unconfirmed) graduate to hex-locked automatically the moment we
    # actually see the aircraft, rather than staying callsign-bootstrapped
    # forever.
    if callsign_live_confirmed and hex_id and not expected_hex:
        try:
            db.set_watchlist_identity(entry["id"], hex_id=hex_id, registration=reg or None)
            log.info("%s: hex-locked to %s (%s) on first live contact",
                     ident, hex_id, reg or "no reg")
        except Exception as e:
            log.warning("%s: hex-lock failed: %s", ident, e)
    alt      = ac.get("alt_baro")   # int ft, or "ground"
    gs       = float(ac.get("gs") or 0)
    lat      = ac.get("lat")
    lon      = ac.get("lon")
    squawk   = ac.get("squawk") or ""
    dest_icao = ac.get("dst") or ""          # destination from FMS if available

    # ── 1. ACARS is authoritative — always checked first ────────────────────
    # Update altitude tracker from valid ADS-B readings for fallback continuity.
    if isinstance(alt, (int, float)) and alt > 500:
        _last_known_alt[ident] = int(alt)

    # OOOI phase state lives in its own dedicated DB field (oooi_phase,
    # added 2026-07-21), decoupled from last_event_summary. Every other
    # alert type for this entry (TMI assignment, flight-plan amendment,
    # approach-proximity ping, FDPS filed/cancelled) also overwrites
    # last_event_summary -- deriving phase by parsing that shared text field
    # meant any of those unrelated alerts would clobber phase-tracking state
    # in between OOOI checks, so the parser fell back to "pre_departure" and
    # the next airborne check looked like a brand-new transition. Confirmed
    # live: one DAL2962 takeoff fired "OFF -- airborne" 14 separate times
    # over 90 minutes before this fix.
    last_phase = entry.get("oooi_phase") or "pre_departure"

    # Prefer the entry's own confirmed registration (operator-set or
    # backfilled by a prior ADS-B hit) over this tick's live-observed reg --
    # the entry-level value only gets set when someone was confident (see
    # web/routes/watchlist.py), so it's less likely to be a same-day tail
    # swap in progress. Falls back to the just-observed ADS-B registration
    # so Jumpseat (registration-keyed) still has something to search on for
    # entries that have never had a registration confirmed yet.
    acars_reg = entry.get("registration") or reg
    acars = _acars_phase(ident, registration=acars_reg)
    if acars:
        current_phase, acars_msg = acars
        log.info(
            "%s: %s %s authoritative — label=%s msg_time=%s",
            ident, acars_msg.get("_source", "ACARS"), current_phase.upper(),
            acars_msg.get("label"), acars_msg.get("msg_time"),
        )
    else:
        # ── 2. ADS-B phase derivation (ACARS unavailable) ────────────────────
        # Altitude guard: if ADS-B reports sudden low alt but last known was high
        # cruise, it is almost certainly a bad transponder reading — discard it.
        last_known = _last_known_alt.get(ident)
        if (alt == "ground" or (isinstance(alt, (int, float)) and alt < 500)) \
                and last_known is not None and last_known > 10_000:
            log.warning(
                "%s: API returned alt=%r but last known was %dft — ignoring likely bad reading",
                ident, alt, last_known,
            )
            alt = last_known

        on_ground = (alt == "ground") or (isinstance(alt, (int, float)) and alt < 100 and gs < 80)
        airborne  = not on_ground and isinstance(alt, (int, float)) and alt >= 100

        if airborne and gs > 50:
            current_phase = "off"
        elif on_ground and last_phase in ("off", "on"):
            current_phase = "in" if gs <= 8 else "on"
        elif on_ground and last_phase == "out":
            current_phase = "out"
        elif on_ground and gs > 2 and last_phase not in ("off", "on"):
            current_phase = "out"
        elif on_ground and last_phase in ("in",):
            current_phase = "in"
        elif on_ground:
            current_phase = "pre_departure"
        else:
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

    # Detect identity mismatch: the live ADS-B hex we just resolved for
    # this callsign/identifier differs from the entry's own stored expected
    # hex_id -- 2026-07-21, schema v18. This is the actual enforcement of
    # "follows the metal, not the schedule": a flight number can get
    # reassigned to a different physical airframe day to day, and every
    # match in this pipeline used to trust the callsign/identifier string
    # alone. Only fires when the entry HAS a known expected hex (an entry
    # with no hex_id set yet has nothing to compare against, so it's
    # silently skipped rather than false-alarming on every poll).
    expected_reg = (entry.get("registration") or "").upper().strip() or None
    if expected_hex and hex_id and hex_id != expected_hex and not resolved_via_hex:
        mismatch_summary = (
            f"{ident} IDENTITY MISMATCH — tracking hex {hex_id} "
            f"(expected {expected_hex}, reg {reg or '?'})"
        )
        tracking = f"https://globe.airplanes.live/?icao={hex_id}" if hex_id else ""
        watchlist_event_hit(entry["id"], mismatch_summary,
                            {"watchlist_trigger": "identity_mismatch",
                             "identifier": ident, "expected_hex": expected_hex,
                             "observed_hex": hex_id, "expected_registration": expected_reg,
                             "observed_registration": reg, "tracking_url": tracking},
                            priority=5)
    elif not expected_hex and expected_reg and reg and reg.upper() != expected_reg:
        # Secondary signal for entries that only have a stored registration
        # (no hex_id backfilled yet) -- weaker check since tail numbers can
        # legitimately be reused across owners over years, but still worth
        # a flag rather than silent trust.
        mismatch_summary = (
            f"{ident} REGISTRATION MISMATCH — tracking {reg} (expected {expected_reg})"
        )
        watchlist_event_hit(entry["id"], mismatch_summary,
                            {"watchlist_trigger": "identity_mismatch_reg",
                             "identifier": ident, "expected_registration": expected_reg,
                             "observed_registration": reg, "observed_hex": hex_id},
                            priority=4)

    # Fire OOOI event if phase changed.
    # Includes mid-leg entries where earlier phases were not observed.
    tracking_url = f"https://globe.airplanes.live/?icao={hex_id}" if hex_id else ""

    event_map = {
        ("pre_departure", "out"): (f"{ident} OUT — gate departure / pushback", 4),
        ("out",           "off"): (f"{ident} OFF — wheels up", 5),
        ("pre_departure", "off"): (f"{ident} OFF — airborne", 5),
        ("off",           "on"):  (f"{ident} ON — wheels down / landed", 5),
        ("out",           "on"):  (f"{ident} ON — wheels down / landed", 5),
        ("pre_departure", "on"):  (f"{ident} ON — landed (departure not tracked)", 5),
        ("on",            "in"):  (f"{ident} IN — at gate", 4),
        ("off",           "in"):  (f"{ident} IN — at gate", 4),
        ("out",           "in"):  (f"{ident} IN — at gate", 4),
        ("pre_departure", "in"):  (f"{ident} IN — at gate (arrival not tracked)", 4),
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
        # Persist the new phase to its own field immediately -- this is what
        # stops the next check from re-deriving a stale phase and re-firing
        # this same transition. Independent of watchlist_event_hit's own
        # 5-min dedup (which only rate-limits identical event_types; it does
        # not fix mis-detected "new" transitions).
        db.update_watchlist_oooi_phase(
            entry["id"], current_phase,
            datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
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

    # See _check_flight_airplanes_live for why this reads the dedicated
    # oooi_phase field instead of parsing last_event_summary.
    last_phase = entry.get("oooi_phase") or "pre_departure"
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
            # Operator directive 2026-07-23: don't accept a pure time-based
            # "landed" guess without a live ACARS cross-check first -- this
            # phase write is what sweep_landed_flights() reads to decide
            # whether to auto-delete the entry. ADS-B dark alone doesn't
            # distinguish "landed, taxied in, out of local SDR/aggregator
            # VHF range" from "still airborne, diverted, or otherwise not
            # actually down." If ACARS has data and it disagrees with IN,
            # suppress the inference and let real tracking catch up
            # instead. If ACARS has nothing either, the schedule guess is
            # still the best signal available -- proceed as before.
            acars_check = None
            try:
                acars_check = _acars_phase(ident, registration=entry.get("registration"))
            except Exception as e:
                log.debug("schedule infer ACARS check %s: %s", ident, e)
            if acars_check and acars_check[0] != "in":
                log.info(
                    "%s: schedule-inferred IN suppressed -- ACARS shows phase=%s instead",
                    ident, acars_check[0],
                )
                return

            summary = f"{ident} IN — at gate (schedule inferred, ADS-B dark)"
            watchlist_event_hit(
                entry["id"], summary,
                {"watchlist_trigger": "oooi_in_inferred",
                 "identifier": ident,
                 "note": reason},
                priority=4,
            )
            db.update_watchlist_oooi_phase(
                entry["id"], "in", now.strftime("%Y-%m-%dT%H:%M:%SZ"),
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
                    db.update_watchlist_oooi_phase(
                        entry["id"], "off", now.strftime("%Y-%m-%dT%H:%M:%SZ"),
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


def _check_vessel_aishub(entry: dict, mmsi: str, aishub_id: str) -> None:
    """
    Query AISHub for a specific watchlisted MMSI and fire a position/status
    event if it's found. AISHub's public API is bbox-based (no direct
    single-MMSI lookup in the free tier) -- reuses the exact same DC-area
    bbox query already proven out in runner/main.py's /api/ais/vessels
    AISHub tier, then filters client-side for our MMSI. Good enough for a
    stub since vessels worth watchlisting here are expected to be in local
    (Chesapeake/DC-area) waters; a vessel outside that window simply won't
    resolve yet -- not an error, just out of range for the free-tier query.
    """
    import requests as _req
    from datetime import datetime, timezone

    AIS_AISHUB_BASE = "http://data.aishub.net/ws.php"
    DEFAULT_LAT, DEFAULT_LON, DIST_NM = 38.8816, -77.0910, 120
    # ~1 deg latitude = 60nm; longitude scaled by cos(latitude) for the DC area.
    dlat = DIST_NM / 60.0
    dlon = DIST_NM / (60.0 * 0.78)
    params = {
        "username": aishub_id, "format": "1", "output": "json", "compress": "0",
        "latmin": DEFAULT_LAT - dlat, "latmax": DEFAULT_LAT + dlat,
        "lonmin": DEFAULT_LON - dlon, "lonmax": DEFAULT_LON + dlon,
    }
    try:
        resp = _req.get(AIS_AISHUB_BASE, params=params, timeout=12,
                        headers={"User-Agent": "corporatetraveldc/1.0"})
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        log.debug("aishub %s: %s", mmsi, e)
        return

    raw = [v for v in data if isinstance(v, dict) and "MMSI" in v]
    match = next((v for v in raw if str(v.get("MMSI")) == str(mmsi)), None)
    if not match:
        return  # not currently in range -- normal, not an error

    lat, lon = match.get("LATITUDE"), match.get("LONGITUDE")
    sog = match.get("SOG")
    name = (match.get("NAME") or "").strip()
    last_event = entry.get("last_event_summary") or ""
    summary = f"MMSI {mmsi} {name} -- {lat}N {lon}W {sog}kt".strip()
    if summary == last_event:
        return
    watchlist_event_hit(
        entry["id"], summary,
        {"watchlist_trigger": "vessel_position", "identifier": mmsi,
         "lat": lat, "lon": lon, "sog": sog, "source": "aishub.net"},
        priority=2,
    )


def _check_train_amtraker(entry: dict, ident: str, base_url: str,
                          watchlist_event_hit) -> None:
    """Query amtraker API for current train status and fire delay/state alerts."""
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

    state = (train.get("trainState") or train.get("status") or "").lower()
    last_event = entry.get("last_event_summary") or ""

    # Derive current delay from amtraker velocity/status fields if available.
    # Amtraker v3 returns velocityMph, trainTimely, and per-station ETA objects.
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

    # Classify state: arrived / delayed / en-route / unknown.
    # "en-route" fires once when train first appears (previous last_event was empty
    # or a non-running state), so the operator knows the service is active.
    is_arrived  = "arrived" in state or "station" in state
    is_enroute  = any(k in state for k in ("active", "enroute", "en route", "en-route",
                                            "moving", "departed"))
    was_running = any(k in (last_event or "").lower()
                      for k in ("departed", "en route", "on time", "late", "arrived"))

    if delay_min is not None:
        if delay_min >= 30:
            summary, priority = f"#{ident} LATE {delay_min}min", 5
        elif delay_min >= 15:
            summary, priority = f"#{ident} late {delay_min}min", 4
        elif is_arrived and delay_min <= 0:
            summary, priority = f"#{ident} arrived on time", 2
        elif is_arrived:
            summary, priority = f"#{ident} arrived +{delay_min}min", 3
        elif is_enroute and not was_running:
            route = entry.get("route_name") or ""
            origin = entry.get("origin") or ""
            dest   = entry.get("destination") or ""
            leg    = f" {origin}→{dest}" if origin or dest else ""
            summary  = f"#{ident} {route}{leg} en route (on time)" if route else f"#{ident}{leg} en route"
            priority = 2
        else:
            return
    elif is_arrived:
        summary, priority = f"#{ident} arrived", 2
    elif is_enroute and not was_running:
        # No schedule data — fire once when train goes live so operator knows it's running
        route = entry.get("route_name") or ""
        origin = entry.get("origin") or ""
        dest   = entry.get("destination") or ""
        leg    = f" {origin}→{dest}" if origin or dest else ""
        summary  = f"#{ident} {route}{leg} en route" if route else f"#{ident}{leg} en route"
        priority = 2
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


# ── FAA registry import ────────────────────────────────────────────────────────


class _FAARegistrySweep:
    """Weekly FAA N-number registry + LADD download, run inside WatchlistSweep."""

    @staticmethod
    def run() -> None:
        try:
            from poller.fetchers.faa_registry import fetch_faa_registry
            stats = fetch_faa_registry()
            if stats.get("ok"):
                log.info(
                    "FAA registry import OK — %d records, %d LADD, %.1fs",
                    stats.get("registry_upserted", 0),
                    stats.get("ladd_count", 0),
                    stats.get("elapsed_sec", 0),
                )
            else:
                log.error("FAA registry import failed: %s", stats.get("error"))
        except Exception as exc:
            log.error("FAA registry sweep exception: %s", exc)


# Patch _do_faa_registry_import onto WatchlistSweep
WatchlistSweep._do_faa_registry_import = staticmethod(_FAARegistrySweep.run)  # type: ignore[attr-defined]


# ── OpenSky registry freshness check ───────────────────────────────────────────


class _OpenSkyRegistrySweep:
    """Monthly HEAD-only freshness probe for the OpenSky aircraft metadata
    CSV, run inside WatchlistSweep. Only downloads the full 94MB+ file if
    the source has actually changed -- see fetcher module docstring."""

    @staticmethod
    def run() -> None:
        try:
            from poller.fetchers.opensky_registry import check_opensky_freshness
            stats = check_opensky_freshness()
            if not stats.get("ok"):
                log.error("OpenSky registry freshness check failed: %s", stats.get("error"))
            elif stats.get("changed"):
                log.info(
                    "OpenSky registry: source changed, re-imported — %d records, %.1fs",
                    stats.get("registry_upserted", 0), stats.get("elapsed_sec", 0),
                )
            else:
                log.info("OpenSky registry: source unchanged, no re-import needed")
        except Exception as exc:
            log.error("OpenSky registry freshness sweep exception: %s", exc)


WatchlistSweep._do_opensky_freshness_check = staticmethod(_OpenSkyRegistrySweep.run)  # type: ignore[attr-defined]


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
    db.init_db_v8()
    db.init_db_v9()
    db.init_db_v10()
    db.init_db_v11()
    db.init_db_v13()
    db.init_db_v14()
    db.init_db_v15()
    db.init_db_v16()
    db.init_db_v18()
    db.init_db_v19()
    db.init_db_v20()

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
