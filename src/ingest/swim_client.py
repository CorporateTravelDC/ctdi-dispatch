"""
ingest.swim_client — FAA SWIM NMS/SCDS subscriber via Solace PubSub+.

Replaces the legacy FNS/AMQP client (ingest/swim.py). Each NMS data feed
connects to its own Solace VPN with dedicated credentials and a
pre-provisioned durable exclusive queue.

Feeds and their REST fallback keys:
  fdps  → push:fdps  (flight events; no direct REST fallback)
  stdds → push:stdds (TFRs via tfr.py)
  tfms  → push:tfms  (NAS programs via nas.py)
  aim   → push:fns   (NOTAMs via notam.py — key kept as "fns" for compat)
  tbfm  → push:tbfm  (arrival sequencing; no REST fallback)
  itws  → push:itws  (terminal weather alerts; no REST fallback)

Missing credentials → feeds log "pending_credentials" and idle; container
never crashes. The poller's REST fallback remains active whenever ingest is
not stamping heartbeats.

Heartbeat contract: mark_push_healthy(feed_name) is called every 30s while
connected. Stopping heartbeats (on disconnect) causes the poller to resume
REST polling automatically — no explicit coordination needed.
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from ingest import failover
from ingest.config import NmsConfig, NmsFeedConfig
from common import db as _db

log = logging.getLogger("ingest.swim_client")

HEARTBEAT_INTERVAL = 30   # seconds between heartbeat stamps
_RECONNECT_BACKOFF = [15, 30, 60, 60, 60]  # successive retry delays, capped at 60s

# Backlog fast-forward triage (added 2026-07-26). After a genuinely long
# outage -- a soft bandwidth-priority pause or a hard container stop via
# scripts/ingest-feed-ctl.sh -- FAA's broker-side durable queue keeps
# queuing messages the whole time we're not draining it. That part is
# inherent to a durable queue and outside our control. What IS in our
# control is what we do with that backlog on reconnect: without this,
# every feed just calls receive_message() in a tight loop and
# processes/writes every backlogged message however old it is, as fast as
# possible -- fine for a short pause (a 10-15 minute Ollama-governor-scale
# gap never even reaches the threshold below, so this never engages and
# behavior is unchanged) but a real problem after a multi-hour+ outage
# (e.g. the 2-day case this was designed against, which would otherwise
# dump two days of stale flight events into the DB in one burst).
#
# Design: on the first message seen after a fresh connect, check its age.
# If it's already older than BACKLOG_STALE_SECONDS, we're behind -- a real
# backlog exists. From that point until we catch back up to "now", keep
# anything younger than BACKLOG_STALE_SECONDS OR within the most recent
# BACKLOG_RECENT_FRACTION of the backlog's total time span (so for a 2-day
# outage, the newest ~10% -- roughly 4.8h -- of the backlog still gets
# processed even though it's older than the flat 2h floor). Everything
# older than both of those is dropped without ever reaching the per-feed
# parser -- not written to the DB, not counted as filter-accepted, no push.
# Messages that DO reach the parser still go through that feed's own
# existing significance filter exactly as before (e.g. NOTAM's geo/
# significance filter) -- this only gates whether a message is even
# offered to the parser, it doesn't change what the parser does with it.
BACKLOG_STALE_SECONDS = int(os.getenv("SWIM_BACKLOG_STALE_SECONDS", "7200"))        # 2h
BACKLOG_RECENT_FRACTION = float(os.getenv("SWIM_BACKLOG_RECENT_FRACTION", "0.10"))  # last 10%

# _db_pool now only carries the low-frequency heartbeat/status calls
# (_stamp_healthy/_stamp_down -- every 30s per feed, plus connect/disconnect).
# It used to also carry record_feed_bytes on every single received message
# across all 6 feed threads (100+ calls/sec combined) -- each call opening a
# brand-new sqlite3.connect() (see common/db.py's conn()), with only 2
# worker threads to drain them. Fixed 2026-07-19: record_feed_bytes is now
# accumulated in-memory (see _accumulate_feed_bytes/_flush_feed_counters
# below) and flushed to the DB in one batched write every
# _COUNTER_FLUSH_INTERVAL seconds instead of once per message. This removes
# both the per-message connection churn AND a plausible unbounded-queue-growth
# risk if the 2 workers ever fell behind message arrival rate (submit() never
# blocks -- a lagging pool just piles up pending Future objects in memory).
_db_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="swim-db")

_COUNTER_FLUSH_INTERVAL = 5.0  # seconds between batched feed_data_usage writes
_counter_lock = threading.Lock()
_pending_counts: dict[str, list[int]] = {}  # feed_name -> [bytes_in, records_in, records_accepted]
_counter_flush_stop = threading.Event()
_counter_flush_thread: threading.Thread | None = None


def _accumulate_feed_bytes(feed_name: str, raw_bytes: int,
                            records_in: int, records_accepted: int) -> None:
    """In-memory counter bump -- no I/O, no new sqlite3 connection. Called
    on every received message; must stay cheap."""
    with _counter_lock:
        entry = _pending_counts.setdefault(feed_name, [0, 0, 0])
        entry[0] += raw_bytes
        entry[1] += records_in
        entry[2] += records_accepted


def _flush_feed_counters_once() -> None:
    with _counter_lock:
        if not _pending_counts:
            return
        snapshot = dict(_pending_counts)
        _pending_counts.clear()
    for feed_name, (b, r, a) in snapshot.items():
        try:
            _db.record_feed_bytes(feed_name, b, r, a)
        except Exception as e:
            log.warning("swim_client: counter flush failed for %s: %s", feed_name, e)


def _counter_flush_loop() -> None:
    # Waits _COUNTER_FLUSH_INTERVAL between flushes; wakes early and does one
    # final flush as soon as stop is signalled, so we don't lose the last
    # partial window on a clean shutdown/restart.
    while not _counter_flush_stop.wait(_COUNTER_FLUSH_INTERVAL):
        _flush_feed_counters_once()
    _flush_feed_counters_once()


def _start_counter_flush_thread() -> None:
    global _counter_flush_thread
    _counter_flush_stop.clear()
    _counter_flush_thread = threading.Thread(
        target=_counter_flush_loop, daemon=True, name="swim-counter-flush",
    )
    _counter_flush_thread.start()


def _stop_counter_flush_thread() -> None:
    _counter_flush_stop.set()
    if _counter_flush_thread is not None:
        _counter_flush_thread.join(timeout=_COUNTER_FLUSH_INTERVAL + 2)


def _stamp_healthy(feed_name: str) -> None:
    try:
        failover.mark_push_healthy(feed_name)
    except Exception:
        pass


def _stamp_down(feed_name: str, error: str) -> None:
    try:
        failover.mark_push_down(feed_name, error)
    except Exception:
        pass


# ── Per-feed Solace session ───────────────────────────────────────────────────

# Backpressure tiers (added 2026-07-26). Ingest runs six concurrent Solace
# sessions plus fdps's own parse/write load on a single Pi 5 that also runs
# the poller, pusher, web, Nextcloud, ACARS stack, and Ollama -- see the
# 2026-07-26 finding that ingest alone was sustaining ~69% CPU with the Pi's
# load average over its 4-core budget (5.10), which was also implicated in
# the SWIM keep-alive churn and poller fetch-cadence drift found the same
# night. This is the "don't suck the line dry" backpressure valve: when
# priority=weather is active, the feeds least likely to change what a
# chauffeur dispatcher needs to act on right now (raw surface/terminal
# tracks, arrival sequencing, raw terminal weather codes, routine NOTAMs)
# stop draining their queue, freeing CPU/bandwidth for the two feeds that
# matter most during a weather event: FDPS (is my client's flight delayed,
# diverted, or cancelled) and TFMS (is there a ground stop/GDP at the
# destination). FIDS (dca_fids/iad_fids) isn't part of this -- those are
# separate REST fetchers in the poller container, not ingest, and aren't
# part of ingest's contention footprint.
_HIGH_PRIORITY_FEEDS: frozenset[str] = frozenset({"fdps", "tfms"})
_LOW_PRIORITY_FEEDS: frozenset[str] = frozenset({"stdds", "tbfm", "itws", "fns"})


def _current_bandwidth_priority_label() -> str:
    """Best-effort current priority value for log messages only -- never
    raises, falls back to 'unknown' on a DB hiccup rather than blocking a
    log line on it."""
    try:
        return _db.get_bandwidth_priority().get("priority") or "unknown"
    except Exception:
        return "unknown"


def _bandwidth_priority_says_pause(feed_name: str) -> bool:
    """True if this feed should stay paused right now due to an operator-set
    or auto-computed bandwidth priority override (see /admin/bandwidth-priority,
    SCHEMA_V20 in common/db.py).

    Two independent modes, both read from the same singleton row:

      priority=nexrad  -- pauses fdps ONLY. Added 2026-07-21 for a future
          NEXRAD Level II puller that needs fdps to step back. Operator-set
          only (no auto-trigger exists for this side yet).

      priority=weather -- pauses the low-priority tier (_LOW_PRIORITY_FEEDS)
          so fdps/tfms (_HIGH_PRIORITY_FEEDS) keep full throughput. Set
          automatically by poller/fetchers/nws.py when a Severe/Extreme NWS
          alert is active for the DC region (see _maybe_set_weather_priority
          there), and clears itself back to auto when the alert lifts --
          but an operator can also set/clear it manually via the admin
          endpoint, and the auto-trigger is written to never stomp on a
          manually-set state (checks set_by before acting).

    fdps and tfms are never paused by priority=weather even though fdps is
    the single largest bandwidth consumer -- during a weather event they are
    exactly the two feeds this platform exists to surface, so backpressure
    has to come out of the other four, not out of the ones the user is most
    likely to be checking against.
    """
    try:
        state = _db.get_bandwidth_priority()
    except Exception:
        return False  # DB hiccup shouldn't pause anything -- fail open

    if not state.get("active", False):
        return False
    priority = state.get("priority")

    if priority == "nexrad":
        return feed_name == "fdps"
    if priority == "weather":
        return feed_name in _LOW_PRIORITY_FEEDS
    return False


class _NmsFeedSession:
    """
    Manages a single Solace PubSub+ session for one NMS feed/VPN.
    Runs its connect/receive/reconnect loop in a daemon thread.
    """

    def __init__(self, feed_name: str, cfg: NmsFeedConfig,
                 message_handler) -> None:
        self.feed_name = feed_name
        self.cfg = cfg
        self._handler = message_handler
        self._stop = threading.Event()

    def start(self, stop_event: threading.Event) -> threading.Thread:
        self._stop = stop_event
        t = threading.Thread(
            target=self._run_loop,
            daemon=True,
            name=f"swim-nms-{self.feed_name}",
        )
        t.start()
        return t

    def _run_loop(self) -> None:
        backoff_idx = 0
        was_paused = False
        while not self._stop.is_set():
            if _bandwidth_priority_says_pause(self.feed_name):
                if not was_paused:
                    active_priority = _current_bandwidth_priority_label()
                    log.warning(
                        "swim_client %s: pausing -- bandwidth priority override "
                        "set to '%s' (operator toggle or auto-trigger via "
                        "/admin/bandwidth-priority)",
                        self.feed_name, active_priority,
                    )
                    _db_pool.submit(_stamp_down, self.feed_name,
                                    f"paused: bandwidth_priority={active_priority}")
                    was_paused = True
                self._stop.wait(15)
                continue
            if was_paused:
                log.info("swim_client %s: resuming -- bandwidth priority override cleared", self.feed_name)
                was_paused = False
                backoff_idx = 0

            try:
                self._connect_and_receive()
                backoff_idx = 0
            except Exception as e:
                log.error("swim_client %s: session error: %s", self.feed_name, e)
                _db_pool.submit(_stamp_down, self.feed_name, f"swim_nms: {e}")

            if self._stop.is_set():
                break
            delay = _RECONNECT_BACKOFF[min(backoff_idx, len(_RECONNECT_BACKOFF) - 1)]
            backoff_idx += 1
            log.warning("swim_client %s: reconnecting in %ds", self.feed_name, delay)
            self._stop.wait(delay)

    def _connect_and_receive(self) -> None:
        # Import here so the module loads cleanly when solace-pubsubplus is absent
        # (container start with pending credentials skips this code path entirely).
        from solace.messaging.messaging_service import MessagingService
        from solace.messaging.resources.queue import Queue
        from solace.messaging.config.transport_security_strategy import TLS
        from solace.messaging.errors.pubsubplus_client_error import PubSubPlusClientError

        # Explicit timeouts prevent indefinite SDK hang on broker unreachable.
        # TLS without validation: FAA ems1/ems2 certs may not be in container
        # trust store; disable for now (same as swim_test.py approach).
        props = {
            "solace.messaging.transport.host": self.cfg.host,
            "solace.messaging.service.vpn-name": self.cfg.vpn,
            "solace.messaging.authentication.scheme.basic.username": self.cfg.username,
            "solace.messaging.authentication.scheme.basic.password": self.cfg.password,
            "SOLCLIENT_SESSION_PROP_CONNECT_TIMEOUT_MS": "15000",
            "SOLCLIENT_SESSION_PROP_CONNECT_RETRIES": "0",
            "SOLCLIENT_SESSION_PROP_RECONNECT_RETRIES": "0",
            # Keep-alive tuning (added 2026-07-26): SDK default is 3000ms
            # interval x 3 missed responses = ~9s tolerance before a session
            # is declared down. Found via journalctl that all 6 SWIM VPN
            # sessions (TFMS/TBFM/STDDS/ITWS/FDPS/AIM_FNS) were hitting
            # SOLCLIENT_SUBCODE_KEEP_ALIVE_FAILURE in tight simultaneous
            # bursts every 1-3 minutes -- consistent with this Pi's known
            # periodic WiFi/gateway congestion (same root cause diagnosed the
            # same night for Tailscale DERP latency and Nextcloud tunnel
            # timeouts: gateway RTT spiking to hundreds of ms - low seconds
            # under load). A ~9s tolerance is well inside that congestion
            # window, so a brief WiFi hiccup was tearing down and forcing a
            # full TLS+auth+queue-bind reconnect on all 6 sessions at once,
            # instead of just riding out a few seconds of no response.
            # Widened to 5000ms x 8 = 40s tolerance -- generous enough to
            # absorb the observed congestion bursts, still well short of the
            # app's own reconnect backoff ladder (15/30/60/60/60s in
            # _RECONNECT_BACKOFF above) if a session is genuinely dead.
            # Does NOT touch CONNECT_RETRIES/RECONNECT_RETRIES (left at 0
            # deliberately -- the app's own _run_loop backoff already owns
            # reconnection, these stay 0 to avoid the SDK racing its own
            # retry against that loop).
            "SOLCLIENT_SESSION_PROP_KEEP_ALIVE_INT_MS": "5000",
            "SOLCLIENT_SESSION_PROP_KEEP_ALIVE_LIMIT": "8",
        }
        tls = TLS.create().without_certificate_validation()
        service = (
            MessagingService.builder()
            .from_properties(props)
            .with_transport_security_strategy(tls)
            .build()
        )
        try:
            service.connect()
        except PubSubPlusClientError as e:
            raise RuntimeError(f"connect failed: {e}") from e

        queue = Queue.durable_non_exclusive_queue(self.cfg.queue_name)
        try:
            receiver = (
                service.create_persistent_message_receiver_builder()
                .with_message_auto_acknowledgement()
                .build(queue)
            )
            receiver.start()
        except Exception as e:
            service.disconnect()
            raise RuntimeError(
                f"queue bind failed for {self.cfg.queue_name!r}: {e}"
            ) from e

        log.info("swim_client %s: connected (VPN=%s queue=%s)",
                 self.feed_name, self.cfg.vpn, self.cfg.queue_name)
        _db_pool.submit(_stamp_healthy, self.feed_name)

        feed_name = self.feed_name
        handler_fn = self._handler
        stop_ev = self._stop

        # Polling receive loop — receive_message() is the correct API for this
        # SDK version; receive_callback() does not exist on _PersistentMessageReceiver.
        last_hb = time.monotonic()
        # Bandwidth-priority suspend state for this session. IMPORTANT
        # (2026-07-21, fixed after a live test hung the fdps thread): do NOT
        # call receiver.terminate()/service.disconnect() to react to a
        # priority flip while messages are actively flowing -- the Solace
        # SDK's clean-disconnect path hung indefinitely when triggered from
        # inside the polling loop rather than after a real network error,
        # and the thread never recovered (no reconnect, no further logs,
        # heartbeat went stale). Instead: while paused, simply stop calling
        # receive_message() and sleep. The connection/receiver stay fully
        # alive and untouched -- Solace's own flow control/prefetch window
        # stops pulling further data from the broker once we quit draining
        # it, which is what actually saves bandwidth, without touching the
        # session lifecycle at all.
        suspended = False

        # Backlog fast-forward triage state -- local to this connection, so
        # a fresh reconnect always starts clean rather than carrying state
        # from whatever outage preceded it. backlog_cutoff_ts_ms stays None
        # (gate disabled, process everything -- today's behavior) unless
        # the very first message we see is already stale, in which case it
        # gets set to a real cutoff and cleared again once we catch up.
        backlog_start_ts_ms: float | None = None
        backlog_cutoff_ts_ms: float | None = None
        backlog_dropped = 0
        backlog_kept = 0

        # Bandwidth-priority is checked on its own cheap ~5s cadence, NOT on
        # every loop iteration. fdps can receive many messages per second --
        # calling _bandwidth_priority_says_pause() (a fresh sqlite3.connect()
        # per call, see common/db.py's conn()) on every single message caused
        # per-message lock contention against the DB (1.2GB+ file, 5 other
        # feed threads writing concurrently) that looked exactly like a hang
        # in testing 2026-07-21 -- fdps went completely silent, no crash, no
        # log, heartbeat went stale, because every message was blocked queuing
        # for a SQLite connection/lock that a busy writer already held. Fixed
        # by gating the check behind its own timer, same pattern as the
        # existing heartbeat cadence below.
        last_priority_check = time.monotonic()
        PRIORITY_CHECK_INTERVAL = 5.0
        try:
            while not stop_ev.is_set():
                if not service.is_connected:
                    log.warning("swim_client %s: service disconnected", feed_name)
                    break

                now_mono = time.monotonic()
                if now_mono - last_priority_check >= PRIORITY_CHECK_INTERVAL:
                    last_priority_check = now_mono
                    if _bandwidth_priority_says_pause(feed_name):
                        if not suspended:
                            active_priority = _current_bandwidth_priority_label()
                            log.warning(
                                "swim_client %s: suspending message consumption -- "
                                "bandwidth priority = '%s' (operator toggle or "
                                "auto-trigger via /admin/bandwidth-priority). "
                                "Connection stays open; not draining the queue is "
                                "what saves bandwidth.",
                                feed_name, active_priority,
                            )
                            _db_pool.submit(_stamp_down, feed_name,
                                            f"suspended: bandwidth_priority={active_priority}")
                            suspended = True
                    elif suspended:
                        log.info("swim_client %s: resuming message consumption -- "
                                 "bandwidth priority override cleared", feed_name)
                        suspended = False
                        _db_pool.submit(_stamp_healthy, feed_name)
                        last_hb = time.monotonic()

                if suspended:
                    stop_ev.wait(5)
                    continue

                try:
                    msg = receiver.receive_message(timeout=5000)
                    if msg is not None:
                        try:
                            payload = msg.get_payload_as_bytes() or b""
                            raw_bytes = len(payload)

                            process_this = True
                            sender_ts_ms = None
                            try:
                                sender_ts_ms = msg.get_sender_timestamp()
                            except Exception:
                                sender_ts_ms = None

                            if sender_ts_ms is not None:
                                now_ms = time.time() * 1000.0
                                age_seconds = (now_ms - sender_ts_ms) / 1000.0

                                if backlog_start_ts_ms is None:
                                    # First message on this connection -- decide
                                    # whether we're actually behind at all.
                                    backlog_start_ts_ms = sender_ts_ms
                                    if age_seconds > BACKLOG_STALE_SECONDS:
                                        backlog_span_ms = now_ms - sender_ts_ms
                                        backlog_cutoff_ts_ms = (
                                            backlog_start_ts_ms
                                            + (1.0 - BACKLOG_RECENT_FRACTION) * backlog_span_ms
                                        )
                                        log.warning(
                                            "swim_client %s: backlog detected on reconnect -- "
                                            "oldest queued message is %.0fs (%.1fh) old. "
                                            "Fast-forward triage engaged: keeping anything "
                                            "under %ds old or in the most recent %.0f%% of "
                                            "the backlog window; everything else drops "
                                            "without being parsed or written.",
                                            feed_name, age_seconds, age_seconds / 3600.0,
                                            BACKLOG_STALE_SECONDS, BACKLOG_RECENT_FRACTION * 100,
                                        )

                                if backlog_cutoff_ts_ms is not None:
                                    if age_seconds <= BACKLOG_STALE_SECONDS:
                                        # Caught back up to "current" -- turn the
                                        # gate off for the rest of this connection.
                                        log.info(
                                            "swim_client %s: backlog triage caught up -- "
                                            "dropped=%d kept=%d, resuming normal processing",
                                            feed_name, backlog_dropped, backlog_kept,
                                        )
                                        backlog_cutoff_ts_ms = None
                                    elif sender_ts_ms < backlog_cutoff_ts_ms:
                                        process_this = False

                            if process_this:
                                accepted = handler_fn(payload)  # True/1 = passed filter
                                if backlog_cutoff_ts_ms is not None:
                                    backlog_kept += 1
                            else:
                                accepted = False
                                backlog_dropped += 1
                                if backlog_dropped % 500 == 1:
                                    log.info(
                                        "swim_client %s: backlog triage progress -- "
                                        "dropped=%d kept=%d so far",
                                        feed_name, backlog_dropped, backlog_kept,
                                    )

                            _accumulate_feed_bytes(
                                feed_name, raw_bytes, 1,
                                0 if accepted is False else 1,
                            )
                        except Exception as ex:
                            log.error("swim_client %s handler error: %s", feed_name, ex)
                except Exception as poll_err:
                    log.warning("swim_client %s: receive error: %s", feed_name, poll_err)
                    break

                if time.monotonic() - last_hb >= HEARTBEAT_INTERVAL:
                    _db_pool.submit(_stamp_healthy, feed_name)
                    last_hb = time.monotonic()
        finally:
            try:
                receiver.terminate()
            except Exception:
                pass
            try:
                service.disconnect()
            except Exception:
                pass
            _db_pool.submit(_stamp_down, feed_name, "swim_nms: disconnected")


# ── Message dispatch ──────────────────────────────────────────────────────────

def _handle_fdps_message(payload: bytes) -> bool:
    from ingest.parsers.fdps_parser import (
        parse_fdps_message, write_flight_event,
        check_marine_one, check_fdps_watchlist,
    )
    parsed = parse_fdps_message(payload)
    if parsed is None:
        return False
    source = parsed.get("source", "")
    accepted = False
    if source in ("FH", "TH", "CL", "HP", "OH"):
        accepted = write_flight_event(parsed)
    check_marine_one(parsed)
    check_fdps_watchlist(parsed)
    return accepted


def _handle_stdds_message(payload: bytes) -> bool:
    from ingest.parsers.smes_parser import (
        parse_smes_message, write_surface_tracks,
        parse_tais_message, write_terminal_tracks,
        check_stdds_alerts, check_surface_alerts,
        parse_safety_logic_message, write_safety_status, check_incursion_alert,
        parse_surface_movement_event_message, write_surface_movement_event,
        check_taxi_alerts,
    )
    smes_tracks = parse_smes_message(payload)
    if smes_tracks:
        n = write_surface_tracks(smes_tracks)
        log.info("stdds: wrote %d surface track(s)", n)
        check_surface_alerts(smes_tracks)
        return n > 0

    tais_tracks = parse_tais_message(payload)
    if tais_tracks:
        n = write_terminal_tracks(tais_tracks)
        log.info("stdds: wrote %d terminal track(s)", n)
        check_stdds_alerts(tais_tracks)
        return n > 0

    safety_record = parse_safety_logic_message(payload)
    if safety_record:
        previous_bitmask = write_safety_status(safety_record)
        log.info("stdds: wrote safety-logic status for %s", safety_record["airport"])
        check_incursion_alert(safety_record, previous_bitmask)
        return True

    taxi_record = parse_surface_movement_event_message(payload)
    if taxi_record:
        ok = write_surface_movement_event(taxi_record)
        if ok:
            log.info(
                "stdds: wrote surface movement event %s %s at %s (event=%s status=%s)",
                taxi_record.get("callsign"), taxi_record["track_id"],
                taxi_record["airport"], taxi_record.get("event"), taxi_record.get("status"),
            )
            check_taxi_alerts(taxi_record)
        return ok

    return False


def _handle_tfms_message(payload: bytes) -> bool:
    from ingest.parsers.tfms_parser import parse_tfms_message, write_tfms_programs
    programs = parse_tfms_message(payload)
    if programs:
        n = write_tfms_programs(programs)
        log.info("tfms: wrote %d NAS program(s)", n)
        return n > 0
    return False


def _handle_aim_message(payload: bytes) -> bool:
    from ingest.parsers.aim_parser import parse_aim_message, write_aim_notams
    notams = parse_aim_message(payload)
    if notams:
        n = write_aim_notams(notams)
        log.info("aim: wrote %d NOTAM(s)", n)
        return n > 0
    return False


def _handle_tbfm_message(payload: bytes) -> bool:
    from ingest.parsers.tbfm_parser import parse_tbfm_message, write_tbfm_sequences
    sequences = parse_tbfm_message(payload)
    if sequences:
        n = write_tbfm_sequences(sequences)
        log.info("tbfm: wrote %d sequence(s)", n)
        return n > 0
    return False


def _handle_itws_message(payload: bytes) -> bool:
    from ingest.parsers.itws_parser import (
        parse_itws_message, write_itws_alerts, check_itws_alerts,
    )
    alerts = parse_itws_message(payload)
    if alerts:
        n = write_itws_alerts(alerts)
        check_itws_alerts(alerts)
        log.info("itws: processed %d alert(s)", len(alerts))
        return n > 0
    return False


# ── Supervisor (async entry point) ────────────────────────────────────────────

async def run(cfg: NmsConfig, stop: asyncio.Event) -> None:
    """
    Launch Solace NMS sessions for all configured feeds.
    Called by ingest.main — runs until stop is set.
    """
    thread_stop = threading.Event()
    _start_counter_flush_thread()

    feed_sessions: list[tuple[str, _NmsFeedSession]] = []

    # feed_name key = heartbeat name stamped in feed_state (must match poller push_feed refs)
    # aim uses "fns" key to match the existing push:fns reference in notam REST fetcher
    _FEED_HANDLERS = {
        "fdps":  (cfg.fdps,  _handle_fdps_message),
        "stdds": (cfg.stdds, _handle_stdds_message),
        "tfms":  (cfg.tfms,  _handle_tfms_message),
        "fns":   (cfg.aim,   _handle_aim_message),   # AIM creds, fns heartbeat key
        "tbfm":  (cfg.tbfm,  _handle_tbfm_message),
        "itws":  (cfg.itws,  _handle_itws_message),
    }

    # Map heartbeat key → env var name for credential warnings
    _ENV_KEY = {
        "fdps": "FDPS", "stdds": "STDDS", "tfms": "TFMS",
        "fns": "AIM", "tbfm": "TBFM", "itws": "ITWS",
    }

    # SWIM_NMS_SKIP_FEEDS: comma-sep feed names to skip even if credentialed.
    # Disables high-bandwidth feeds (FDPS, STDDS) without removing credentials.
    _skip_raw = os.getenv("SWIM_NMS_SKIP_FEEDS", "")
    _skip_feeds = {f.strip().lower() for f in _skip_raw.split(",") if f.strip()}

    for feed_name, (feed_cfg, handler) in _FEED_HANDLERS.items():
        if feed_name.lower() in _skip_feeds:
            log.info("swim_client %s: skipped via SWIM_NMS_SKIP_FEEDS", feed_name)
            try:
                        _db.upsert_feed_skip(f"push:{feed_name}", time.time(),
                                     "disabled: SWIM_NMS_SKIP_FEEDS")
            except Exception:
                pass
            continue

        if not feed_cfg.username:
            env_key = _ENV_KEY.get(feed_name, feed_name.upper())
            log.warning(
                "swim_client %s: credentials not configured — "
                "set SWIM_NMS_USER_%s in dispatch-secrets.env to enable",
                feed_name, env_key,
            )
            _db.upsert_feed_skip(
                f"push:{feed_name}",
                time.time(),
                "pending_credentials: NMS credentials not yet provisioned",
            )
            continue

        _db.init_feed_usage(feed_name)
        session = _NmsFeedSession(feed_name, feed_cfg, handler)
        session.start(thread_stop)
        feed_sessions.append((feed_name, session))
        log.info("swim_client: started feed session for %s", feed_name)

    if not feed_sessions:
        log.warning("swim_client: no NMS feeds active (all pending credentials); "
                    "idling until credentials are provided")

    # Wait for the asyncio stop event, then signal threads.
    await stop.wait()
    thread_stop.set()
    _stop_counter_flush_thread()  # final flush so the last partial window isn't lost
    log.info("swim_client: stop signalled to all feed threads")
