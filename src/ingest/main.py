"""
ingest.main — async supervisor for the push-ingest service.

Launches the enabled sources (SWIM, NWWS-OI, Amtrak), each in its own supervised
task that reconnects on failure, and shuts them down cleanly on SIGTERM/SIGINT
(so `systemctl --user stop corporatetraveldc-ingest` is graceful).

Entry point matches the rest of your tree:  python3 -m ingest.main
"""
from __future__ import annotations

import asyncio
import logging
import signal
import threading

from common import db
from ingest import amtrak, config, nwws, swim, swim_client
from ingest.local_airspace import LocalAirspaceMonitor

log = logging.getLogger("ingest")


async def _supervise(name: str, coro_factory, stop: asyncio.Event) -> None:
    """Run a source coroutine; if it returns or raises while we're still up,
    log and restart it after a short delay. The source's own reconnect logic
    handles transient drops; this is the backstop."""
    while not stop.is_set():
        try:
            await coro_factory()
            if stop.is_set():
                return
            log.warning("Source %s exited unexpectedly; restarting in 10s", name)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.error("Source %s crashed: %s; restarting in 10s", name, e)
        try:
            await asyncio.wait_for(stop.wait(), timeout=10)
        except asyncio.TimeoutError:
            pass


async def main() -> None:
    cfg = config.load()
    logging.basicConfig(
        level=getattr(logging, cfg.log_level.upper(), logging.INFO),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    # Ensure the schema exists (idempotent CREATE TABLE IF NOT EXISTS). Safe to
    # run alongside the poller — important here since the DB may be uninitialized.
    db.init_db()
    db.init_db_v2()
    db.init_db_v3()
    db.init_db_v4()
    db.init_db_v5()
    db.init_db_v6()
    db.init_db_v7()

    stop = asyncio.Event()
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    hb = cfg.heartbeat_interval
    tasks: list[asyncio.Task] = []

    if cfg.swim.enabled:
        tasks.append(asyncio.create_task(
            _supervise("swim", lambda: swim.run(cfg.swim, stop, hb), stop)))
        log.info("SWIM (legacy AMQP) source enabled")
    if cfg.nms.enabled:
        tasks.append(asyncio.create_task(
            _supervise("swim_nms", lambda: swim_client.run(cfg.nms, stop), stop)))
        log.info("SWIM NMS (Solace) source enabled")
    if cfg.nwws.enabled:
        tasks.append(asyncio.create_task(
            _supervise("nwws", lambda: nwws.run(cfg.nwws, stop, hb), stop)))
        log.info("NWWS-OI source enabled")
    if cfg.amtrak.enabled:
        tasks.append(asyncio.create_task(
            _supervise("amtrak", lambda: amtrak.run(cfg.amtrak, stop, hb), stop)))
        log.info("Amtrak source enabled")

    # Local airspace monitor runs in its own daemon thread, independent of SWIM.
    # Skip the "no tasks" exit — local airspace may be the only active source.
    local_monitor = LocalAirspaceMonitor()
    threading.Thread(target=local_monitor.run_forever, daemon=True,
                     name="local-airspace").start()
    log.info("Local airspace monitor started")

    if not tasks:
        log.warning("No SWIM/NWWS/Amtrak sources enabled — local airspace monitor only")
        await stop.wait()
        log.info("corporatetraveldc ingest stopped")
        return

    log.info("corporatetraveldc ingest started (%d source[s])", len(tasks))
    await stop.wait()
    log.info("Shutdown requested; stopping sources")
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    log.info("corporatetraveldc ingest stopped")


if __name__ == "__main__":
    asyncio.run(main())
