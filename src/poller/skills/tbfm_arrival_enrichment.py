"""
tbfm_arrival_enrichment -- fills flight_events.arrival_time from
tbfm_sequences.eta, closing the gap root-caused 2026-08-07 (see
20260807-internal-03 commit message / AI-memory): fdps_parser.py's
write_flight_event() hardcodes arrival_time=None on every FDPS write --
FDPS track/plan messages don't carry an arrival-time estimate natively.
tbfm_sequences DOES carry a real ETA (TBFM = arrival sequencing), but
the two tables key on different flight identifiers:

  - flight_events.flight_id is FDPS's GUFI (UUID, e.g.
    "ef47403f-c0b2-49df-9d66-b0ff3f6bdb2c")
  - tbfm_sequences.flight_id is actually the CALLSIGN (e.g. "UAL1742"),
    despite the shared column name -- confirmed empirically 2026-08-07:
    0 rows match on direct flight_id equality; airline||flight_num
    concatenation against tbfm_sequences.flight_id produces real,
    plausible matches (all DC-area destinations, consistent with TBFM's
    scope as DC-TRACON arrival-sequencing data).

Join key is therefore flight_events.airline || flight_events.flight_num
= tbfm_sequences.flight_id. Both sides are freshness-scoped:
  - flight_events: updated_at within _FLIGHT_EVENTS_LOOKBACK_S (only
    actively-tracked flights)
  - tbfm_sequences: last_seen within _TBFM_LOOKBACK_S (confirmed by
    testing this is necessary -- callsigns are reused daily, so an
    unscoped join attached a 3-day-stale TBFM row to a currently-active
    flight_events row on the first test run)
A flight can appear at multiple TBFM meter fixes; the most-recently-seen
row per callsign wins (SQL window function, ROW_NUMBER OVER PARTITION).

Does NOT touch flight_events.updated_at -- see
db.enrich_flight_arrival_times()'s docstring for why.

SR-1: not applicable, no Anthropic API call.
SR-2: exempt -- deterministic, no LLM call.
"""
import logging
import sqlite3
import time
from datetime import datetime, timezone

from common import config, db
from common.sr1_log import log_usage

log = logging.getLogger(__name__)

SKILL_NAME = "tbfm-arrival-enrichment"

_FLIGHT_EVENTS_LOOKBACK_S = 7200   # 2h -- only actively-tracked flights
_TBFM_LOOKBACK_S = 10800           # 3h -- only fresh TBFM sequencing data

_ENRICH_QUERY = """
SELECT fe.flight_id, t.eta
FROM flight_events fe
JOIN (
    SELECT flight_id, eta, last_seen,
           ROW_NUMBER() OVER (PARTITION BY flight_id ORDER BY last_seen DESC) rn
    FROM tbfm_sequences
    WHERE last_seen > ?
) t ON t.flight_id = (fe.airline || fe.flight_num) AND t.rn = 1
WHERE fe.updated_at > ?
"""


def _parse_iso_to_epoch(iso_ts: str) -> float | None:
    try:
        dt = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
        return None


def run_enrichment() -> dict:
    """Returns {"candidates": N, "updated": N}. Never raises -- a broken
    enrichment pass must not itself become an outage."""
    now = time.time()
    tbfm_cutoff_iso = datetime.fromtimestamp(
        now - _TBFM_LOOKBACK_S, tz=timezone.utc
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    fe_cutoff = now - _FLIGHT_EVENTS_LOOKBACK_S

    conn = sqlite3.connect(config.db_path())
    try:
        rows = conn.execute(_ENRICH_QUERY, (tbfm_cutoff_iso, fe_cutoff)).fetchall()
    finally:
        conn.close()

    updates: list[tuple[float, str]] = []
    for flight_id, eta_iso in rows:
        epoch = _parse_iso_to_epoch(eta_iso)
        if epoch is not None:
            updates.append((epoch, flight_id))

    updated = db.enrich_flight_arrival_times(updates)
    return {"candidates": len(rows), "updated": updated}


def main() -> None:
    status = "ok"
    try:
        result = run_enrichment()
        log.info("%s: %d candidate(s), %d row(s) updated",
                  SKILL_NAME, result["candidates"], result["updated"])
    except Exception as e:
        log.error("%s: enrichment pass failed: %s", SKILL_NAME, e)
        status = "error"
    finally:
        log_usage(SKILL_NAME, "deterministic", 0, 0, status, "new")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
