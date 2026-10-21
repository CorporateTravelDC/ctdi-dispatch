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
import os
import pathlib
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from common import config, db, db_backend, maintenance_window
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
    # push_feed / push_max_age for the three push-gated fetchers come from
    # ingest.failover.REST_FALLBACKS (2026-09-07, single source of truth
    # shared with web/main.py's push_covers / stale_thresholds).
    {"name": "nws",           "module": "poller.fetchers.nws",           "interval": 300,
     **failover.REST_FALLBACKS["nws"]},
    {"name": "notam",         "module": "poller.fetchers.notam",         "interval": 300,
     **failover.REST_FALLBACKS["notam"]},
    # 2026-09-06 (work-order item 1): amtrak wired in as the REST fallback
    # for push:amtrak. Both push writers (amtrak-tracker container --
    # deployed primary -- and ingest.amtrak inside ingest-core) stamp the
    # heartbeat once per successful 300 s poll, not every 30 s like the
    # SWIM/NWWS heartbeats, so the default FALLBACK_MAX_AGE (90) would read
    # a perfectly healthy push as dead 70% of the time and double-write.
    # push_max_age = 2x poll + margin. Output (db.insert_amtrak_status +
    # feed_state "amtrak") is the same row the push writers produce; read by
    # /api/v1/amtrak, ops_brief, transport_pattern_digest, train_impact.
    {"name": "amtrak",        "module": "poller.fetchers.amtrak",        "interval": 300,
     **failover.REST_FALLBACKS["amtrak"]},
    # 2026-09-06: fdps/stdds/tbfm/tfms/itws have NO REST twin. Operator
    # decision after review of what public sources actually carry (OpenSky
    # = ADS-B positions only, no flight plans; nothing public for TBFM
    # metering or ASDE-X surface): "keep the three that are hardened, and
    # we'll just take the L on the others until something comes
    # available." See docs/REST_FALLBACK_AUDIT_2026-09-06.md section 5.
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
    # 2026-09-23: direct sibling of flight-cleanup above (operator directive:
    # "mirror the flight events log behavior ... for the surface movement").
    # surface_movement_events had NO retention path at all -- absent from
    # retention_prune's _PRUNE_JOBS, accumulating since 2026-08-03.
    #
    # Archives rather than prunes, because this table is the only correlation
    # substrate for the SMES bitmask decoding work; a plain DELETE would
    # destroy that dataset permanently. See the skill's own header.
    #
    # maintenance_only, unlike flight-cleanup: the first run has ~12,350
    # eligible rows to tar, upload and delete, and that is bulk WebDAV I/O on
    # a box whose alert path shares two cores. Steady-state volume is small,
    # but the backlog run should not land mid-afternoon.
    {"name": "surface-cleanup", "script": "poller/skills/surface_events_cleanup.py",
     "interval": 86400, "timeout": 900, "maintenance_only": True},
    # 2026-09-22: audit-log-prune REMOVED from this schedule and replaced by
    # audit-log-archive. The prune job deleted audit rows past 90 days with
    # nothing left behind; the operator retention decision is that audit rows
    # are never destroyed, only checkpointed, signed, moved off-box, and
    # reduced on-device to a signed stub. Both jobs must never be scheduled
    # together: whichever ran first would win, and if that were the prune the
    # rows would be gone before the archive could sign them. See
    # poller/skills/audit_log_prune.py, which is now a refusing stub.
    #
    # maintenance_only: this tars, signs, and uploads batches over WebDAV --
    # report-tier work that belongs in the quiet hours alongside the
    # systemd-governed jobs, not competing with the alert path at noon.
    {"name": "audit-log-archive", "script": "poller/skills/audit_log_archive.py",
     "interval": 86400, "timeout": 900, "maintenance_only": True},
    # 2026-08-26 (Opus blind review C-33): same "written but never wired"
    # failure mode as flight-cleanup/audit-log-prune above -- see
    # retention_prune.py's module docstring.
    {"name": "retention-prune", "script": "poller/skills/retention_prune.py", "interval": 86400},
]

# Daily/weekly skills are handled by systemd timers, not this scheduler.
# daily-brief: 05:00 ET
# freshness-audit: 06:00 ET
# weekly-summary: Sun 18:00 ET


# A push heartbeat fresher than this means push owns the data; poller skips REST.
# Must exceed ingest heartbeat interval (30s) with margin to avoid flapping.
# Defined in ingest.failover since 2026-09-07 (shared with web); re-exported
# here because FetchLoop and the guardrail tests read it from this module.
FALLBACK_MAX_AGE = failover.FALLBACK_MAX_AGE  # seconds

# 2026-09-06 (operator directive): NOTHING fires immediately on a restart.
# Every FetchLoop/SkillLoop used to start with _last_run = 0.0, so on the
# first 10 s tick after any start every fetcher, every skill (8 of them,
# several LLM-backed) and every piled-up trigger file ran at once -- on top
# of the SWIM feeds draining their durable-queue backlog. Measured
# 2026-09-06: that cold-start burst is ~+20 load1 on a 4-core box whose
# baseline is 15-20 whenever an LLM job is alive, i.e. it crosses the
# thermal-ingest-guard's LOCKDOWN line (40) about half the time, and last
# night the same burst drew a 429 from the FAA NMS API. Now: a hold-off
# after start during which no scheduled job, trigger or sweep runs, then a
# fixed stagger so jobs become eligible one at a time instead of all at
# once. Both tunable via dispatch.env; defaults chosen so a 300 s fetcher
# first fires ~2-5 min after start and the guard's 2-min cycle sees the
# ingest backlog drain and the poller's first jobs as separate humps.
POLLER_STARTUP_HOLDOFF_S = float(os.environ.get("POLLER_STARTUP_HOLDOFF_S", "120"))
POLLER_STARTUP_STAGGER_S = float(os.environ.get("POLLER_STARTUP_STAGGER_S", "15"))

# 2026-09-06 (work-order item 1, REST fallbacks): push-gated fetchers get a
# second grace on top of the hold-off. After a LOCKDOWN restore or any
# stack restart the push heartbeats are stale by construction -- the feeds
# were stopped -- and the push containers are still connecting/draining
# when the poller's nws/notam/amtrak slots open at +165/+180/+195 s. Without
# this, the first eligible tick reads "push stale" and fires a REST pull
# purely because of the shed, which is exactly the burst the hold-off was
# meant to remove (NMS 429, 2026-09-05). Rule: a heartbeat that predates
# THIS poller process is not evidence the push feed is down until the
# poller has been up for HOLDOFF + PUSH_GRACE; a heartbeat stamped after
# boot that then goes stale (the feed came up and died) falls back at once
# as before. Default = the hold-off itself; 0 disables the grace.
POLLER_STARTUP_PUSH_GRACE_S = float(
    os.environ.get("POLLER_STARTUP_PUSH_GRACE_S", str(POLLER_STARTUP_HOLDOFF_S)))
# Wall-clock time this poller process started; main() resets it. Module
# level so FetchLoop can compare heartbeat stamps against it.
_boot_time = time.time()


def push_grace_active(feed: str, now: float | None = None) -> bool:
    """True while a stale push heartbeat for <feed> should be ignored because
    it went stale before this poller started and the grace window is open."""
    if POLLER_STARTUP_PUSH_GRACE_S <= 0:
        return False
    now = time.time() if now is None else now
    if now >= _boot_time + POLLER_STARTUP_HOLDOFF_S + POLLER_STARTUP_PUSH_GRACE_S:
        return False
    last_seen = failover.push_last_seen(feed)
    return last_seen is None or last_seen < _boot_time


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
                 active_check: str | None = None,
                 push_max_age: float | None = None):
        self.name = name
        self.module_name = module
        self.interval = interval
        self.push_feed = push_feed
        # 2026-09-06: per-feed heartbeat staleness threshold for push twins
        # that stamp per-poll rather than every 30 s (amtrak). None = the
        # shared FALLBACK_MAX_AGE.
        self.push_max_age = push_max_age if push_max_age is not None else FALLBACK_MAX_AGE
        self.active_interval = active_interval  # faster cadence when trip leg active
        self.active_check = active_check        # watchlist session_type: 'train'|'flight'
        self._last_run = 0.0
        # 2026-09-06: earliest wall-clock time this job may run at all; set
        # by main() to boot + hold-off + this job's stagger slot. Until then
        # maybe_run() is a no-op -- see POLLER_STARTUP_HOLDOFF_S.
        self.not_before = 0.0

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
        if now < self.not_before:
            return
        if now - self._last_run < self._effective_interval():
            return
        if self.push_feed:
            if failover.push_is_healthy(self.push_feed, self.push_max_age):
                self._last_run = now
                log.debug("Fetcher %s: deferring to healthy push source", self.name)
                return
            if push_grace_active(self.push_feed, now):
                # Stale from the shed/restart, not from a live failure --
                # see POLLER_STARTUP_PUSH_GRACE_S. Re-check next interval.
                self._last_run = now
                log.info("Fetcher %s: push:%s heartbeat predates this poller start; "
                         "startup push grace active, not falling back to REST yet",
                         self.name, self.push_feed)
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
                 timeout: int = 120,
                 maintenance_only: bool = False):
        self.name = name
        self.script = script
        self.interval = interval
        self.active_interval = active_interval
        self.active_check = active_check
        self.timeout = timeout
        # maintenance_only: confine this skill to the overnight window.
        # systemd units get this from ExecCondition=maintenance-window-guard.sh,
        # which is a host hook and cannot reach work scheduled inside this
        # process -- common/maintenance_window.py is the in-process mirror.
        self.maintenance_only = maintenance_only
        self._last_run = 0.0
        self.not_before = 0.0  # 2026-09-06: see FetchLoop.not_before
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
        if now < self.not_before:
            return
        if now - self._last_run < self._effective_interval():
            return
        if self._task is not None and not self._task.done():
            log.debug("Skill %s: previous run still in flight (queued behind "
                      "Ollama?) -- skipping this tick", self.name)
            return
        # Window gate sits BEFORE the _last_run stamp on purpose. Stamping
        # first would consume the skill's eligibility for a whole interval
        # every time a closed window turned it away, so a daily job whose
        # eligibility happened to land at noon would never run again. Not
        # stamping means it stays eligible and fires the moment the window
        # opens -- which also makes the run time settle onto the window
        # boundary instead of drifting.
        if self.maintenance_only and not maintenance_window.is_open():
            log.debug("Skill %s: outside maintenance window %s -- holding",
                      self.name, maintenance_window.describe())
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
        self.not_before = 0.0  # 2026-09-06: see FetchLoop.not_before

    async def process(self) -> None:
        if time.time() < self.not_before:
            return
        self.trigger_dir.mkdir(parents=True, exist_ok=True)
        # 2026-09-06: coalesce piled-up refresh_feed triggers per feed. While
        # the poller is down (LOCKDOWN shed, restart) the kickover guardrail
        # keeps dropping one refresh_feed per feed every 5 min; on the next
        # start every one of them ran -- 18 back-to-back NOTAM pulls last
        # night, which is what earned the FAA NMS API's 429. One fetch of a
        # feed satisfies every queued request for it; the extra trigger rows
        # resolve as success with a note instead of each hitting the API.
        seen_feeds: set[str] = set()
        for path in sorted(self.trigger_dir.glob("*.json")):
            try:
                data = json.loads(path.read_text())
                trigger_id = data.get("id")
                trigger_type = data.get("type")
                payload = data.get("payload", {})
                path.unlink(missing_ok=True)  # Consume trigger file.

                if trigger_type == "refresh_feed":
                    feed = payload.get("feed_name")
                    if feed in seen_feeds:
                        log.info("Trigger %s: refresh_feed %s coalesced into the "
                                 "fetch already run this pass", trigger_id, feed)
                        db.resolve_trigger(trigger_id, "success",
                                           f"coalesced: {feed} already refreshed this pass")
                        continue
                    seen_feeds.add(feed)

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
                _sent = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: pusher_main.send_test_alert(
                        _msg, topic=_topic, title=_title, priority=_priority)
                )
                # 2026-10-15 (parity audit / ntfy audit): a failed send was recorded as success.
                if _sent:
                    db.resolve_trigger(trigger_id, "success")
                else:
                    db.resolve_trigger(trigger_id, "failed", "ntfy and Pushover both failed")
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
    OPENSKY_FRESHNESS_INTERVAL = 30 * 86400  # monthly dated-snapshot check
    # Added 2026-07-21. OpenSky's rolling bulk aircraft metadata CSV is a
    # frozen snapshot (confirmed stale since Nov 2024, "on hold" per their
    # own site) -- but real dated monthly snapshots DO appear at a
    # different S3 path. Repointed 2026-09-02 (operator directive) to a
    # cheap prefix-filtered S3 listing against that path instead of the
    # rolling file's dead HEAD check; only pulls the real ~103MB+ import
    # when a genuinely new aircraft-database-complete-YYYY-MM.csv has
    # appeared. See poller/fetchers/opensky_registry.py module docstring.
    # NOT a full-download interval like FAA_REGISTRY_INTERVAL above.

    def __init__(self) -> None:
        self.not_before = 0.0  # 2026-09-06: see FetchLoop.not_before
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
        if now < self.not_before:
            return
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
        FIDS (DCA/IAD gate/baggage~~/status~~, MWAA feed; 2026-10-18: gate,
        terminal and baggage only, via _check_flight_gates) are NOT part of that
        fallback chain -- they're independent plan/airport-system signals,
        so both are checked every tick for every entry regardless of
        whether the OOOI-phase chain above got an ADS-B hit. 2026-07-27:
        FDPS's checker had a matching bug that meant it never actually
        fired (see _check_flight_fdps_cache docstring); FIDS was never
        wired into this sweep at all (see _check_flight_fids docstring).
        Both fixed/added same day.

        Triggers: OUT, OFF, ON, IN, delay >15min, delay >30min, diversion,
        FDPS status/destination/cancellation, resolved gate/baggage
        assignments (~~FIDS gate/baggage/status~~ 2026-10-18: every gate source,
        common.gate_assignments; FIDS is gate/terminal/baggage only).
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
                    _check_flight_gates(entry, ident)
                    _check_flight_inbound(entry, ident)
                    _check_flight_tdes_tail(entry, ident)
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
            skipped_non_amtrak = 0
            for entry in entries:
                ident = entry["identifier"]
                # 2026-09-23 PROVIDER GUARD -- amtraker is an AMTRAK-ONLY
                # feed, but this loop used to query it for EVERY train entry
                # regardless of operator. MARC and VRE train numbers collide
                # with Amtrak numbers, so a VRE entry asking for /trains/334
                # got back a real AMTRAK train 334 and the sweep stamped that
                # Amtrak data with the VRE entry's route name.
                #
                # Observed live 2026-09-23 17:24, immediately after the
                # amtraker unwrap fix made this code path reachable at all:
                #     "#334 VRE Manassas Line ->WAS en route (on time)"
                # -- route name from the entry, everything else from a
                # different railroad. Note the empty origin. train_events
                # rows for MARC/VRE: 0.
                #
                # Fixing the unwrap turned "silently dead" into "confidently
                # wrong" for the 104 MARC/VRE entries. Wrong operational data
                # is worse than none, so an entry whose route is not an
                # Amtrak route is SKIPPED here and produces nothing until its
                # own provider adapter exists (docs/TRAIN_PARITY_DESIGN_2026-09-23.md).
                if not _is_amtrak_route(entry.get("route_name")):
                    skipped_non_amtrak += 1
                    continue
                try:
                    _check_train_amtraker(entry, ident, amtraker_url,
                                          watchlist_event_hit)
                except Exception as e:
                    log.debug("train sweep %s: %s", ident, e)
            if skipped_non_amtrak:
                log.debug("train sweep: skipped %d non-Amtrak entr%s (no provider "
                          "adapter yet -- would otherwise collide with Amtrak "
                          "train numbers)", skipped_non_amtrak,
                          "y" if skipped_non_amtrak == 1 else "ies")

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
# tables for registration/tail -> hex resolution over a third-party
# reg-lookup API, which depends on that specific aircraft currently
# broadcasting AND the external service being reachable at all (confirmed
# live that night: this box was getting 403'd by api.airplanes.live
# entirely). The local registries are deterministic (a tail's hex barely
# ever changes) and don't depend on any external service being reachable.
#
# 2026-08-22: _local_registry_hex_lookup itself moved to
# shared/watchlist.py alongside the rest of identity resolution (see
# resolve_flight_identity's docstring) -- this comment block stays here as
# the historical rationale for the lookup-priority order it now lives
# inside of.
#
# 2026-08-27: airplanes.live removed entirely (operator directive,
# "everything is meant to be local") -- resolve_flight_identity() now
# sources live position from this box's own ADS-B receiver first, then
# already-ingested local FDPS SWIM data, never a third-party API. The
# globe.airplanes.live/?icao= tracking-URL link fired in notifications is
# kept (a click-through convenience for the operator's own phone, not a
# lookup this box performs).


def _check_flight_airplanes_live(entry: dict, ident: str) -> bool:
    """
    Despite the name (kept for minimal call-site diff -- this function is
    called from the main sweep loop and by name in several comments this
    session already updated), this queries LOCAL sources only as of
    2026-08-27: this box's own ADS-B receiver, then already-ingested FDPS
    SWIM data (see shared.watchlist.resolve_flight_identity, which this
    delegates to). <del>No third-party API is queried.</del> Since 2026-10-08
    airplanes.live is queried as a FALLBACK only, after every local source is
    dark (common.airplanes_live; position/identity, never OOOI authority).
    Returns True if data found (even if no new event fired), False if no data.
    Derives OOOI phase from position/altitude/speed.
    Also captures ICAO hex ID and updates watchlist notes.

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
        # 2026-10-05 (JZA825): a first airborne sighting with no departure
        # observed is NOT a takeoff -- it fired "OFF -- airborne" 8 nm from
        # DCA, 70 min after the real off. Labelled for what it is; the
        # airline-reported OFF (TFMS) is appended below when known.
        ("pre_departure", "off"): (f"{ident} first seen airborne (takeoff not observed)", 4),
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
        elif current_phase in ("on", "in"):
            # 2026-10-05 operator directive: ADS-B shall NEVER authoritatively
            # say ON/IN -- and FIDS is never OOOI-authoritative (2026-09-27),
            # so an ADS-B-derived landing "confirmed via FIDS" is two
            # non-authorities agreeing. ON/IN come from ACARS (above), SMES
            # and TFMS (their own parsers) only.
            log.debug("%s: ADS-B suggests %s; ON/IN only from ACARS/SMES/TFMS -- not firing", ident, current_phase)
            return True
        else:
            # 2026-10-12 (docs-parity audit): FIDS used to confirm OUT/OFF here and the phase was then
            # persisted -- FIDS asserting OOOI by proxy, against the 2026-09-27 rule (FIDS is gate +
            # baggage enrichment only). OFF now needs ACARS (above) or FDPS (below); nothing else.
            if current_phase == "off":
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
        if event_key == ("pre_departure", "off"):
            try:
                from shared.watchlist import tfms_departure_state, _et
                _st = tfms_departure_state(ident, entry.get("origin"), entry.get("destination"))
                if _st and _st[0] == "off":
                    summary = f"{summary}; OFF {_et(_st[1])} per airline (TFMS)"
            except Exception as e:
                log.debug("%s: TFMS off-time lookup failed: %s", ident, e)
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
        # Persist the new phase to its own field FIRST -- this is what stops
        # the next check from re-deriving a stale phase and re-firing this
        # same transition. Independent of watchlist_event_hit's own 5-min
        # dedup (which only rate-limits identical event_types; it does not
        # fix mis-detected "new" transitions).
        # (operator 2026-10-16): through the authority gate, naming the source
        # that confirmed the transition (confirm_src: acars | fdps), not the
        # lock-less writer -- so a tier-30 confirmation can never overwrite a
        # SWIM-asserted phase while SWIM is live, and nothing fires unless the
        # write was accepted. The event's "source" field used to claim
        # "acars"/"adsb" even when FDPS was the confirming source.
        if not db.update_watchlist_oooi_phase_authoritative(
                entry["id"], current_phase, source=confirm_src,
                updated_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")):
            log.info("flight OOOI: %s %s->%s from %s REJECTED by the authority gate -- "
                     "phase held, nothing fired", ident, last_phase, current_phase, confirm_src)
            return True
        watchlist_event_hit(entry["id"], summary_full,
                            {"watchlist_trigger": f"oooi_{current_phase}",
                             "identifier": ident, "hex": hex_id, "reg": reg,
                             "alt_ft": alt, "gs_kt": gs, "lat": lat, "lon": lon,
                             "phase": current_phase, "acars_context": acars_ctx,
                             "source": confirm_src,
                             "tracking_url": tracking_url},
                            priority=priority)
        log.info("flight OOOI: %s %s→%s (%s)", ident, last_phase, current_phase, confirm_src)

    return True



# 2026-10-18 (operator, gate batch): the dead FIDS-to-OOOI code that sat here --
# _fids_shows_landed() (no caller) and _FIDS_STATUS_TO_PHASE (no reader) -- was
# removed. FIDS never supplies a time, a status, a tail or an OOOI phase.

# 2026-10-12: the FIDS phase-confirmation helper was removed -- FIDS never confirms an OOOI phase (2026-09-27 rule);
# its last caller (the ADS-B sweep's OUT/OFF confirmation) was taken out the same day.

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

            # 2026-10-05: FIDS may not confirm IN (FIDS is never OOOI-authoritative,
            # 2026-09-27); this inferred IN needs the aircraft's own ACARS.

            if acars_contradicts:
                log.info(
                    "%s: schedule-inferred IN suppressed -- ACARS shows phase=%s instead",
                    ident, acars_check[0],
                )
                return

            if not acars_confirms:
                log.debug(
                    "%s: schedule window for IN reached (%s) but neither ACARS nor "
                    "FIDS confirms yet -- holding, ADS-B-dark alone is not sufficient",
                    ident, reason,
                )
                return

            # (operator 2026-10-16): schedule inference never writes the OOOI
            # phase. It used to write oooi_phase=in through the lock-less
            # writer (the sweep then deleted the entry on that phase); now it
            # records the inferred phase in the watchlist_history row only
            # (event_detail.inferred_phase, asserted=False) -- a non-
            # authoritative record -- and the asserted phase is left to the
            # SWIM surface/airline parsers (SMES/TFMS) and the gated ACARS
            # path. One record per entry: the phase stays "off", so without
            # this guard the inference would re-fire every tick.
            if any(h.get("event_type") == "oooi_in_inferred"
                   for h in db.get_watchlist_history(entry_id=entry["id"], limit=200)):
                return
            acars_ctx = _acars_reason_context(ident, entry.get("registration"))
            summary = (f"{ident} IN (inferred, not asserted) — schedule window reached, "
                       f"ACARS agrees, ADS-B dark; awaiting SWIM/ACARS phase write\n{acars_ctx}")
            watchlist_event_hit(
                entry["id"], summary,
                {"watchlist_trigger": "oooi_in_inferred",
                 "identifier": ident, "inferred_phase": "in", "asserted": False,
                 "source": "schedule_inference",
                 "note": reason, "acars_context": acars_ctx},
                priority=4,
            )
            log.info("flight schedule infer: %s IN inferred, not asserted (%s)", ident, reason)

    # Departure check: past sched_dep+90min with no OFF seen yet.
    # 2026-10-06 (operator: "inference may never assert"): this used to push
    # "OFF -- departed (schedule inferred)" and write oooi_phase=off through the
    # unguarded writer -- asserting a takeoff nobody observed, and handing the
    # entry to the ADS-B-dark arrival inference above. Now it ASKS the real
    # sources and only an observation (or the airline's own reported time,
    # subject to SWIM-beats-airline) may move the phase, through the authority
    # gate; otherwise one honest "departure overdue, not observed" push and
    # the phase stays where it is.
    if last_phase == "pre_departure":
        sched_dep = entry.get("scheduled_departure")
        if sched_dep:
            try:
                dep_dt = datetime.fromisoformat(sched_dep.replace("Z", "+00:00"))
                if now > dep_dt + timedelta(minutes=90):
                    _departure_overdue_check(entry, ident, sched_dep, now)
            except Exception as e:
                log.debug("schedule infer dep %s: %s", ident, e)


def _departure_overdue_check(entry: dict, ident: str, sched_dep: str, now) -> str:
    """Returns the source that confirmed OFF ('acars'|'fdps'|'tfms_airline') or
    'overdue' when nothing did. Never asserts a phase from the schedule."""
    from shared.watchlist import watchlist_event_hit, tfms_departure_state, _et
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    acars_ctx = _acars_reason_context(ident, entry.get("registration"))
    src, label, when = None, "", stamp
    try:
        a = _acars_phase(ident, registration=entry.get("registration"))
        if a and a[0] in ("off", "on", "in"):
            src, label = "acars", f"confirmed via ACARS ({a[1].get('label') or a[1].get('_source') or 'ACARS'})"
    except Exception as e:
        log.debug("%s: overdue ACARS check failed: %s", ident, e)
    if not src:
        try:
            if _fdps_confirms_off(ident, entry.get("hex_id")):
                src, label = "fdps", "confirmed via FDPS (flight plan active)"
        except Exception as e:
            log.debug("%s: overdue FDPS check failed: %s", ident, e)
    if not src:
        try:
            st = tfms_departure_state(ident, entry.get("origin"), entry.get("destination"))
            if st and st[0] == "off":
                src, label, when = "tfms_airline", f"OFF {_et(st[1])} per airline (TFMS)", st[1]
        except Exception as e:
            log.debug("%s: overdue TFMS check failed: %s", ident, e)
    if src and db.update_watchlist_oooi_phase_authoritative(entry["id"], "off", source=src, updated_at=when):
        watchlist_event_hit(entry["id"], f"{ident} OFF -- {label}; not seen by ADS-B\n{acars_ctx}",
                            {"watchlist_trigger": "oooi_off", "identifier": ident, "phase": "off", "source": src,
                             "scheduled_departure": sched_dep, "acars_context": acars_ctx},
                            priority=4)
        log.info("flight departure check: %s OFF via %s (past dep+90m)", ident, src)
        return src
    watchlist_event_hit(entry["id"],
                        f"{ident} departure overdue -- not observed 90+ min after scheduled departure "
                        f"({_et(sched_dep)}); no OFF asserted\n{acars_ctx}",
                        {"watchlist_trigger": "departure_overdue", "identifier": ident,
                         "scheduled_departure": sched_dep, "acars_context": acars_ctx},
                        priority=4)
    log.info("flight departure check: %s overdue, nothing confirms OFF -- phase left at pre_departure", ident)
    return "overdue"


from common.push_dedup import PushDedup, content_hash  # noqa: E402

# One "tail change NOT accepted" note per (watch, candidate hex) -- the FDPS
# check runs every tick and the rejected plan keeps saying the same thing.
_TAIL_GUARD_DEDUP = PushDedup("fdps_tail_guard")


def _fdps_tail_change_decision(entry: dict, plan: dict, new_hex: str,
                               new_reg: str | None, prior_hex: str | None):
    """Build the inputs for flight_instance.decide_tail_change from this
    watch + its FDPS plan and return the decision. The watched leg is keyed
    on the plan's OPERATING callsign with the watch's route/schedule; the
    candidate tail's other filings come from flight_events (DC-area FDPS
    only, so a watch whose origin is outside that coverage usually has no
    visible previous leg). Since 2026-10-13 a SWIM source showing the tail on
    the watched flight is itself the proof, so rule (c) does not apply to
    SWIM sources and is unreachable while FIDS is not a tail source
    (flight_instance.SWIM_TAIL_SOURCES); rule (b) still applies. Any error ->
    rejection, never a silent repoint."""
    from common import flight_instance as fi
    try:
        watched_key = fi.FlightInstanceKey.make(
            f"{plan.get('airline') or ''}{plan.get('flight_num') or ''}",
            entry.get("origin") or plan.get("origin"),
            entry.get("destination") or plan.get("destination"),
            entry.get("scheduled_departure") or plan.get("departure_time"))
        if watched_key is None:
            return fi.TailChangeDecision(False, True, "watched leg has no usable instance key",
                                         new_hex, new_reg, prior_hex)
        watched = fi.FlightInstance(
            key=watched_key, gufi=plan.get("flight_id"), status=plan.get("status"),
            sched_dep=fi.to_epoch(entry.get("scheduled_departure")) or fi.to_epoch(plan.get("departure_time")),
            sched_arr=fi.to_epoch(entry.get("scheduled_arrival")),
            est_arr=fi.to_epoch(plan.get("arrival_time")))
        candidates = fi.instances_for_hex(new_hex)
        prev = fi.previous_leg(candidates, watched, exclude_gufi=plan.get("flight_id"))
        return fi.decide_tail_change(
            watched=watched, candidate_hex=new_hex, candidate_registration=new_reg,
            prior_hex=prior_hex, sources=("fdps",), candidate_instances=candidates,
            previous=prev, exclude_gufi=plan.get("flight_id"))
    except Exception as e:
        log.warning("fdps tail guard %s: %s -- change not accepted", entry.get("id"), e)
        return fi.TailChangeDecision(False, True, f"guard error: {e}", new_hex, new_reg, prior_hex)


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
            # 2026-08-27: ident's marketing carrier (e.g. "AAL") often never
            # appears in FDPS at all for codeshare/regional-operated flights
            # -- see get_flight_plan_by_flight_num's docstring (the
            # UAL4044/ASH4044 case). add_flight_watchlist's own add-time
            # flow already has a fallback for this; this PERIODIC recheck
            # never did, so any such entry's FDPS status silently never
            # updated after the initial add. Prefer a CONFIRMED
            # operating-carrier mapping (codeshare_map) over a blind
            # ignore-the-carrier flight_num+origin scan every tick --
            # flight numbers are reused across entirely unrelated
            # carriers/routes on the same day (confirmed live: bare
            # flight_num 5265 alone matched CAL/SKW/EDV/JIA -- four
            # different routes -- the same day), so guessing by number
            # alone on every tick risks locking onto the wrong flight.
            # Only fall back to the broad origin-scoped discovery scan when
            # no mapping exists yet; a successful fallback hit both answers
            # this tick and seeds codeshare_map (same shape
            # add_flight_watchlist already writes) so future ticks for this
            # marketing identifier go straight to the precise path.
            import re
            m = re.match(r"^([A-Za-z]{2,3})(\d+[A-Za-z]?)$", ident.strip())
            if m:
                marketing_carrier, marketing_num = m.group(1).upper(), m.group(2)
                # 2026-10-05: resolve_operating_callsign normalises IATA (AC) to
                # ICAO (ACA) -- codeshare_map stores ICAO, so AC8825 used to miss.
                # 2026-10-13: route-filtered -- codeshare_map can hold both
                # GJS4520 and RPA4520 for UAL4520 (different routes, same
                # night); resolve_operating_callsign took the top-confidence
                # row regardless of route. operating_identities() keeps only
                # rows whose stored route does not contradict this watch.
                from common.flight_instance import operating_identities
                for op_c, op_n in operating_identities(ident, entry.get("origin"),
                                                       entry.get("destination"))[1:]:
                    plan = db.get_flight_plan_by_callsign(f"{op_c}{op_n}")
                    if plan:
                        break
                if not plan:
                    fallback_plan = db.get_flight_plan_by_flight_num(
                        marketing_num, origin=entry.get("origin"))
                    if fallback_plan:
                        operating_carrier = (fallback_plan.get("airline") or "").upper() or None
                        if operating_carrier and operating_carrier != marketing_carrier:
                            try:
                                db.upsert_codeshare_mapping(
                                    marketing_carrier=marketing_carrier,
                                    marketing_flight_num=marketing_num,
                                    operating_carrier=operating_carrier,
                                    operating_flight_num=fallback_plan.get("flight_num"),
                                    origin=fallback_plan.get("origin"),
                                    destination=fallback_plan.get("destination"),
                                    source="fdps_periodic_recheck_fallback",
                                )
                            except Exception as e:
                                log.debug("codeshare_map upsert %s: %s", ident, e)
                        plan = fallback_plan
        if not plan:
            return
        # 2026-10-13 (flight-instance resolver): a plan whose ORIGIN
        # contradicts the watch is another leg of a reused number (the
        # shuttle's return leg, a regional flying the brand number on
        # another route) -- never this watch's tail, status or diversion.
        # Destination is deliberately not gated: an FDPS destination change
        # on the right leg IS the diversion signal below.
        from common.flight_instance import airports_agree
        if airports_agree(entry.get("origin"), plan.get("origin")) is False:
            log.debug("fdps cache %s: plan %s is %s-%s, watch is %s-%s -- other leg, ignored",
                      ident, plan.get("flight_id"), plan.get("origin"), plan.get("destination"),
                      entry.get("origin"), entry.get("destination"))
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
            # 2026-10-13: tail-change guard (common.flight_instance.
            # decide_tail_change). The FAA plan is an authoritative source,
            # but a repoint now also needs the new tail to be free (not filed
            # on another overlapping leg) and its previous leg to reach this
            # watch's origin with a full turn to spare. Rejected -> the watch
            # keeps its hex and gets ONE "tail change NOT accepted" note per
            # candidate hex; the rest of this check (diversion/status) runs on.
            decision = _fdps_tail_change_decision(entry, plan, new_hex, new_reg, prior_hex)
            if decision.accepted:
                if not new_reg:
                    # hex-only FDPS shape (ADSB_02M_52B): look the tail up
                    # rather than writing registration=None over the old one
                    from common.flight_instance import registration_for_hex
                    new_reg = registration_for_hex(new_hex)
                watchlist_event_hit(
                    entry["id"],
                    f"{ident} FDPS: TAIL CHANGE {prior_hex}/{entry.get('registration') or '?'} "
                    f"→ {new_hex}/{new_reg or '?'} ({decision.reason})",
                    {"watchlist_trigger": "fdps_tail_change", "identifier": ident,
                     "prior_hex": prior_hex, "new_hex": new_hex,
                     "prior_registration": entry.get("registration"),
                     "new_registration": new_reg, "reason": decision.reason},
                    priority=4,
                )
                db.update_watchlist_hex_registration(entry["id"], new_hex, new_reg)
                db.update_watchlist_fdps_status(entry["id"], status, now_iso)
                return
            if decision.changed:
                slot = f"{entry['id']}:tail"
                ck = content_hash(f"tailguard:{entry['id']}:{new_hex.lower()}")
                if _TAIL_GUARD_DEDUP.should_push(slot, ck):
                    watchlist_event_hit(
                        entry["id"],
                        f"{ident} FDPS: tail change NOT accepted "
                        f"({prior_hex} → {new_hex}/{new_reg or '?'}): {decision.reason}",
                        {"watchlist_trigger": "fdps_tail_change_rejected", "identifier": ident,
                         "prior_hex": prior_hex, "candidate_hex": new_hex,
                         "candidate_registration": new_reg, "reason": decision.reason},
                        priority=3,
                    )
                    _TAIL_GUARD_DEDUP.record(slot, ck)

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
# 2026-10-03: table moved to common.airline_codes (single source). The old
# private copy here mapped ASH -> YX (wrong: ASH is Mesa = YV).
from common.airline_codes import ICAO_TO_IATA as _ICAO_TO_IATA_CARRIER  # noqa: E402


_HEX_INSTANCES_CACHE: dict[str, tuple[float, list]] = {}
HEX_INSTANCES_TTL_S = 600


def _hex_instances(hex_id: str) -> list:
    """fi.instances_for_hex is a raw_json LIKE scan (~20 s live, swarm audit NB-05): one
    scan per aircraft per 10 min, shared by the inbound tracker and the tail guard."""
    from common import flight_instance as fi
    hx = (hex_id or "").lower()
    hit = _HEX_INSTANCES_CACHE.get(hx)
    now = time.time()
    if hit and now - hit[0] < HEX_INSTANCES_TTL_S:
        return hit[1]
    insts = fi.instances_for_hex(hx)
    _HEX_INSTANCES_CACHE[hx] = (now, insts)
    for k in [k for k, v in _HEX_INSTANCES_CACHE.items() if now - v[0] > HEX_INSTANCES_TTL_S]:
        _HEX_INSTANCES_CACHE.pop(k, None)
    return insts


def _watch_sched_dep(entry: dict, ident: str):
    """The watch's scheduled departure, or -- when it carries none (0 of 13 live watches
    did on 10-14, swarm audit NB-04) -- today's instance from TFMS/FDPS: exactly one
    instance of this flight from the watch's origin scheduled in [now - 6 h, now + 24 h].
    None when that is not unique (never a guess)."""
    from common import flight_instance as fi
    if entry.get("scheduled_departure"):
        return entry["scheduled_departure"]
    now = time.time()
    try:
        cands = [i for i in fi.resolve(ident, entry.get("origin"), entry.get("destination"))
                 if i.sched_dep is not None and now - 6 * 3600 <= i.sched_dep <= now + 24 * 3600
                 and fi.airports_agree(i.key.origin, entry.get("origin")) is not False]
    except Exception as e:
        log.debug("watch sched %s: %s", ident, e)
        return None
    deps = {round(i.sched_dep) for i in cands}
    return next(iter(deps)) if len(deps) == 1 else None


_INBOUND_DEDUP = PushDedup("inbound_tracker")
_INBOUND_LAST_RUN: dict[str, float] = {}
INBOUND_EVERY_S = 600          # instances_for_hex is a raw_json scan: not every tick
INBOUND_LOOKAHEAD_H = 6.0      # start watching the inbound this long before the scheduled departure
_INBOUND_PRIORITY = {"late_beyond_turn": 4, "diverted": 4, "cancelled": 4, "on_time": 2, "at_origin": 2}


def _check_flight_inbound(entry: dict, ident: str) -> None:
    """2026-10-14 (flight-instance resolver, section 4): "where's my inbound".
    For a watch with a hex that has not pushed back, from INBOUND_LOOKAHEAD_H
    before its scheduled departure: find the assigned tail's leg into the
    watched origin (instances_for_hex + previous_leg) and assess it with the
    turn for its airframe class. One push per status transition; the dedup
    content is the status and the inbound's instance key, never times or
    slack. 'unknown' (inbound outside DC FDPS coverage, the usual case for a
    non-DC origin) is logged, not pushed. Reads only; never an OOOI phase."""
    from common import flight_instance as fi
    hex_id = (entry.get("hex_id") or "").strip().lower()
    if not hex_id or (entry.get("oooi_phase") or "").lower() in ("out", "off", "on", "in"):
        return
    sched = fi.to_epoch(_watch_sched_dep(entry, ident))
    now = time.time()
    if sched is None or not (sched - INBOUND_LOOKAHEAD_H * 3600 <= now <= sched + 3600):
        return
    if now - _INBOUND_LAST_RUN.get(entry["id"], 0.0) < INBOUND_EVERY_S:
        return
    _INBOUND_LAST_RUN[entry["id"]] = now
    ops = fi.operating_identities(ident, entry.get("origin"), entry.get("destination"))
    op_ident = f"{ops[-1][0]}{ops[-1][1]}" if len(ops) == 2 else ident
    key = fi.FlightInstanceKey.make(op_ident, entry.get("origin"), entry.get("destination"), sched)
    if key is None:
        return
    watched = fi.FlightInstance(key=key, sched_dep=sched, sched_arr=fi.to_epoch(entry.get("scheduled_arrival")))
    candidates = _hex_instances(hex_id)
    inbound = fi.previous_leg(candidates, watched)
    a = fi.assess_inbound(watched, inbound,
                          aircraft_class=fi.aircraft_class_for_type(inbound.aircraft_type if inbound else None))
    if a.status == "unknown":
        log.debug("inbound %s: %s", ident, a.summary)
        return
    slot = f"{entry['id']}:inbound"
    ck = content_hash(f"inbound:{entry['id']}:{a.status}:{a.inbound}")
    if not _INBOUND_DEDUP.should_push(slot, ck):
        return
    from shared.watchlist import watchlist_event_hit
    watchlist_event_hit(
        entry["id"], a.summary,
        {"watchlist_trigger": "inbound_" + a.status, "identifier": ident, "inbound": a.inbound,
         "flags": list(a.flags), "min_turn_min": a.min_turn_min, "slack_min": a.slack_min},
        priority=_INBOUND_PRIORITY.get(a.status, 3),
    )
    _INBOUND_DEDUP.record(slot, ck)


def _guarded_tail_change(entry: dict, ident: str, *, source: str, label: str, new_hex: str | None,
                         tail: str | None, exclude_gufi: str | None = None) -> None:
    """Run a candidate tail through decide_tail_change with ONE source and act
    on the decision: accepted -> repoint + push with the reason; rejected ->
    one note per (watch, candidate hex). Keyed on the OPERATING flight (a
    marketing number's own filing would otherwise look like "the tail is
    filed on another leg"). Any error is a rejection, never a silent repoint.
    Identity only: never an OOOI phase."""
    from common import flight_instance as fi
    prior_hex = (entry.get("hex_id") or "").lower() or None
    if not new_hex or new_hex.lower() == prior_hex:
        return
    new_hex = new_hex.lower()
    tail = tail or fi.registration_for_hex(new_hex)
    from shared.watchlist import watchlist_event_hit
    try:
        ops = fi.operating_identities(ident, entry.get("origin"), entry.get("destination"))
        op_ident = f"{ops[-1][0]}{ops[-1][1]}" if len(ops) == 2 else ident
        sched = _watch_sched_dep(entry, ident)
        watched_key = fi.FlightInstanceKey.make(op_ident, entry.get("origin"), entry.get("destination"), sched)
        if watched_key is None:
            decision = fi.TailChangeDecision(False, True, "watched leg has no usable instance key",
                                             new_hex, tail, prior_hex)
        else:
            watched = fi.FlightInstance(
                key=watched_key, gufi=exclude_gufi, sched_dep=fi.to_epoch(sched),
                sched_arr=fi.to_epoch(entry.get("scheduled_arrival")))
            candidates = _hex_instances(new_hex)
            decision = fi.decide_tail_change(
                watched=watched, candidate_hex=new_hex, candidate_registration=tail, prior_hex=prior_hex,
                sources=(source,), candidate_instances=candidates,
                previous=fi.previous_leg(candidates, watched, exclude_gufi=exclude_gufi),
                exclude_gufi=exclude_gufi)
    except Exception as e:
        log.warning("%s tail guard %s: %s -- change not accepted", source, entry.get("id"), e)
        decision = fi.TailChangeDecision(False, True, f"guard error: {e}", new_hex, tail, prior_hex)
    if decision.accepted:
        watchlist_event_hit(
            entry["id"],
            f"{ident} {label}: TAIL CHANGE {prior_hex}/{entry.get('registration') or '?'} "
            f"→ {new_hex}/{tail or '?'} ({decision.reason})",
            {"watchlist_trigger": f"{source}_tail_change", "identifier": ident, "prior_hex": prior_hex,
             "new_hex": new_hex, "prior_registration": entry.get("registration"),
             "new_registration": tail, "reason": decision.reason},
            priority=4,
        )
        db.update_watchlist_hex_registration(entry["id"], new_hex, tail, source=source)
        return
    if decision.changed:
        slot = f"{entry['id']}:tail"
        ck = content_hash(f"tailguard:{entry['id']}:{new_hex}")
        if _TAIL_GUARD_DEDUP.should_push(slot, ck):
            watchlist_event_hit(
                entry["id"],
                f"{ident} {label}: tail change NOT accepted ({prior_hex} → {new_hex}/{tail or '?'}): "
                f"{decision.reason}",
                {"watchlist_trigger": f"{source}_tail_change_rejected", "identifier": ident,
                 "prior_hex": prior_hex, "candidate_hex": new_hex, "candidate_registration": tail,
                 "reason": decision.reason},
                priority=3,
            )
            _TAIL_GUARD_DEDUP.record(slot, ck)


_TDES_TAIL_LAST_RUN: dict[str, float] = {}
_TDES_LAST_CANDIDATE: dict[str, tuple] = {}
TDES_TAIL_EVERY_S = 600
TDES_ORIGINS = ("DCA", "IAD", "BWI")


def _check_flight_tdes_tail(entry: dict, ident: str) -> None:
    """2026-10-14 (operator): the departure clearance is evidence of the tail
    for every watched flight departing DCA/IAD/BWI. TDES's clearance links (by
    the FAA-internal GUFI) to the FDPS plan, whose aircraft address is the
    tail flying THIS instance (fi.tdes_clearance_plan, +-3 h of the scheduled
    departure). A SWIM source, so rule (c) is not required; rule (b) is.
    Before takeoff only; at most every 10 min per watch."""
    from common import flight_instance as fi
    if fi.norm_airport(entry.get("origin")) not in TDES_ORIGINS:
        return
    if (entry.get("oooi_phase") or "").lower() in ("off", "on", "in"):
        return
    now = time.time()
    if now - _TDES_TAIL_LAST_RUN.get(entry["id"], 0.0) < TDES_TAIL_EVERY_S:
        return
    _TDES_TAIL_LAST_RUN[entry["id"]] = now
    ops = fi.operating_identities(ident, entry.get("origin"), entry.get("destination"))
    plan = fi.tdes_clearance_plan([f"{c}{n}" for c, n in ops], entry.get("origin"),
                                  _watch_sched_dep(entry, ident))
    if plan is None or not plan.hex:
        return
    seen = (plan.hex.lower(), plan.gufi)
    if _TDES_LAST_CANDIDATE.get(entry["id"]) == seen:
        return              # same candidate and plan as last time: already decided (NB-05)
    _TDES_LAST_CANDIDATE[entry["id"]] = seen
    if fi.airports_agree(plan.key.destination, entry.get("destination")) is False:
        return
    _guarded_tail_change(entry, ident, source="tdes", label=f"TDES {fi.norm_airport(entry.get('origin'))}",
                         new_hex=plan.hex, tail=plan.registration, exclude_gufi=plan.gufi)


def _check_flight_gates(entry: dict, ident: str) -> None:
    """2026-10-18 (operator, gate batch): gate / terminal / baggage for a flight
    watch from every source, through common.gate_assignments. Runs the two
    sweep-side providers -- MWAA FIDS (DCA/IAD, lowest tier) and the budgeted
    AeroAPI fallback -- then pushes each changed, KNOWN resolved assignment
    (SWIM TDES and ACARS evidence arrive from ingest and the web route). No
    push for an unknown or conflicting value; dedup on leg + side + field +
    value + status only. Any failure here is logged and swallowed: OOOI and
    every other check in the sweep are unaffected by enrichment errors."""
    from common import gate_assignments as ga
    try:
        key = ga.watch_leg_key(entry, ident, _watch_sched_dep(entry, ident))
    except Exception as e:
        log.debug("gates %s: leg key: %s", ident, e)
        return
    if key is None:
        return
    for step in (_check_flight_fids, _check_flight_aeroapi_gate):
        try:
            step(entry, ident, key)
        except Exception as e:
            log.debug("gates %s: %s: %s", ident, step.__name__, e)
    try:
        ga.push_watch_changes(entry, ident, key)
    except Exception as e:
        log.debug("gates %s: push: %s", ident, e)


def _check_flight_aeroapi_gate(entry: dict, ident: str, key) -> None:
    """AeroAPI, fallback only (common.gate_aeroapi): no higher source has the
    arrival gate (or a non-DC baggage claim), at most 2 calls per leg (~45 min
    before the ETA, after ON), daily cap, every call ledgered."""
    from common import gate_aeroapi, gate_assignments as ga, watch_eta
    inst = ga.leg_instance(key)
    # 2026-10-20: the watch ETA (SWIM only, TBFM > TFMS > FDPS) times the pre-ETA
    # call; a meter-fix time counts only through its runway estimate.
    w = None
    try:
        w = watch_eta.resolve(ident, getattr(key, "destination", None) or entry.get("destination"))
    except Exception as e:
        log.debug("gates %s: watch eta: %s", ident, e)
    eta = ((w.runway_epoch if w else None) or (inst.best_arr if inst else None)
           or _ep_or_none(entry.get("scheduled_arrival")))
    gate_aeroapi.maybe_fetch(key, eta=eta, phase=entry.get("oooi_phase"),
                             phase_at=_ep_or_none(entry.get("oooi_phase_updated_at")))


def _ep_or_none(value):
    from common import flight_instance as fi
    return fi.to_epoch(value)


def _check_flight_fids(entry: dict, ident: str, key) -> None:
    """MWAA FIDS (poller.fetchers.dca_fids/iad_fids, common/airport_fids.py) as
    the LOWEST-tier gate/baggage provider for a watched leg touching DCA/IAD.
    Writes evidence only (common.gate_assignments); pushes come from the
    resolved assignment in _check_flight_gates.

    2026-10-18 (operator, gate batch):
      - FIDS supplies gate, terminal and baggage ONLY -- never a time, a status,
        a tail or an OOOI phase. The status word that 21 of 40 fids_update
        pushes carried in the last 30 days is gone with the push itself.
      - Origin side fixed: at the watch's ORIGIN this read that airport's
        ARRIVALS board for the flight number (another leg's gate on an
        out-and-back); it now reads the DEPARTURES board, departure gate only.
      - The row is matched to the SWIM/TFMS-verified leg by route and by its
        published time inside the leg's own window (the one permitted use of a
        FIDS time; never stored, logged or shown). No verified times: only a
        route that is unique on the board matches.
    History: net-new 2026-07-27 (FIDS had been an on-demand lookup only).
    """
    import re
    from common import airport_fids, gate_assignments as ga
    sides = []
    if key.destination in ("DCA", "IAD"):
        sides.append(("arrival", key.destination, key.origin))
    if key.origin in ("DCA", "IAD"):
        sides.append(("departure", key.origin, key.destination))
    if not sides:
        return
    m = re.match(r"^([A-Za-z]{2,3})(\d+[A-Za-z]?)$", ident.strip())
    if not m:
        return
    iata_carrier = _ICAO_TO_IATA_CARRIER.get(m.group(1).upper()) or \
        (m.group(1).upper() if len(m.group(1)) == 2 else None)
    if not iata_carrier:
        return
    flight_num = m.group(2)
    from common import callsign_lookaside as cl
    inst = ga.leg_instance(key)
    for side, airport, other in sides:
        # window bounds moved to callsign_lookaside.fids_window (same values) so
        # the lookaside learner below picks rows by the identical rule.
        window = cl.fids_window(inst, side)
        row = airport_fids.match_leg(airport, side, iata_carrier, flight_num, other, window)
        # 2026-10-20 (bellwether batch): learn the public number -> ATC callsign
        # (or codeshare) link from this match. Callsigns and the route only --
        # never a FIDS time, status or tail. Failure never affects the gate step.
        try:
            cl.learn_from_fids(entry, ident, airport=airport, side=side, iata=iata_carrier,
                               flight_num=flight_num, other=other, inst=inst, row=row,
                               sched_date=key.sched_date)
        except Exception as e:  # noqa: BLE001
            log.debug("gates %s: lookaside learn: %s", ident, e)
        if row is None:
            continue
        payload_ref = airport_fids.get_payload_hash(airport)
        now_iso = ga._iso()
        fields = (("gate", "terminal", "baggage") if side == "arrival" else ("gate",))
        obs = [ga.Observation(key=key, side=side, field=f, value_raw=row.get(f), source="mwaa_fids",
                              observed_at=now_iso, leg_match=row["leg_match"], evidence_type="display",
                              confidence=0.7, parser_version="mwaa-fids-2", airport=airport,
                              raw_table="mwaa_fids_payload", raw_ref=payload_ref)
               for f in fields]
        ga.record(obs, ladd_idents=[ident, entry.get("registration")], ladd_hexes=[entry.get("hex_id")])


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
    DEFAULT_LAT, DEFAULT_LON, DIST_NM = 39.0000, -77.0000, 120
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


# Which route names belong to the Amtrak provider. Config-driven so an
# outside operator can run this platform in a region with different services
# without editing code -- same idiom as AMTRAK_CORE_ROUTES /
# AMTRAK_REGIONAL_STATIONS already in dispatch.env. See
# docs/TRAIN_PARITY_DESIGN_2026-09-23.md sec5 for the full provider scheme.
#
# Default deliberately lists the Amtrak services actually on this platform's
# watchlist rather than "everything not MARC/VRE": an unmapped route must
# produce NO data, never another provider's data.
_AMTRAK_ROUTE_DEFAULT = (
    "Acela,Northeast Regional,Crescent,Silver Meteor,Silver Star,Palmetto,"
    "Vermonter,Floridian,Cardinal,Carolinian,Keystone,Empire Service,"
    "Maple Leaf,Lake Shore Limited,Capitol Limited,Pennsylvanian,Adirondack,"
    "Ethan Allen Express,Berkshire Flyer,Valley Flyer,Downeaster,Corridor"
)


def _amtrak_routes() -> set[str]:
    import os as _os
    raw = _os.environ.get("TRAIN_PROVIDER_ROUTES_AMTRAK") or _AMTRAK_ROUTE_DEFAULT
    return {r.strip().casefold() for r in raw.split(",") if r.strip()}


def _is_amtrak_route(route_name: str | None) -> bool:
    """True when this route is served by the amtraker feed.

    A missing/blank route_name returns True so a transient entry added
    without one (e.g. by flight-hifi-track's train sibling, or a hand POST)
    still gets swept -- the collision risk is specifically MARC/VRE entries
    that DO carry a route_name naming another operator."""
    if not route_name or not str(route_name).strip():
        return True
    return str(route_name).strip().casefold() in _amtrak_routes()


# Train phase vocabulary, mirroring OOOI's four states. See
# docs/TRAIN_PARITY_DESIGN_2026-09-23.md sec2 and pg_schema/0064.
TRAIN_PHASES = ("scheduled", "departed", "approaching", "arrived")


def _derive_train_phase(train: dict, stations: list, dest_stn: dict | None) -> str | None:
    """Map a provider payload onto the four-state train phase.

        scheduled   trainState == Predeparture              (pre-OUT)
        departed    origin station status == Departed        (OUT/OFF)
        approaching destination is first station not Departed (ON)
        arrived     destination status == Station,
                    or trainState == Completed               (IN)

    Enum values measured 2026-09-23 across 195 live trains / 47 routes:
    trainState is one of Active (173) / Predeparture (15) / Completed (7);
    station status is Departed (1627) / Enroute (1772) / Station (63).

    Returns None when the payload supports no confident call -- the caller
    must not write a phase it cannot justify, since a wrong phase is worse
    than an absent one for anything downstream that gates on it.

    NOTE: this reads amtraker's vocabulary directly. Once the provider
    abstraction lands (design sec5) this mapping moves INTO the provider
    adapter -- MARC and VRE will not emit Enroute/Station/Departed, and
    keeping the mapping here would mean every new provider edits the sweep."""
    state = (train.get("trainState") or "").strip().lower()
    if state == "predeparture":
        return "scheduled"
    if state == "completed":
        return "arrived"

    dest_status = ((dest_stn or {}).get("status") or "").strip().lower()
    if dest_status == "station":
        return "arrived"

    if not stations:
        return None

    # First station not yet departed is where the train is headed next. If
    # that IS the destination, it is on final approach to it.
    next_stn = next((s for s in stations
                     if (s.get("status") or "").strip().lower() != "departed"), None)
    if next_stn is not None and dest_stn is not None \
            and next_stn.get("code") == dest_stn.get("code"):
        return "approaching"

    origin_status = (stations[0].get("status") or "").strip().lower()
    if origin_status == "departed":
        return "departed"
    return None


def _train_last_event_for_run(entry: dict, train: dict, now: float | None = None) -> str:
    """The watch's last event, but only if it belongs to THIS run of the train.

    2026-10-13 (operator: no train pushes for hours; 2155 silent all evening):
    last_event_summary was compared across days. "#2155 LATE 31min" from 10-05
    made every later run count as already running, so its en-route push never
    fired again, and "#2154 arrived" matched the same text from 09-28 so arrivals
    were suppressed. An event recorded before this run's scheduled origin
    departure (amtraker v3 origSchDep) belongs to an earlier run. Without
    origSchDep, an event older than 16 h is treated as an earlier run's."""
    last = entry.get("last_event_summary") or ""
    at = _iso_epoch(entry.get("last_event_at"))
    if not last or at is None:
        return last
    run_start = _iso_epoch(train.get("origSchDep"))
    if run_start is not None:
        return last if at >= run_start - 3600 else ""
    now = time.time() if now is None else now
    return last if now - at < 16 * 3600 else ""


def _iso_epoch(v) -> float | None:
    from datetime import datetime
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


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

    # 2026-09-23 FIX. amtraker v3 returns a DICT KEYED BY TRAIN NUMBER whose
    # value is a list:  {"141": [ {...train...} ]}
    #
    # The previous line was:
    #     train = trains[0] if isinstance(trains, list) else trains
    # A dict is not a list, so `train` became the WRAPPER, not the train.
    # Every field read after it -- trainState, stations, everything -- was
    # therefore None, `state` was "", every classification below evaluated
    # False, and the function returned having fired nothing. Silently, for
    # every train, on every sweep, since this was written.
    #
    # Confirmed 2026-09-23: all 304 train watchlist entries had
    # last_event_summary NULL -- the train alert path had never once fired,
    # while the identical flight path worked. The sweep, the API and the
    # entries were all fine; only this unwrap was wrong.
    if isinstance(trains, dict):
        bucket = trains.get(str(ident))
        if bucket is None:                      # key mismatch (e.g. "141" vs 141)
            bucket = next(iter(trains.values()), None)
        train = bucket[0] if isinstance(bucket, list) and bucket else bucket
    elif isinstance(trains, list):
        train = trains[0]
    else:
        train = trains
    if not isinstance(train, dict):
        log.debug("amtraker %s: unexpected payload shape %s", ident, type(train).__name__)
        return

    state = (train.get("trainState") or train.get("status") or "").lower()
    last_event = _train_last_event_for_run(entry, train)

    # Derive current delay from amtraker velocity/status fields if available.
    # Amtraker v3 returns velocityMph, trainTimely, and per-station ETA objects.
    # 2026-09-23: amtraker v3 carries NO top-level estimatedArrival /
    # predicted_arrival / arrivalTime -- all three reads below returned None
    # on every call. The real times live per-station in `stations[]` as
    # schArr (scheduled) and arr (current estimate/actual), which is also
    # where the destination's own `status` ("Enroute"/"Station") lives.
    #
    # Reading them from the DESTINATION station means delay no longer depends
    # on the entry carrying scheduled_arrival -- which none of the 304
    # existing entries did, so delay_min was permanently None and the
    # LATE/late branches below were unreachable even once the unwrap above
    # was fixed. Entry scheduled_arrival is still preferred when present, so
    # an operator-supplied schedule still wins over the feed's.
    dest_code = (entry.get("destination") or "").strip().upper()
    stations = train.get("stations") or []
    dest_stn = None
    for stn in stations:
        if (stn.get("code") or "").strip().upper() == dest_code:
            dest_stn = stn
            break
    if dest_stn is None and stations:
        dest_stn = stations[-1]          # final stop is the destination

    sched_str = entry.get("scheduled_arrival") or (dest_stn or {}).get("schArr")
    pred_str = ((dest_stn or {}).get("arr")
                or train.get("estimatedArrival")
                or train.get("predicted_arrival")
                or train.get("arrivalTime"))
    # The destination station's own status is more specific than the
    # train-level trainState ("Active" right up until it berths).
    if dest_stn and (dest_stn.get("status") or "").strip():
        state = f"{state} {dest_stn['status']}".strip().lower()

    delay_min = None
    if sched_str and pred_str:
        try:
            sched = datetime.fromisoformat(sched_str.replace("Z", "+00:00"))
            pred = datetime.fromisoformat(str(pred_str).replace("Z", "+00:00"))
            delay_min = int((pred - sched).total_seconds() / 60)
        except ValueError:
            pass

    # 2026-09-23 -- persist phase + live ETA (pg_schema/0064). Closes parity
    # breaks 1 and 2 in docs/TRAIN_PARITY_DESIGN_2026-09-23.md: trains had no
    # phase concept at all while flights carry OOOI, and scheduled_arrival was
    # written once at add time and never refreshed while flights get continuous
    # FIDS/FDPS/TBFM updates.
    #
    # This runs BEFORE the alert classification below and independently of it:
    # a train that produces no alert this tick (unchanged summary, or a state
    # the classifier ignores) must still have its phase and ETA kept current.
    # Tying state persistence to alert emission is what left the flight OOOI
    # table at 4 rows for three weeks -- same mistake, different table.
    try:
        _phase = _derive_train_phase(train, stations, dest_stn)
        if _phase:
            db.update_train_phase(
                entry["id"], _phase,
                source=(train.get("provider") or "amtrak"),
                live_eta=pred_str,
                arrival_delay_min=delay_min,
            )
    except Exception as e:                       # never block alerting on this
        log.debug("train phase/eta persist %s: %s", ident, e)

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

    # 2026-09-18: skip the whole SQLite bootstrap chain on Postgres --
    # the schema there is already fully built by the 48 files in
    # common/pg_schema/, applied+tracked by scripts/pg_migrate.py. These
    # init_db_vN() calls are real SQLite DDL (some with SQLite-only
    # syntax translate_sql() doesn't cover), not a harmless idempotent
    # no-op on this backend -- confirmed live: one left a Postgres
    # transaction aborted and crash-looped every core container on
    # startup after the 2026-09-18 cutover.
    if db_backend.backend() != "postgres":
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
        db.init_db_v38()
        # 2026-08-30: v43 = uas_phase columns for entry_type="drone" (v39/v40
        # are ingest-owned, v41/v42 live in db_swim). The poller runs
        # WatchlistFileWatcher, which loads permanent_drones.json entries, so
        # it applies this one too (idempotent alongside web's own call).
        db.init_db_v43()
        # 2026-09-04: metar_history -- poller.fetchers.metar.run() (fired via
        # FETCH_SCHEDULE every 300s) now logs every observation here; must
        # exist before the first fetch on a fresh DB.
        db.init_db_v44()
        # 2026-09-05: cifp_fixes/cifp_procedure_legs/cifp_holds -- faa_cifp_parse.py
        # (fired via its own weekly timer, after faa_cifp_pull.py) writes here.
        db.init_db_v45()
        # 2026-10-18: nas_programs.source -- the REST NAS fetcher stamps it and
        # deactivates only its own rows (pg_schema/0076 on Postgres).
        db.init_db_v47()
        # 2026-10-18: tfrs.vip_push_reason -- the TFR fetcher stamps it
        # (pg_schema/0081 on Postgres).
        db.init_db_v50()
        # 2026-10-20: codeshare_map relation/season/retired_at/superseded_by +
        # the two seeded ATC-callsign links, watchlist_entries.alert_topic (the
        # file watcher copies it) -- pg_schema/0082 on Postgres.
        db.init_db_v51()

    src_dir = Path(__file__).parent.parent
    trigger_dir = Path(config.trigger_dir())
    trigger_dir.mkdir(parents=True, exist_ok=True)

    fetchers = [FetchLoop(**s) for s in FETCH_SCHEDULE]
    skills = [SkillLoop(**s) for s in SKILL_SCHEDULE]
    reactor = TriggerReactor(trigger_dir, src_dir)
    watchlist_sweep = WatchlistSweep()

    # 2026-09-06 restart hold-off (operator directive: nothing may fire
    # immediately on a restart). Every loop gets a slot: nothing before
    # boot+HOLDOFF, then fetchers one per STAGGER, skills continuing the
    # same sequence after the last fetcher. Triggers and watchlist sweeps
    # are held until the plain HOLDOFF. Set POLLER_STARTUP_HOLDOFF_S=0 to
    # get the old fire-everything-at-t0 behaviour back.
    global _boot_time
    boot = time.time()
    _boot_time = boot  # push_grace_active() compares heartbeat stamps to this
    slot = 0
    for f in fetchers:
        f.not_before = boot + POLLER_STARTUP_HOLDOFF_S + slot * POLLER_STARTUP_STAGGER_S
        slot += 1
    for s in skills:
        s.not_before = boot + POLLER_STARTUP_HOLDOFF_S + slot * POLLER_STARTUP_STAGGER_S
        slot += 1
    reactor.not_before = boot + POLLER_STARTUP_HOLDOFF_S
    watchlist_sweep.not_before = boot + POLLER_STARTUP_HOLDOFF_S
    last_slot_s = POLLER_STARTUP_HOLDOFF_S + max(slot - 1, 0) * POLLER_STARTUP_STAGGER_S
    log.info("Startup hold-off: no fetch/skill/trigger/sweep for %.0fs; %d loops "
             "staggered %.0fs apart, last slot at +%.0fs; push-gated fetchers "
             "ignore pre-boot heartbeats until +%.0fs",
             POLLER_STARTUP_HOLDOFF_S, slot, POLLER_STARTUP_STAGGER_S, last_slot_s,
             POLLER_STARTUP_HOLDOFF_S + POLLER_STARTUP_PUSH_GRACE_S)

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
