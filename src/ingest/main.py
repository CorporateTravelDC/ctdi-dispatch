"""
ingest.main — async supervisor for the push-ingest service.

Launches the enabled sources (SWIM NMS, NWWS-OI, Amtrak), each in its own supervised
task that reconnects on failure, and shuts them down cleanly on SIGTERM/SIGINT
(so `systemctl --user stop corporatetraveldc-ingest` is graceful).

Entry point matches the rest of your tree:  python3 -m ingest.main
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import threading

from common import db
from ingest import amtrak, config, nwws, swim_client
from ingest.local_airspace import LocalAirspaceMonitor

# Added 2026-07-26 when ingest was split into per-SWIM-feed containers (see
# systemd/quadlets/corporatetraveldc-ingest-*.container): local_airspace has
# no per-source "enabled" field of its own -- it just always ran, which was
# fine when there was exactly one ingest process. With seven containers now
# sharing this same image (one core + six single-feed), only ONE of them
# should actually run it. Defaults to True so the original single-container
# deployment (and the new "ingest-core" container) keep working unchanged;
# the six per-feed containers set this to false in their Quadlet units.
def _local_airspace_enabled() -> bool:
    return os.getenv("LOCAL_AIRSPACE_ENABLED", "true").strip().lower() not in ("0", "false", "no")

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
    db.init_db_v13()
    db.init_db_v14()
    db.init_db_v15()
    db.init_db_v18()
    db.init_db_v19()
    db.init_db_v20()
    db.init_db_v28()
    db.init_db_v29()

    stop = asyncio.Event()
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    hb = cfg.heartbeat_interval
    tasks: list[asyncio.Task] = []

    # NOTE: the legacy AMQP SWIM client (ingest/swim.py, SwimConfig) was
    # removed 2026-07-19 -- NMS/Solace (below) is the only SWIM transport now.
    # See tests/ingest/test_legacy_amqp_removed.py.
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
    # Gated per-container now that ingest can run as seven separate
    # containers sharing this image -- see _local_airspace_enabled() above.
    if _local_airspace_enabled():
        local_monitor = LocalAirspaceMonitor()
        threading.Thread(target=local_monitor.run_forever, daemon=True,
                         name="local-airspace").start()
        log.info("Local airspace monitor started")
    else:
        log.info("Local airspace monitor disabled for this container (LOCAL_AIRSPACE_ENABLED=false)")

    if not tasks:
        log.warning("No SWIM/NWWS/Amtrak sources enabled for this container")
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
