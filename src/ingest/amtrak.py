"""
ingest.amtrak — Amtrak status, polled inside ingest as the PRIMARY path.

Amtrak has no push feed, so this is still a poll — but running it here makes it
the primary source and lets the poller's own `amtrak` fetcher act as the
fallback (it only fires when this heartbeat goes stale). Writes via
db.insert_amtrak_status — the same call the poller fetcher uses — so there is
never a double-write: exactly one of the two is active at a time.

Feed: AMTRAK_FEED_URL=https://api.amtraker.com/v3/trains
Response: dict of {train_key: list[train_obj]} — mirrors amtrak_tracker/main.py schema.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime

from common import db
from ingest import failover
from ingest.config import AmtrakConfig

log = logging.getLogger("ingest.amtrak")


def _delay_minutes(train: dict, station_code: str) -> int:
    """Compute delay in whole minutes at station_code. 0 = on time or unknown."""
    stn = next(
        (s for s in train.get("stations", []) if s.get("code") == station_code),
        None,
    )
    if not stn:
        return 0
    sch = stn.get("schArr") or stn.get("schDep")
    est = stn.get("arr") or stn.get("dep")
    if not (sch and est):
        return 0
    try:
        s_dt = datetime.fromisoformat(sch.replace("Z", "+00:00"))
        e_dt = datetime.fromisoformat(est.replace("Z", "+00:00"))
        return max(0, int((e_dt - s_dt).total_seconds() / 60))
    except Exception:
        return 0


def parse_feed(raw: bytes, station: str) -> tuple[list[dict], str]:
    """
    Parse amtraker v3 JSON response, filter to trains serving `station`,
    and return (trains, delay_summary).

    amtraker v3 shape: {train_key: [train_obj, ...], ...}
    Each train_obj has: trainNum, routeName, stations[], lat, lon, trainID, trainTimely
    Each station entry has: code, schArr, schDep, arr, dep, status
    """
    raw_data = json.loads(raw)
    trains: list[dict] = []

    for train_key, v in raw_data.items():
        entries = v if isinstance(v, list) else [v]
        for t in entries:
            station_codes = [s.get("code") for s in t.get("stations", [])]
            if station not in station_codes:
                continue

            stn = next(
                (s for s in t.get("stations", []) if s.get("code") == station),
                {},
            )
            delay = _delay_minutes(t, station)
            all_stns = t.get("stations", [])

            trains.append({
                "train_num":     str(t.get("trainNum", train_key)),
                "route":         t.get("routeName", ""),
                "origin":        all_stns[0].get("code", "") if all_stns else "",
                "destination":   all_stns[-1].get("code", "") if all_stns else "",
                "status":        stn.get("status") or t.get("trainTimely") or "",
                "delay_minutes": delay,
                "scheduled_arr": stn.get("schArr"),
                "estimated_arr": stn.get("arr"),
                "scheduled_dep": stn.get("schDep"),
                "estimated_dep": stn.get("dep"),
                "lat":           t.get("lat"),
                "lon":           t.get("lon"),
                "train_id":      t.get("trainID", ""),
            })

    # Build summary
    if not trains:
        summary = f"No trains serving {station}."
    else:
        delayed = [t for t in trains if t.get("delay_minutes", 0) >= 15]
        if not delayed:
            summary = f"All {len(trains)} {station} trains on time or minor delay."
        else:
            worst = sorted(delayed, key=lambda x: -x["delay_minutes"])
            lines = [
                f"#{t['train_num']} {t['route']}: +{t['delay_minutes']}min"
                for t in worst[:5]
            ]
            summary = f"{len(delayed)}/{len(trains)} trains delayed. " + "; ".join(lines)
            if len(delayed) > 5:
                summary += f" (+{len(delayed)-5} more)"

    return trains, summary


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
