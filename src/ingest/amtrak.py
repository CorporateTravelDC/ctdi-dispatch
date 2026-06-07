"""
ingest.amtrak — Amtrak status, polled inside ingest as the PRIMARY path.

Amtrak has no push feed, so this is still a poll — but running it here makes it
the primary source and lets the poller's own `amtrak` fetcher act as the
fallback (it only fires when this heartbeat goes stale). Writes via
db.insert_amtrak_status — the same call the poller fetcher uses — so there is
never a double-write: exactly one of the two is active at a time.

PARSING SEAM:  set AMTRAK_FEED_URL to your source and complete parse_feed() to
filter to the station and return (trains:list[dict], delay_summary:str). The old
amtrak-tracker used FILTER_STATION=WAS and emitted a JSON array of train objects
plus a human-readable delay summary; mirror that shape.
"""
from __future__ import annotations

import asyncio
import json
import logging

from common import db
from ingest import failover
from ingest.config import AmtrakConfig

log = logging.getLogger("ingest.amtrak")


def parse_feed(raw: bytes, station: str) -> tuple[list[dict], str]:
    """TODO(operator): parse your Amtrak feed, filter to `station`, and return
    (trains, delay_summary). `trains` is a list of dicts (train_id, route,
    status, delay_minutes, …); delay_summary is one human-readable line."""
    raise NotImplementedError("parse_feed: parse AMTRAK_FEED_URL response, filter to station")


async def run(cfg: AmtrakConfig, stop: asyncio.Event, heartbeat: int) -> None:
    import httpx  # lazy import

    if not cfg.feed_url:
        log.warning("Amtrak enabled but AMTRAK_FEED_URL empty — nothing to poll")
        return

    backoff = 5
    async with httpx.AsyncClient(timeout=30) as http:
        while not stop.is_set():
            try:
                resp = await http.get(cfg.feed_url)
                resp.raise_for_status()
                trains, summary = parse_feed(resp.content, cfg.filter_station)
                db.insert_amtrak_status(json.dumps(trains), summary)
                failover.mark_push_healthy("amtrak")
                log.info("Amtrak: %d train(s) at %s — %s",
                         len(trains), cfg.filter_station, summary)
                backoff = 5
            except NotImplementedError as e:
                log.warning("Amtrak parser seam: %s", e)
                failover.mark_push_down("amtrak", "parse_feed not implemented")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                failover.mark_push_down("amtrak", f"amtrak: {e}")
                log.error("Amtrak poll failed (%s)", e)
                await _sleep_or_stop(stop, backoff)
                backoff = min(backoff * 2, cfg.poll_interval)
                continue
            await _sleep_or_stop(stop, cfg.poll_interval)


async def _sleep_or_stop(stop: asyncio.Event, secs: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout=secs)
    except asyncio.TimeoutError:
        pass
