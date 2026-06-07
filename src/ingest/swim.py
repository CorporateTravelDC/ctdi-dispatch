"""
ingest.swim — FAA SWIM / SCDS subscriber over AMQP 1.0.

Six receivers on a single AMQP connection:
  STDDS (TFRs), SFDPS (flight data), AIM_FNS (NOTAMs),
  TBFM (flow mgmt), TFMS (NAS/GDPs), ITWS (terminal wx)

Protocol: AMQP 1.0 via Apache Qpid Proton (python3-qpid-proton from Debian apt).
Broker:   ems1/ems2.swim.faa.gov:55443 (amqps://, TLS via Proton defaults).
Design:   One Proton Container, one connection, one receiver per topic.
          The Container runs in a thread executor; asyncio stop bridges via
          threading.Event → container.stop().

PARSING SEAMS — implement the parse_* function for your product:
  stdds / fns / tfms have DB writers wired; fill in the parser.
  sfdps / tbfm / itws are stubs until DB tables or parsers are finalized.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote as urlquote

# Thread pool for DB heartbeat writes so we never block the Proton reactor.
_db_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="swim-db")

try:
    from proton.handlers import MessagingHandler
    from proton.reactor import Container
    _PROTON_AVAILABLE = True
except ModuleNotFoundError:
    _PROTON_AVAILABLE = False
    MessagingHandler = object  # type: ignore[assignment,misc]

from common import db
from ingest import failover
from ingest.config import SwimConfig, SwimFeedConfig

log = logging.getLogger("ingest.swim")


def _stamp_healthy(feed_names: list[str]) -> None:
    """Write push heartbeats to DB. Runs in _db_pool, never in Proton reactor."""
    for name in feed_names:
        try:
            failover.mark_push_healthy(name)
        except Exception:
            pass


def _stamp_down(feed_names: list[str], error: str) -> None:
    """Write push-down error to DB. Runs in _db_pool, never in Proton reactor."""
    for name in feed_names:
        try:
            failover.mark_push_down(name, error)
        except Exception:
            pass


# ── PARSING SEAMS ──────────────────────────────────────────────────────────────
# Return a list of dicts whose keys match the db.upsert_* call below.
# Raise NotImplementedError (or return []) to log-and-skip without crashing.

def parse_stdds(topic: str, payload: bytes) -> list[dict]:
    # Expected: {tfr_id, raw_json, is_vip}
    raise NotImplementedError("parse_stdds: map STDDS TFR product to {tfr_id, raw_json, is_vip}")


def parse_sfdps(topic: str, payload: bytes) -> list[dict]:
    # DB table exists (flight_events, schema v4). Parser is a stub until a real
    # SFDPS/SCDS sample is captured against live credentials.
    # Expected dict keys: flight_id, airline, flight_num, origin, destination,
    #   aircraft_type, departure_time (epoch), arrival_time (epoch), status,
    #   position_lat, position_lon, altitude_ft, ground_speed_kt, raw_json.
    raise NotImplementedError("parse_sfdps: SFDPS sample not yet captured — wire parser after first live message")


def parse_fns(topic: str, payload: bytes) -> list[dict]:
    # Expected: {notam_id, raw_json, facility}
    raise NotImplementedError("parse_fns: map AIM_FNS NOTAM product to {notam_id, raw_json, facility}")


def parse_tbfm(topic: str, payload: bytes) -> list[dict]:
    raise NotImplementedError("parse_tbfm: TBFM flow data — no DB table yet")


def parse_tfms(topic: str, payload: bytes) -> list[dict]:
    # Expected: {program_id, prog_type, facility}
    raise NotImplementedError("parse_tfms: map TFMS NAS product to {program_id, prog_type, facility}")


def parse_itws(topic: str, payload: bytes) -> list[dict]:
    raise NotImplementedError("parse_itws: ITWS terminal wx — no DB table yet")


# parser → DB writer pairs; writer is None for feeds without a table yet.
_WRITERS: dict[str, tuple] = {
    "stdds": (parse_stdds, lambda r: db.upsert_tfr(r["tfr_id"], r["raw_json"], r.get("is_vip", False))),
    "sfdps": (parse_sfdps, lambda r: db.upsert_flight_event(
        r["flight_id"], r.get("airline"), r.get("flight_num"),
        r.get("origin"), r.get("destination"), r.get("aircraft_type"),
        r.get("departure_time"), r.get("arrival_time"), r.get("status"),
        r.get("position_lat"), r.get("position_lon"), r.get("altitude_ft"),
        r.get("ground_speed_kt"), r.get("raw_json", "")
    )),
    "fns":   (parse_fns,   lambda r: db.upsert_notam(r["notam_id"], r["raw_json"], r.get("facility", ""),
                                                       r.get("classification", ""), None, None, r.get("text_body", ""))),
    "tbfm":  (parse_tbfm,  None),
    "tfms":  (parse_tfms,  lambda r: db.upsert_nas_program(r["program_id"], r["prog_type"], r["facility"])),
    "itws":  (parse_itws,  None),
}


class _ScdsMultiHandler(MessagingHandler):
    """
    Single AMQP 1.0 connection with one receiver per SCDS topic.
    Routes incoming messages by topic address to the appropriate parser/writer.
    """

    def __init__(self, feeds: dict[str, SwimFeedConfig],
                 stop_flag: threading.Event, heartbeat: int):
        super().__init__()
        self.feeds = feeds           # feed_name → SwimFeedConfig
        self.stop_flag = stop_flag
        self.heartbeat = heartbeat
        self._container = None
        self._topic_map: dict[str, str] = {}  # topic_address → feed_name
        self._receivers: list = []            # for manual keepalive flow frames
        self._host_idx = 0

    def _primary_host(self) -> tuple[str, str, str, int, bool]:
        """Return (url, username, password, port, tls) for the first usable feed."""
        # All feeds share the same credentials; use the first one's host list.
        for cfg in self.feeds.values():
            if cfg.hosts:
                host = cfg.hosts[self._host_idx % len(cfg.hosts)]
                self._host_idx += 1
                user = urlquote(cfg.username, safe="")
                pw = urlquote(cfg.password, safe="")
                scheme = "amqps" if cfg.tls else "amqp"
                return f"{scheme}://{user}:{pw}@{host}:{cfg.port}"
        raise RuntimeError("No SWIM hosts configured")

    # Maximum link credit per receiver. FAA broker drops connections when credit
    # accumulates past ~25; keep well below that. We top up to this value each
    # timer tick (sending a FLOW frame), never above.
    _TARGET_CREDIT = 10

    def on_start(self, event):
        self._container = event.container
        url = self._primary_host()
        # heartbeat=50: AMQP OPEN advertises our local idle-timeout (50s) so Proton
        # sends empty transport frames every 25s. However the FAA SCDS broker does
        # NOT count empty frames when enforcing its own 30s idle-timeout — only
        # link-level FLOW frames reset that timer. We therefore send a capped FLOW
        # each tick in on_timer_task. heartbeat=50 is kept for the (rare) case where
        # the broker disconnects us for our own idle-timeout violation.
        conn = event.container.connect(url, heartbeat=50)

        for feed_name, cfg in self.feeds.items():
            for topic in cfg.topics:
                recv = event.container.create_receiver(conn, topic)
                self._receivers.append(recv)
                self._topic_map[topic] = feed_name

        # Fire at heartbeat/2 to stay comfortably inside the broker's 30s idle window.
        event.container.schedule(max(1, self.heartbeat // 2), self)
        log.info("SWIM connected (AMQP 1.0) — %d receiver(s)", len(self._topic_map))

    def on_timer_task(self, event):
        # Offload DB write to the thread pool — never block the Proton reactor
        # with a synchronous SQLite call or it will miss keepalives and drop.
        feed_names = list(self.feeds.keys())
        _db_pool.submit(_stamp_healthy, feed_names)
        if self.stop_flag.is_set():
            event.container.stop()
            return

        # Send a link-level FLOW frame on each receiver to reset the FAA broker's
        # 30-second idle-timeout. Empty AMQP transport frames don't count.
        # flow(0) when credit is already at _TARGET_CREDIT sends a FLOW with no
        # delta, which is valid and resets the broker's timer without accumulating
        # credit. Only top up to _TARGET_CREDIT — never exceed it.
        for recv in self._receivers:
            delta = max(0, self._TARGET_CREDIT - recv.credit)
            recv.flow(delta)  # flow(0) when already at cap: keepalive FLOW, no credit change

        event.container.schedule(max(1, self.heartbeat // 2), self)

    def on_message(self, event):
        topic = str(event.receiver.source.address)
        feed_name = self._topic_map.get(topic, "unknown")
        parser, writer = _WRITERS.get(feed_name, (None, None))
        if parser is None:
            return

        body = event.message.body
        payload = body.encode() if isinstance(body, str) else (body or b"")
        try:
            rows = parser(topic, payload)
            if writer:
                for r in rows:
                    writer(r)
            if rows:
                log.info("SWIM %s: %d record(s)", feed_name, len(rows))
        except NotImplementedError as e:
            log.warning("SWIM %s parser seam: %s", feed_name, e)
        except Exception as e:
            log.error("SWIM %s handler error: %s", feed_name, e)

    def on_connection_error(self, event):
        cond = getattr(event.connection, "remote_condition", None)
        log.error("SWIM connection error: %s", cond)
        msg = f"swim: connection error: {cond}"
        _db_pool.submit(_stamp_down, list(self.feeds.keys()), msg)

    def on_transport_error(self, event):
        cond = getattr(event.transport, "condition", None)
        log.error("SWIM transport error: %s", cond)
        msg = f"swim: transport: {cond}"
        _db_pool.submit(_stamp_down, list(self.feeds.keys()), msg)

    def on_disconnected(self, event):
        if not self.stop_flag.is_set():
            log.warning("SWIM disconnected")
            _db_pool.submit(_stamp_down, list(self.feeds.keys()), "swim: disconnected")
            if self._container:
                self._container.stop()


async def run(cfg: SwimConfig, stop: asyncio.Event, heartbeat: int) -> None:
    """
    Run AMQP subscriptions for all configured SCDS products.

    The FAA SCDS broker enforces a 5-receiver-per-connection limit. We split
    the 6 feeds into two groups (≤3 each) so each connection stays within the
    limit. Both groups run independently with their own reconnect loops.
    """
    if not _PROTON_AVAILABLE:
        log.info("swim: python-qpid-proton not installed — AMQP path disabled")
        await stop.wait()
        return
    all_feeds: dict[str, SwimFeedConfig] = {
        name: feed_cfg
        for name, feed_cfg in {
            "stdds": cfg.stdds,
            "sfdps": cfg.sfdps,
            "fns":   cfg.fns,
            "tbfm":  cfg.tbfm,
            "tfms":  cfg.tfms,
            "itws":  cfg.itws,
        }.items()
        if feed_cfg.hosts and feed_cfg.topics
    }

    if not all_feeds:
        log.warning("SWIM enabled but no feeds configured — nothing to do")
        return

    # Split into two groups of ≤3 to respect broker's 5-receiver limit.
    names = list(all_feeds.keys())
    mid = len(names) // 2
    group_a = {n: all_feeds[n] for n in names[:mid + len(names) % 2]}
    group_b = {n: all_feeds[n] for n in names[mid + len(names) % 2:]}

    log.info("SWIM: %d feed(s) in 2 connections (group A=%d, B=%d)",
             len(all_feeds), len(group_a), len(group_b))

    tasks = []
    if group_a:
        tasks.append(asyncio.create_task(
            _run_connection(group_a, stop, heartbeat), name="swim-conn-a"
        ))
    if group_b:
        tasks.append(asyncio.create_task(
            _run_connection(group_b, stop, heartbeat), name="swim-conn-b"
        ))

    await stop.wait()
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


async def _run_connection(feeds: dict[str, SwimFeedConfig],
                          stop: asyncio.Event, heartbeat: int) -> None:
    """Reconnect loop: single AMQP connection, all topics as receivers."""
    loop = asyncio.get_event_loop()
    backoff = 5

    while not stop.is_set():
        stop_flag = threading.Event()
        handler = _ScdsMultiHandler(feeds, stop_flag, heartbeat)

        async def _watch_stop(sf=stop_flag):
            await stop.wait()
            sf.set()

        watcher = asyncio.create_task(_watch_stop())
        try:
            await loop.run_in_executor(None, lambda h=handler: Container(h).run())
        except asyncio.CancelledError:
            stop_flag.set()
            raise
        except Exception as e:
            for feed_name in feeds:
                failover.mark_push_down(feed_name, f"swim: {e}")
            log.error("SWIM container error: %s", e)
        finally:
            watcher.cancel()

        if not stop.is_set():
            log.error("SWIM connection ended; reconnecting in %ds", backoff)
            try:
                await asyncio.wait_for(stop.wait(), timeout=backoff)
            except asyncio.TimeoutError:
                pass
            backoff = min(backoff * 2, 120)
