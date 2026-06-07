"""
amtrak_tracker.main — push-primary Amtrak status source for WAS.

Polls amtraker.com on a fixed interval, filters for trains serving
FILTER_STATION (default WAS), normalizes to the schema expected by
train_impact and the web API, and writes to amtrak_status.

Heartbeat pattern:
  push:amtrak in feed_state — stamped on every successful write.
  poller's amtrak fetcher checks push_is_healthy("amtrak", 90) and
  skips its own REST call while this container is healthy.

Historical readback:
  amtrak_status is append-only. Rows older than HISTORY_HOURS are
  pruned each cycle to bound table size (~288 rows at 5 min interval).
"""

import json
import logging
import os
import signal
import time
from datetime import datetime, timezone, timedelta

import requests

from common import db
from ingest.failover import mark_push_healthy, mark_push_down

log = logging.getLogger(__name__)

AMTRAKER_URL = "https://api.amtraker.com/v3/trains"
FILTER_STATION = os.environ.get("FILTER_STATION", "WAS")
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL_SECS", "300"))
HISTORY_HOURS = int(os.environ.get("HISTORY_HOURS", "24"))
FEED_NAME = "amtrak"


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


def fetch_and_normalize(station: str) -> list[dict]:
    """
    Pull all trains from amtraker v3, filter those serving station,
    and return a flat normalized list suitable for train_impact and the web API.
    """
    resp = requests.get(
        AMTRAKER_URL, timeout=20,
        headers={"User-Agent": "corporatetraveldc-amtrak-tracker/1.0"},
    )
    resp.raise_for_status()
    raw = resp.json()

    trains: list[dict] = []
    for train_key, v in raw.items():
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

            # Derive first and last station for origin/destination display
            all_stns = t.get("stations", [])
            origin_code = all_stns[0].get("code", "") if all_stns else ""
            dest_code = all_stns[-1].get("code", "") if all_stns else ""

            trains.append({
                "train_num": str(t.get("trainNum", train_key)),
                "route": t.get("routeName", ""),
                "origin": origin_code,
                "destination": dest_code,
                "status": stn.get("status") or t.get("trainTimely") or "",
                "delay_minutes": delay,
                "scheduled_arr": stn.get("schArr"),
                "estimated_arr": stn.get("arr"),
                "scheduled_dep": stn.get("schDep"),
                "estimated_dep": stn.get("dep"),
                "lat": t.get("lat"),
                "lon": t.get("lon"),
                "train_id": t.get("trainID", ""),
            })

    return trains


def _summarize(trains: list[dict]) -> str:
    if not trains:
        return f"No trains serving {FILTER_STATION}."
    delayed = [t for t in trains if t.get("delay_minutes", 0) >= 15]
    if not delayed:
        return f"All {len(trains)} {FILTER_STATION} trains on time or minor delay."
    worst = sorted(delayed, key=lambda x: -x["delay_minutes"])
    lines = [
        f"#{t['train_num']} {t['route']}: +{t['delay_minutes']}min"
        for t in worst[:5]
    ]
    s = f"{len(delayed)}/{len(trains)} trains delayed. " + "; ".join(lines)
    if len(delayed) > 5:
        s += f" (+{len(delayed)-5} more)"
    return s


def _prune_old_records() -> None:
    cutoff = time.time() - (HISTORY_HOURS * 3600)
    with db.conn() as c:
        c.execute("DELETE FROM amtrak_status WHERE fetched_at < ?", (cutoff,))


def run_once() -> None:
    trains = fetch_and_normalize(FILTER_STATION)
    summary = _summarize(trains)
    db.insert_amtrak_status(
        trains_json=json.dumps(trains),
        delay_summary=summary,
    )
    mark_push_healthy(FEED_NAME)
    _prune_old_records()
    log.info("amtrak-tracker: %d trains at %s — %s",
             len(trains), FILTER_STATION, summary[:100])


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    shutdown = False

    def _stop(sig, _frame):
        nonlocal shutdown
        shutdown = True
        mark_push_down(FEED_NAME, f"amtrak-tracker stopped (signal {sig})")

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    log.info("amtrak-tracker started — station=%s interval=%ds history=%dh",
             FILTER_STATION, POLL_INTERVAL, HISTORY_HOURS)

    while not shutdown:
        try:
            run_once()
        except requests.exceptions.RequestException as e:
            mark_push_down(FEED_NAME, str(e))
            log.warning("amtrak-tracker fetch failed (will retry): %s", e)
        except Exception as e:
            mark_push_down(FEED_NAME, str(e))
            log.error("amtrak-tracker error: %s", e, exc_info=True)

        for _ in range(POLL_INTERVAL):
            if shutdown:
                break
            time.sleep(1)

    log.info("amtrak-tracker stopped")


if __name__ == "__main__":
    main()
