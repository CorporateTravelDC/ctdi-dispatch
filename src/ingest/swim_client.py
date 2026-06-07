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

log = logging.getLogger("ingest.swim_client")

HEARTBEAT_INTERVAL = 30   # seconds between heartbeat stamps
_RECONNECT_BACKOFF = [15, 30, 60, 60, 60]  # successive retry delays, capped at 60s

_db_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="swim-db")


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
        while not self._stop.is_set():
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
        from solace.messaging.config.retry_strategy import RetryStrategy
        from solace.messaging.resources.queue import Queue
        from solace.messaging.receiver.message_receiver import MessageHandler

        props = {
            "solace.messaging.transport.host": self.cfg.host,
            "solace.messaging.service.vpn-name": self.cfg.vpn,
            "solace.messaging.authentication.scheme.basic.username": self.cfg.username,
            "solace.messaging.authentication.scheme.basic.password": self.cfg.password,
        }
        service = (
            MessagingService.builder()
            .from_properties(props)
            .with_reconnection_retry_strategy(
                RetryStrategy.parametrized_retry(20, 3)
            )
            .build()
        )
        service.connect()

        queue = Queue.durable_exclusive_queue(self.cfg.queue_name)
        try:
            receiver = (
                service.create_persistent_message_receiver_builder()
                .build(queue)
            )
            receiver.start()
        except Exception as e:
            service.disconnect()
            raise RuntimeError(
                f"queue bind failed for {self.cfg.queue_name!r} "
                f"(queue may not be provisioned yet): {e}"
            ) from e

        feed_name = self.feed_name
        handler_fn = self._handler
        stop_ev = self._stop

        class _MsgHandler(MessageHandler):
            def on_message(self, message) -> None:  # type: ignore[override]
                try:
                    payload = message.get_payload_as_bytes() or b""
                    handler_fn(payload)
                    message.ack()
                except Exception as ex:
                    log.error("swim_client %s handler error: %s", feed_name, ex)

        receiver.receive_callback(_MsgHandler())
        log.info("swim_client %s: connected (VPN=%s queue=%s)",
                 feed_name, self.cfg.vpn, self.cfg.queue_name)

        _db_pool.submit(_stamp_healthy, feed_name)

        # Heartbeat loop while the service reports connected.
        last_hb = time.monotonic()
        while not stop_ev.is_set():
            if not service.is_connected:
                log.warning("swim_client %s: service disconnected", feed_name)
                break
            if time.monotonic() - last_hb >= HEARTBEAT_INTERVAL:
                _db_pool.submit(_stamp_healthy, feed_name)
                last_hb = time.monotonic()
            stop_ev.wait(5)

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

def _handle_fdps_message(payload: bytes) -> None:
    from ingest.parsers.fdps_parser import (
        parse_fdps_message, write_flight_event,
        check_marine_one, check_fdps_watchlist,
    )
    parsed = parse_fdps_message(payload)
    if parsed is None:
        return
    source = parsed.get("source", "")
    if source in ("FH", "TH", "CL", "HP", "OH"):
        write_flight_event(parsed)
    check_marine_one(parsed)
    check_fdps_watchlist(parsed)


def _handle_stdds_message(payload: bytes) -> None:
    from ingest.parsers.smes_parser import (
        parse_smes_message, write_surface_tracks,
        parse_tais_message, write_terminal_tracks,
    )
    smes_tracks = parse_smes_message(payload)
    if smes_tracks:
        n = write_surface_tracks(smes_tracks)
        log.debug("stdds: wrote %d surface track(s)", n)
        return

    tais_tracks = parse_tais_message(payload)
    if tais_tracks:
        n = write_terminal_tracks(tais_tracks)
        log.debug("stdds: wrote %d terminal track(s)", n)


def _handle_tfms_message(payload: bytes) -> None:
    from ingest.parsers.tfms_parser import parse_tfms_message, write_tfms_programs
    programs = parse_tfms_message(payload)
    if programs:
        n = write_tfms_programs(programs)
        log.debug("tfms: wrote %d NAS program(s)", n)


def _handle_aim_message(payload: bytes) -> None:
    from ingest.parsers.aim_parser import parse_aim_message, write_aim_notams
    notams = parse_aim_message(payload)
    if notams:
        n = write_aim_notams(notams)
        log.debug("aim: wrote %d NOTAM(s)", n)


def _handle_tbfm_message(payload: bytes) -> None:
    from ingest.parsers.tbfm_parser import parse_tbfm_message, write_tbfm_sequences
    sequences = parse_tbfm_message(payload)
    if sequences:
        n = write_tbfm_sequences(sequences)
        log.debug("tbfm: wrote %d sequence(s)", n)


def _handle_itws_message(payload: bytes) -> None:
    from ingest.parsers.itws_parser import (
        parse_itws_message, write_itws_alerts, check_itws_alerts,
    )
    alerts = parse_itws_message(payload)
    if alerts:
        write_itws_alerts(alerts)
        check_itws_alerts(alerts)
        log.debug("itws: processed %d alert(s)", len(alerts))


# ── Supervisor (async entry point) ────────────────────────────────────────────

async def run(cfg: NmsConfig, stop: asyncio.Event) -> None:
    """
    Launch Solace NMS sessions for all configured feeds.
    Called by ingest.main — runs until stop is set.
    """
    thread_stop = threading.Event()

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

    for feed_name, (feed_cfg, handler) in _FEED_HANDLERS.items():
        if not feed_cfg.username:
            env_key = _ENV_KEY.get(feed_name, feed_name.upper())
            log.warning(
                "swim_client %s: credentials not configured — "
                "set SWIM_NMS_USER_%s in dispatch-secrets.env to enable",
                feed_name, env_key,
            )
            from common import db as _db
            import time as _time
            _db.upsert_feed_skip(
                f"push:{feed_name}",
                _time.time(),
                "pending_credentials: NMS credentials not yet provisioned",
            )
            continue

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
    log.info("swim_client: stop signalled to all feed threads")
