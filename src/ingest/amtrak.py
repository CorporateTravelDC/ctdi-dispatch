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


def parse_feed(raw: bytes, cfg: "AmtrakConfig") -> tuple[list[dict], str]:
    """
    Parse amtraker v3 JSON response. Returns (trains, delay_summary).

    Filtering logic:
      1. Watchlist stations  -- any train serving these triggers a per-train alert
      2. Regional stations   -- trains touching any of these are included in the
                                ops brief / delay summary (broader net)
      3. Primary station     -- map centering, used as fallback for both above

    amtraker v3 shape: {train_key: [train_obj, ...], ...}
    """
    raw_data = json.loads(raw)
    primary = cfg.filter_station.upper()
    regional = frozenset(s.upper() for s in cfg.regional_stations)
    watchlist = frozenset(s.upper() for s in cfg.watchlist_stations)
    all_watched = regional | watchlist | {primary}

    trains: list[dict] = []

    for train_key, v in raw_data.items():
        entries = v if isinstance(v, list) else [v]
        for t in entries:
            station_codes = {s.get("code", "") for s in t.get("stations", [])}
            if not (station_codes & all_watched):
                continue

            # Find best matching station for arrival/departure times
            # Priority: watchlist > regional > primary
            ref_station = None
            for priority_set in (watchlist, regional, {primary}):
                match = next(
                    (s for s in t.get("stations", [])
                     if s.get("code") in priority_set),
                    None
                )
                if match:
                    ref_station = match
                    break
            stn = ref_station or {}

            delay = _delay_minutes(t, stn.get("code", primary))
            all_stns = t.get("stations", [])
            serving_watched = sorted(station_codes & all_watched)

            trains.append({
                "train_num":        str(t.get("trainNum", train_key)),
                "route":            t.get("routeName", ""),
                "origin":           all_stns[0].get("code", "") if all_stns else "",
                "destination":      all_stns[-1].get("code", "") if all_stns else "",
                "status":           stn.get("status") or t.get("trainTimely") or "",
                "delay_minutes":    delay,
                "scheduled_arr":    stn.get("schArr"),
                "estimated_arr":    stn.get("arr"),
                "scheduled_dep":    stn.get("schDep"),
                "estimated_dep":    stn.get("dep"),
                "lat":              t.get("lat"),
                "lon":              t.get("lon"),
                "train_id":         t.get("trainID", ""),
                "serving_watched":  serving_watched,
                "is_watchlist":     bool(station_codes & watchlist),
                "is_regional":      bool(station_codes & regional),
            })

    # Build summary
    if not trains:
        summary = f"No trains serving watched stations ({primary})."
    else:
        delayed = [t for t in trains if t.get("delay_minutes", 0) >= 15]
        watchlist_delayed = [t for t in delayed if t.get("is_watchlist")]
        if not delayed:
            summary = f"All {len(trains)} watched trains on time or minor delay."
        else:
            worst = sorted(delayed, key=lambda x: -x["delay_minutes"])
            lines = [
                f"#{t['train_num']} {t['route']}: +{t['delay_minutes']}min"
                + (" [WATCH]" if t.get("is_watchlist") else "")
                for t in worst[:5]
            ]
            summary = (
                f"{len(delayed)}/{len(trains)} trains delayed"
                + (f" ({len(watchlist_delayed)} watchlist)" if watchlist_delayed else "")
                + ". " + "; ".join(lines)
            )
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
                trains, summary = parse_feed(resp.content, cfg)
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
