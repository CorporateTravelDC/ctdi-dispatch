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
# Phase 4 2026-08-15 (plan joyful-mapping-crown): reconciled with the
# spike-measured per-skill Python timeouts so this subprocess cap never
# undercuts a legitimately-running call. Coverage: tfr-enrichment (hot, 540s) and
# route-impact (hot, 480s) fit single-attempt + overhead; osint-monitor's
# budget allows 2 new HIGH+ items x 2 attempts x 300s narrative calls +
# generate() preflight gates (240+180) + load phase + overhead. A
# many-new-item OSINT run can still exceed this; that loses only the
# remainder of that one sweep, and the next 15-min cycle picks the items
# back up. Non-LLM skills keep 120s.
_OLLAMA_SKILL_TIMEOUT = 2000

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
    # 2026-07-27: was hourly against a 1h-window delete policy that never
    # actually ran (missing __main__ entry point -- see the skill's module
    # docstring). Redesigned as a 30-day retention + Nextcloud archival job;
    # daily is plenty for a 30-day-out window.
    {"name": "flight-cleanup",  "script": "poller/skills/flight_events_cleanup.py", "interval": 86400},
    # 2026-08-19: added alongside require_admin's new per-action audit writes
    # (auth.py) -- see audit_log_prune.py's module docstring for why this
    # shipped in the same change instead of as a follow-up.
    {"name": "audit-log-prune", "script": "poller/skills/audit_log_prune.py", "interval": 86400},
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
        skill_timeout = _OLLAMA_SKILL_TIMEOUT if "osint_monitor" in script else 120
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
        Check active flight watchlist entries for OOOI events, delays, and
        confirmed-flight-plan / airport-system changes.

        OOOI-phase (OUT/OFF/ON/IN) source priority -- a fallback chain,
        since these are all position-derived signals competing for the
        same phase determination:
          1. FlightAware AeroAPI  (if FLIGHTAWARE_AEROAPI_KEY set)
          2. airplanes.live       (free, no key needed — primary live source)
          3. Schedule inference   (ADS-B dark fallback)

        FDPS (FAA filed flight plan: destination/cancellation/status) and
        FIDS (DCA/IAD gate/baggage/status, MWAA feed) are NOT part of that
        fallback chain -- they're independent plan/airport-system signals,
        so both are checked every tick for every entry regardless of
        whether the OOOI-phase chain above got an ADS-B hit. 2026-07-27:
        FDPS's checker had a matching bug that meant it never actually
        fired (see _check_flight_fdps_cache docstring); FIDS was never
        wired into this sweep at all (see _check_flight_fids docstring).
        Both fixed/added same day.

        Triggers: OUT, OFF, ON, IN, delay >15min, delay >30min, diversion,
        FDPS status/destination/cancellation, FIDS gate/baggage/status.
        Standing directive: all watchlist flights use this trigger set --
        transient and permanent entries alike (get_active_entries below
        already returns both, no tier filter).
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
                        # airplanes.live is the OOOI-phase source; schedule
                        # inference is its ADS-B-dark fallback only.
                        hit = _check_flight_airplanes_live(entry, ident)
                        if not hit:
                            _check_flight_schedule_inference(entry, ident)

                    # Independent of OOOI-phase hit/miss above.
                    _check_flight_fdps_cache(entry, ident)
                    _check_flight_fids(entry, ident)
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


def _acars_reason_context(ident: str, registration: str | None) -> str:
    """2026-08-10: shared sub-check for diversion/OOOI alerts, per operator
    request -- attach whatever recent ACARS/VDL traffic exists for this
    flight so a diversion or phase-change alert carries actual context
    instead of a bare notice. Uses common.acars.get_recent_message_texts
    (raw messages, not a keyword-guessed "reason" -- see that function's
    docstring for why guessing a cause from message text isn't done).

    Always returns a non-empty string -- an explicit "no ACARS traffic
    found" is meaningfully different from silence in an alert body (the
    reader needs to know this WAS checked, not just that nothing appeared)."""
    try:
        from common.acars import get_recent_message_texts
        msgs = get_recent_message_texts(ident, registration=registration, limit=3)
    except Exception as e:
        log.debug("%s: ACARS reason sub-check failed: %s", ident, e)
        return "ACARS: sub-check failed, see logs"
    if not msgs:
        return "ACARS: no recent messages found (last 3h)"
    lines = [f"ACARS ({m['source']}, {m.get('label') or '?'}): {m['text'][:200]}" for m in msgs]
    return "\n".join(lines)


# 2026-08-13: standing directive -- prefer the local FAA/OpenSky registry
# tables for registration/tail -> hex resolution over asking airplanes.live's
# own /v2/reg/ endpoint, which depends on that specific aircraft currently
# broadcasting AND airplanes.live's reg-lookup working at all (confirmed
# live tonight: this box is currently getting 403'd by api.airplanes.live
# entirely). The local registries are deterministic (a tail's hex barely
# ever changes) and don't depend on any external service being reachable.
# airplanes.live is still the only source for LIVE POSITION -- this only
# changes how we get FROM a tail number TO the hex we then ask
# airplanes.live's /v2/hex/ endpoint about.
#
# 2026-08-22: _local_registry_hex_lookup itself moved to
# shared/watchlist.py alongside the rest of identity resolution (see
# resolve_flight_identity's docstring) -- this comment block stays here as
# the historical rationale for the lookup-priority order it now lives
# inside of.


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
    from datetime import datetime, timezone
    from shared.watchlist import watchlist_event_hit, resolve_flight_identity

    # 2026-08-22: identity resolution (hex/tail lookup + hex-lock + the
    # "identity resolved" notification) was extracted into
    # shared.watchlist.resolve_flight_identity() so ingest-side TFMS
    # OUT-transition handling can force the same resolve immediately
    # instead of waiting for this sweep's own interval -- see that
    # function's docstring for the full history/rationale. Behavior here
    # is unchanged; only the call site moved.
    expected_hex = (entry.get("hex_id") or "").lower().strip() or None
    ac = resolve_flight_identity(entry, ident, source="sweep")
    if ac is None:
        return False
    resolved_via_hex = ac.pop("_resolved_via_hex", False)
    hex_id   = (ac.get("hex") or "").lower().strip()
    reg      = ac.get("r") or ""
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

    # Detect diversion: FMS dest differs from watchlist destination.
    #
    # 2026-08-10: hardened with an FDPS cross-check, same "requires
    # confirmation before firing hard" discipline the OOOI block below
    # already applies (2026-07-28 operator directive, after the UA6203
    # false-positive) -- a single ADS-B FMS `dst` field read is real
    # telemetry but can be stale or mid-reroute-clearance rather than a
    # genuine diversion. When FDPS's own filed flight plan for this
    # callsign agrees on the new destination, this fires as CONFIRMED
    # (priority 5); when FDPS hasn't caught up yet or has no data, it
    # still fires -- silence would be worse than a single-source flag --
    # but labeled SUSPECTED (priority 4) so the reader knows this is
    # ADS-B-only evidence, not corroborated. Either way, a recent-ACARS-
    # traffic sub-check (_acars_reason_context) is attached so the alert
    # carries actual context instead of a bare destination-changed line.
    # (Previously computed a `detail` string with a tracking-URL line that
    # was never actually used in the watchlist_event_hit call below --
    # fixed as part of this same edit.)
    expected_dest = (entry.get("destination") or "").upper().replace("K", "", 1)
    if dest_icao and expected_dest and dest_icao.upper() not in (expected_dest, "K" + expected_dest):
        fdps_plan = None
        try:
            fdps_plan = db.get_flight_plan_by_callsign(ident)
        except Exception as e:
            log.debug("%s: FDPS cross-check for diversion failed: %s", ident, e)
        fdps_dest = (fdps_plan or {}).get("destination") or ""
        confirmed = bool(fdps_dest) and fdps_dest.upper().lstrip("K") == dest_icao.upper().lstrip("K")
        label = "DIVERSION CONFIRMED (FDPS agrees)" if confirmed else "SUSPECTED DIVERSION (ADS-B only, FDPS not yet confirming)"
        divert_summary = f"{ident} {label} -- now tracking to {dest_icao} (expected {entry.get('destination','')})"
        tracking = f"https://globe.airplanes.live/?icao={hex_id}" if hex_id else ""
        acars_ctx = _acars_reason_context(ident, entry.get("registration") or reg)
        detail_lines = [divert_summary]
        if tracking:
            detail_lines.append(f"Track: {tracking}")
        detail_lines.append(acars_ctx)
        detail = "\n".join(detail_lines)
        watchlist_event_hit(entry["id"], detail,
                            {"watchlist_trigger": "diversion", "identifier": ident,
                             "hex": hex_id, "diverted_to": dest_icao,
                             "tracking_url": tracking, "confirmed": confirmed,
                             "acars_context": acars_ctx},
                            priority=5 if confirmed else 4)

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
        # 2026-07-28: operator directive -- "ACARS/FDPS/FIDS/VDL IS the sole
        # authority of any oooi alert period going forward." Supersedes the
        # on/in-only gate added earlier the same day (after the UA6203
        # false-landed incident) -- that gate covered arrival-side only,
        # and the SAME flight then produced a false OUT/OFF on its
        # departure side hours later (a CLT gate pushback-and-return
        # misread by the alt/gs heuristic as wheels-up), confirmed against
        # a live FlightAware screenshot plus FDPS's own re-proposed flight
        # plan showing it never actually departed. ADS-B-derived
        # current_phase is still computed above (needed for diversion
        # detection, hex-lock, and position telemetry) but can no longer
        # fire or persist ANY of the four OOOI transitions on its own --
        # every phase now requires ACARS, FIDS, or (OFF only -- see
        # _fdps_confirms_off's docstring for why it's scoped that narrowly)
        # FDPS confirmation before this block writes anything. VDL2 is
        # already folded into the ACARS check (common.acars.get_latest_phase
        # pulls ACARS/VDL2/HFDL uniformly) -- no separate VDL path exists
        # or is needed.
        confirm_src = None
        if acars:
            confirm_src = "acars"
        else:
            try:
                if _fids_confirms_phase(entry, ident, current_phase):
                    confirm_src = "fids"
            except Exception as e:
                log.debug("%s: FIDS phase confirmation check failed: %s", ident, e)
            if not confirm_src and current_phase == "off":
                try:
                    if _fdps_confirms_off(ident, hex_id):
                        confirm_src = "fdps"
                except Exception as e:
                    log.debug("%s: FDPS phase confirmation check failed: %s", ident, e)

        if not confirm_src:
            log.debug(
                "%s: ADS-B suggests %s->%s but no ACARS/FIDS/FDPS confirmation "
                "yet -- holding phase at %s, not firing/persisting",
                ident, last_phase, current_phase, last_phase,
            )
            return True

        summary, priority = event_map[event_key]
        # 2026-07-28: credit the actual source that determined this phase
        # transition instead of a bare generic string. `acars` (set above,
        # unconditionally, before the phase-derivation branch) is truthy
        # exactly when _acars_phase() returned an authoritative OOOI message
        # for this flight -- that data (source system, label, msg_time) was
        # already being computed and logged, just never surfaced in the
        # actual push text riders see. Operator flagged this directly:
        # alerts were reading as generic ADS-B-derived even when a real
        # ACARS OUT/OFF/ON/IN transmission is what actually confirmed it.
        # "ADS-B derived" can no longer appear here at all -- confirm_src
        # is guaranteed non-None by the gate above.
        if confirm_src == "acars":
            src_label = acars_msg.get("label") or acars_msg.get("_source") or "ACARS"
            summary = f"{summary} — confirmed via ACARS ({src_label})"
        elif confirm_src == "fids":
            summary = f"{summary} — confirmed via FIDS"
        else:
            summary = f"{summary} — confirmed via FDPS"
        if tracking_url:
            summary_full = summary + "\n" + tracking_url
        else:
            summary_full = summary
        # 2026-08-10: same ACARS-context sub-check as the diversion alerts,
        # per operator request to cover "any diversion alert, diversion
        # change, or OOOI watchlist alert." Mildly redundant with
        # acars_msg above when confirm_src=="acars" (that's the single
        # message that confirmed THIS transition) -- kept anyway since
        # get_recent_message_texts can surface additional recent traffic
        # beyond just the one confirming message.
        acars_ctx = _acars_reason_context(ident, entry.get("registration") or reg)
        summary_full = summary_full + "\n" + acars_ctx
        watchlist_event_hit(entry["id"], summary_full,
                            {"watchlist_trigger": f"oooi_{current_phase}",
                             "identifier": ident, "hex": hex_id, "reg": reg,
                             "alt_ft": alt, "gs_kt": gs, "lat": lat, "lon": lon,
                             "phase": current_phase, "acars_context": acars_ctx,
                             "source": "acars" if acars else "adsb",
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



def _fids_shows_landed(entry: dict, ident: str) -> bool:
    """
    True if DCA/IAD FIDS currently reports this flight as landed/arrived.
    Reuses the same airport/carrier/flight-number resolution as
    _check_flight_fids(). Returns False (not "unknown") for any flight not
    routed through DCA/IAD, or on any lookup failure -- callers treat False
    as "no FIDS corroboration available," not as a negative signal.
    """
    import re
    dest = (entry.get("destination") or "").upper()
    origin = (entry.get("origin") or "").upper()
    if dest in ("KDCA", "DCA"):
        airport = "DCA"
    elif dest in ("KIAD", "IAD"):
        airport = "IAD"
    elif origin in ("KDCA", "DCA"):
        airport = "DCA"
    elif origin in ("KIAD", "IAD"):
        airport = "IAD"
    else:
        return False

    m = re.match(r"^([A-Za-z]{2,3})(\d+[A-Za-z]?)$", ident.strip())
    if not m:
        return False
    iata_carrier = _ICAO_TO_IATA_CARRIER.get(m.group(1).upper())
    if not iata_carrier:
        return False
    flight_num = m.group(2)

    try:
        from common.airport_fids import lookup_arrival
        result = lookup_arrival(airport, iata_carrier, flight_num)
    except Exception:
        return False
    if not result:
        return False
    status_lower = (result.get("status") or "").lower()
    return "land" in status_lower or "arrived" in status_lower


# 2026-07-28 operator directive: "ACARS/FDPS/FIDS/VDL IS the sole authority
# of any oooi alert period going forward." VDL2 is already folded into
# ACARS (common.acars.get_latest_phase pulls from ACARS/VDL2/HFDL
# uniformly -- there is no separate VDL check to add). This generalizes
# the landed-only FIDS check above into a full four-phase confirmer so the
# same standard applies to OUT/OFF, not just ON/IN.
_FIDS_STATUS_TO_PHASE = {
    "outgate":    "out",
    "inair":      "off",
    "landed":     "on",
    "ingate":     "in",
    "in customs": "in",
}


def _fids_confirms_phase(entry: dict, ident: str, target_phase: str) -> bool:
    """
    True if DCA/IAD FIDS currently reports this flight at the given target
    OOOI phase. Checks the arrival-side FIDS record if the flight's
    destination is DCA/IAD (arrival records carry full-lifecycle status --
    OutGate/InAir/Landed/InGate all appear on the SAME arrival record as
    the flight progresses), or the departure-side record via
    lookup_departure() if the origin is DCA/IAD. Returns False (not
    "unknown") for any flight not routed through DCA/IAD, any status that
    doesn't map to target_phase, or on any lookup failure -- callers treat
    False as "no FIDS corroboration available," never as a negative signal.
    """
    import re
    dest = (entry.get("destination") or "").upper()
    origin = (entry.get("origin") or "").upper()
    m = re.match(r"^([A-Za-z]{2,3})(\d+[A-Za-z]?)$", ident.strip())
    if not m:
        return False
    iata_carrier = _ICAO_TO_IATA_CARRIER.get(m.group(1).upper())
    if not iata_carrier:
        return False
    flight_num = m.group(2)

    result = None
    try:
        from common.airport_fids import lookup_arrival, lookup_departure
        if dest in ("KDCA", "DCA"):
            result = lookup_arrival("DCA", iata_carrier, flight_num)
        elif dest in ("KIAD", "IAD"):
            result = lookup_arrival("IAD", iata_carrier, flight_num)
        elif origin in ("KDCA", "DCA"):
            result = lookup_departure("DCA", iata_carrier, flight_num)
        elif origin in ("KIAD", "IAD"):
            result = lookup_departure("IAD", iata_carrier, flight_num)
    except Exception as e:
        log.debug("%s: FIDS phase confirmation lookup failed: %s", ident, e)
        return False
    if not result:
        return False
    status = (result.get("status") or "").strip().lower()
    return _FIDS_STATUS_TO_PHASE.get(status) == target_phase


def _fdps_confirms_off(ident: str, hex_id: str | None) -> bool:
    """
    True if FDPS's own flight-plan status corroborates OFF (airborne).
    Deliberately narrow: the only FDPS status value confirmed live to map
    cleanly to an OOOI phase is fdpsFlightStatus="ACTIVE" (an activated IFR
    flight plan is FDPS's equivalent of airborne). No FDPS status has been
    observed that cleanly maps to OUT, ON, or IN -- inventing those
    mappings without a verified live sample would repeat the exact mistake
    flagged in feedback_swim_parser_verification.md, so this only ever
    confirms "off" and is never called for any other target phase.

    Matches by hex (embedded in raw_json as aircraftAddress) rather than
    by airline+flight_num string, because FDPS files under the OPERATING
    carrier (e.g. ASH/Mesa for a UA-marketed regional leg), not the
    marketing identifier riders search under -- confirmed live on
    UA6203/ASH6203. Bounded to a 2h updated_at window so this stays an
    indexed range scan (idx_flight_events_updated_at), never a full-table
    LIKE scan across a quarter-million-row table.
    """
    if not hex_id:
        return False
    import re as _re
    m = _re.match(r"^([A-Za-z]{2,3})(\d+[A-Za-z]?)$", ident.strip())
    if not m:
        return False
    flight_num = m.group(2)
    hex_upper = hex_id.upper()
    try:
        with db.conn() as c:
            rows = c.execute(
                """
                SELECT status, raw_json FROM flight_events
                WHERE flight_num = ? AND updated_at > ?
                ORDER BY updated_at DESC LIMIT 20
                """,
                (flight_num, time.time() - 7200),
            ).fetchall()
    except Exception as e:
        log.debug("%s: FDPS phase confirmation query failed: %s", ident, e)
        return False
    for row in rows:
        raw = row["raw_json"] or ""
        if hex_upper not in raw.upper():
            continue
        status = (row["status"] or "").strip().lower()
        return status == "active"
    return False


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
            # 2026-07-28 operator directive: the DC-metro local UltraFeeder
            # receiver does not have reliable sky coverage for terminal-area
            # confirmations. "ADS-B dark" (this function's own trigger
            # condition) was firing landed guesses 15-20 minutes before
            # actual arrival on multiple flights. ADS-B/schedule-based
            # inference alone must NEVER independently confirm IN/landed --
            # require positive corroboration from ACARS or FIDS first.
            acars_check = None
            try:
                acars_check = _acars_phase(ident, registration=entry.get("registration"))
            except Exception as e:
                log.debug("schedule infer ACARS check %s: %s", ident, e)

            acars_confirms = bool(acars_check and acars_check[0] in ("on", "in"))
            acars_contradicts = bool(acars_check and acars_check[0] not in ("on", "in"))

            fids_confirms = False
            if not acars_confirms:
                try:
                    fids_confirms = _fids_shows_landed(entry, ident)
                except Exception as e:
                    log.debug("schedule infer FIDS check %s: %s", ident, e)

            if acars_contradicts:
                log.info(
                    "%s: schedule-inferred IN suppressed -- ACARS shows phase=%s instead",
                    ident, acars_check[0],
                )
                return

            if not (acars_confirms or fids_confirms):
                log.debug(
                    "%s: schedule window for IN reached (%s) but neither ACARS nor "
                    "FIDS confirms yet -- holding, ADS-B-dark alone is not sufficient",
                    ident, reason,
                )
                return

            confirm_src = "ACARS" if acars_confirms else "FIDS"
            acars_ctx = _acars_reason_context(ident, entry.get("registration"))
            summary = f"{ident} IN — at gate ({confirm_src}-confirmed, ADS-B dark)\n{acars_ctx}"
            watchlist_event_hit(
                entry["id"], summary,
                {"watchlist_trigger": "oooi_in_inferred",
                 "identifier": ident,
                 "note": reason, "acars_context": acars_ctx},
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
                    acars_ctx = _acars_reason_context(ident, entry.get("registration"))
                    summary = f"{ident} OFF — departed (schedule inferred, ADS-B not seen)\n{acars_ctx}"
                    watchlist_event_hit(
                        entry["id"], summary,
                        {"watchlist_trigger": "oooi_off_inferred",
                         "identifier": ident, "scheduled_departure": sched_dep,
                         "note": "No ADS-B contact — departure inferred from schedule",
                         "acars_context": acars_ctx},
                        priority=4,
                    )
                    db.update_watchlist_oooi_phase(
                        entry["id"], "off", now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    )
                    log.info("flight schedule infer: %s OFF (past dep+90m)", ident)
            except Exception as e:
                log.debug("schedule infer dep %s: %s", ident, e)

def _check_flight_fdps_cache(entry: dict, ident: str) -> None:
    """Check FAA FDPS (SWIM/SFDPS FIXM feed, see ingest/parsers/fdps_parser.py)
    for a confirmed flight plan matching this callsign. Fires on a
    cancellation, a tail/airframe reassignment (2026-08-10, see the
    dedicated block below), or a destination change (diversion signal);
    otherwise fires a lower-priority status note only when the status
    actually changed since last tick.

    2026-07-27: replaced a matching predicate that compared the ICAO
    callsign (e.g. 'UAL2185') against flight_events.flight_id (a GUFI/UUID
    -- never equal to a callsign) or flight_num alone (e.g. '2185', missing
    the airline prefix -- also never equal to 'UAL2185'). Neither branch
    could ever match, so this function has fired zero real events since it
    was written despite flight_events holding 200k+ real rows -- this is
    the actual reason FDPS looked "disconnected" from OOOI alerts. Now uses
    db.get_flight_plan_by_callsign, which splits the callsign correctly and
    is backed by an index (SCHEMA_V22) instead of a per-tick 600s-window
    full-table Python scan.

    2026-07-27 (same day, follow-up fix): this function originally compared
    its own status string against the SHARED entry["last_event_summary"]
    field -- but that field is overwritten by every other check type
    (OOOI, FIDS, proximity, schedule inference) too, so an intervening fire
    from any of those made this function see a false "changed" status and
    re-fire an unchanged FDPS status. Now reads/writes its own dedicated
    last_fdps_status column (SCHEMA_V23), same pattern as oooi_phase.
    Destination changes are now also persisted onto the entry itself
    (db.update_watchlist_destination) so the diversion event converges
    instead of re-firing every tick forever.

    Called every tick regardless of ADS-B hit status (unlike the OOOI-phase
    chain above) -- flight-plan-level changes like a cancellation or
    destination change are meaningful even while the aircraft is still
    tracking normally on ADS-B.
    """
    from shared.watchlist import watchlist_event_hit
    from datetime import datetime, timezone
    try:
        plan = db.get_flight_plan_by_callsign(ident)
        if not plan:
            return
        status = (plan.get("status") or "").lower()
        dest = plan.get("destination")
        last_status = (entry.get("last_fdps_status") or "").lower()
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        if status == "cancelled" and last_status != "cancelled":
            watchlist_event_hit(
                entry["id"], f"{ident} FDPS: flight plan CANCELLED",
                {"watchlist_trigger": "fdps_cancelled", "identifier": ident},
                priority=4,
            )
            db.update_watchlist_fdps_status(entry["id"], status, now_iso)
            return

        # 2026-08-10: tail/airframe reassignment detection -- FDPS's
        # aircraftDescription carries a real aircraftAddress (hex) +
        # registration for a filed flight plan (see
        # _extract_aircraft_hex_registration in common/db.py). A watchlist
        # entry added against one airframe can have a DIFFERENT one
        # assigned by the time of a later refile/replan for the same
        # callsign (equipment swap) -- worth its own explicit alert,
        # distinct from a destination change or generic status note, since
        # every subsequent ADS-B/ACARS poll needs the NEW hex to keep
        # tracking the right physical aircraft. Only compared when FDPS
        # actually has both a hex and a prior hex to compare -- a flight
        # plan with no aircraft assigned yet (hex is None) is not a "tail
        # change", just an equipment assignment still pending.
        new_hex = plan.get("hex")
        new_reg = plan.get("registration")
        prior_hex = entry.get("hex_id")
        if new_hex and prior_hex and new_hex.upper() != prior_hex.upper():
            watchlist_event_hit(
                entry["id"],
                f"{ident} FDPS: TAIL CHANGE {prior_hex}/{entry.get('registration') or '?'} "
                f"→ {new_hex}/{new_reg or '?'}",
                {"watchlist_trigger": "fdps_tail_change", "identifier": ident,
                 "prior_hex": prior_hex, "new_hex": new_hex,
                 "prior_registration": entry.get("registration"),
                 "new_registration": new_reg},
                priority=4,
            )
            db.update_watchlist_hex_registration(entry["id"], new_hex, new_reg)
            db.update_watchlist_fdps_status(entry["id"], status, now_iso)
            return

        prior_dest = entry.get("destination")
        if dest and prior_dest and dest.upper() != prior_dest.upper():
            # 2026-08-10: an FDPS-confirmed flight-plan destination change
            # is itself a CONFIRMED diversion (this is the authoritative
            # source, not an ADS-B inference -- see the "diversion"
            # trigger in _check_flight_airplanes_live for the ADS-B-only/
            # unconfirmed counterpart). Same ACARS-context sub-check
            # attached per operator request.
            acars_ctx = _acars_reason_context(ident, plan.get("registration") or entry.get("registration"))
            summary = (f"{ident} FDPS: destination changed {prior_dest}→{dest}\n" + acars_ctx)
            watchlist_event_hit(
                entry["id"], summary,
                {"watchlist_trigger": "fdps_destination_change",
                 "identifier": ident, "prior_destination": prior_dest,
                 "new_destination": dest, "acars_context": acars_ctx},
                priority=4,
            )
            db.update_watchlist_destination(entry["id"], dest.upper())
            db.update_watchlist_fdps_status(entry["id"], status, now_iso)
            return

        if status and status != last_status:
            watchlist_event_hit(
                entry["id"], f"{ident} FDPS: {status}",
                {"watchlist_trigger": "fdps_status", "status": status,
                 "identifier": ident},
                priority=3,
            )
            db.update_watchlist_fdps_status(entry["id"], status, now_iso)
    except Exception as e:
        log.debug("fdps cache %s: %s", ident, e)


# ICAO callsign prefix -> IATA carrier code, for FIDS lookups below (MWAA's
# feed is IATA-keyed). Not exhaustive -- covers carriers actually seen at
# DCA/IAD plus common internationals already known to flight-hifi-track.
# A carrier missing from this map just means _check_flight_fids no-ops for
# it (same as a genuine FIDS miss), not an error.
_ICAO_TO_IATA_CARRIER = {
    "AAL": "AA", "UAL": "UA", "DAL": "DL", "SWA": "WN", "JBU": "B6",
    "ASA": "AS", "NKS": "NK", "FFT": "F9", "RPA": "YX", "ENY": "MQ",
    "ASH": "YX", "SKW": "OO", "EDV": "9E", "BAW": "BA", "KLM": "KL",
    "AFR": "AF", "DLH": "LH", "ACA": "AC", "VIR": "VS", "QTR": "QR",
    "UAE": "EK", "ETD": "EY",
}


def _check_flight_fids(entry: dict, ident: str) -> None:
    """Check DCA/IAD FIDS (MWAA feed, poller.fetchers.dca_fids/iad_fids,
    common/airport_fids.py) for gate/baggage/status on a watchlisted flight,
    firing when any of those change since last tick.

    2026-07-27: net-new wiring, not a reconnect -- FIDS was added
    2026-07-14 (commit 74db2c2) purely as an on-demand
    GET /api/v1/fids/{airport}/{flight} lookup (what hub-arrivals-lookup and
    flight-hifi-track's baggage-claim step call). It was never hooked into
    the continuous OOOI watchlist sweep at all; there was nothing to
    disconnect. FIDS only covers DCA and IAD (MWAA-operated), so this is a
    genuine no-op for any entry not routed through one of those two.
    """
    import re
    dest = (entry.get("destination") or "").upper()
    origin = (entry.get("origin") or "").upper()
    if dest in ("KDCA", "DCA"):
        airport = "DCA"
    elif dest in ("KIAD", "IAD"):
        airport = "IAD"
    elif origin in ("KDCA", "DCA"):
        airport = "DCA"
    elif origin in ("KIAD", "IAD"):
        airport = "IAD"
    else:
        return

    m = re.match(r"^([A-Za-z]{2,3})(\d+[A-Za-z]?)$", ident.strip())
    if not m:
        return
    iata_carrier = _ICAO_TO_IATA_CARRIER.get(m.group(1).upper())
    if not iata_carrier:
        return
    flight_num = m.group(2)

    try:
        from common.airport_fids import lookup_arrival
        result = lookup_arrival(airport, iata_carrier, flight_num)
    except Exception as e:
        log.debug("fids check %s: %s", ident, e)
        return
    if not result:
        return

    summary = (f"{ident} FIDS {airport}: gate {result.get('gate') or '?'} "
               f"baggage {result.get('baggage') or '?'} "
               f"{result.get('status') or ''}").strip()
    from datetime import datetime, timezone
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # 2026-07-27 follow-up fix #1: this used to compare against the SHARED
    # entry["last_event_summary"] field, which every other check type
    # (FDPS, OOOI, proximity, schedule inference) also overwrites -- an
    # intervening fire from any of those made this see a false "changed"
    # FIDS summary and re-fire an unchanged one. Confirmed root cause of
    # three duplicate "...Landed" pushes for UAL2670 on 2026-07-27, ~5min
    # apart, each sitting between unrelated fdps_status fires. Now compares
    # against its own dedicated last_fids_status column (SCHEMA_V23), same
    # pattern as oooi_phase / last_fdps_status.
    last_status = entry.get("last_fids_status") or ""
    if summary.lower() == last_status.lower():
        return

    # 2026-07-27 follow-up fix #2: MWAA's own FIDS display reported "Landed"
    # for UAL2670 at 17:27z, well before the aircraft was actually at the
    # gate (ADS-B-confirmed oooi_in didn't fire until 17:42z) -- a real
    # quirk in the airport display system, not a bug in our polling. FIDS
    # is not authoritative for "landed"; ADS-B-driven OOOI is. Suppress a
    # landed-type FIDS claim until OOOI's own phase independently agrees the
    # aircraft is in ("in") -- still cache the status (so it doesn't
    # re-evaluate as "changed" every tick and doesn't emit a duplicate once
    # OOOI does catch up), just don't push it standalone.
    status_lower = (result.get("status") or "").lower()
    if "land" in status_lower and entry.get("oooi_phase") != "in":
        # 2026-07-28: was gated on oooi_phase == "in" (ADS-B/OOOI agreement)
        # -- but ADS-B is no longer a trusted independent source for landed
        # confirmation (local DC-metro receiver coverage gaps were firing
        # false-early "landed" pushes 15-20 min before actual arrival), so
        # requiring it to have already agreed would make FIDS landed claims
        # almost never fire. ACARS is the check-and-balance now instead:
        # trust FIDS unless ACARS actively says otherwise.
        acars_check = None
        try:
            acars_check = _acars_phase(ident, registration=entry.get("registration"))
        except Exception as e:
            log.debug("fids landed ACARS cross-check %s: %s", ident, e)
        if acars_check and acars_check[0] not in ("on", "in"):
            db.update_watchlist_fids_status(entry["id"], summary, now_iso)
            log.debug("fids %s: suppressing landed claim -- ACARS shows phase=%s instead",
                      ident, acars_check[0])
            return
        log.info("fids %s: landed claim accepted (FIDS-confirmed, ACARS phase=%s)",
                  ident, acars_check[0] if acars_check else "unavailable")

    from shared.watchlist import watchlist_event_hit
    watchlist_event_hit(
        entry["id"], summary,
        {"watchlist_trigger": "fids_update", "identifier": ident,
         "airport": airport, "gate": result.get("gate"),
         "baggage": result.get("baggage"), "status": result.get("status")},
        priority=3,
    )
    db.update_watchlist_fids_status(entry["id"], summary, now_iso)


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

    # 2026-07-28 (vessel_events parity with flight_events): persist every
    # vessel this bbox query returns, not just the one this watchlist entry
    # is looking for. Without this, DC-area water-taxi/cruise traffic (the
    # operator's National Harbor<->Alexandria example) would only ever
    # accumulate history for MMSIs someone happened to hand-watchlist --
    # the same gap codeshare_map was built to close on the flight side.
    # Best-effort, one row at a time -- a persistence hiccup on one vessel
    # must never block the watchlist-match logic below or the rest of the
    # sweep.
    for v in raw:
        try:
            db.insert_vessel_event(
                mmsi=str(v.get("MMSI") or ""),
                name=(v.get("NAME") or "").strip() or None,
                lat=v.get("LATITUDE"), lon=v.get("LONGITUDE"),
                sog=v.get("SOG"), cog=v.get("COG"), hdg=v.get("HEADING"),
                nav_status=v.get("NAVSTAT"), ship_type=v.get("TYPE"),
                source="aishub.net",
            )
        except Exception:
            pass

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
    db.init_db_v21()
    db.init_db_v22()
    db.init_db_v23()
    db.init_db_v24()
    db.init_db_v25()
    db.init_db_v26()
    db.init_db_v27()
    db.init_db_v28()
    db.init_db_v29()
    db.init_db_v30()
    db.init_db_v31()
    db.init_db_v32()
    db.init_db_v33()
    db.init_db_v34()
    db.init_db_v35()
    db.init_db_v36()
    db.init_db_v37()

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
