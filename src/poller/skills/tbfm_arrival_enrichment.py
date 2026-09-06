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
A flight can appear at multiple TBFM meter fixes. This used to pick the
most-recently-seen row per callsign (ROW_NUMBER OVER PARTITION) and take
its `eta` verbatim as the arrival time -- both halves of that were wrong:
the freshest row is very often a facility/meter-point pseudo-label rather
than a real fix, and an "mfx" eta is an ETA to the METER FIX, not the
runway. The SQL now only picks CANDIDATES; the estimate itself comes from
common.cifp_lookup.runway_eta_epoch(), the single arc-aware implementation
shared with fdps_parser.write_flight_event(). See that function's
docstring, and db.init_db_v46() for the eta_kind column it relies on.

Does NOT touch flight_events.updated_at -- see
db.enrich_flight_arrival_times()'s docstring for why.

SR-1: not applicable, no Anthropic API call.
SR-2: exempt -- deterministic, no LLM call.
"""
import logging
import sqlite3
import time
from datetime import datetime, timezone

from common import cifp_lookup, config, db
from common.sr1_log import log_usage
# Legacy hardcoded meter-fix coordinates -- fallback only, for the handful
# of names CIFP does not carry.
from ingest.parsers.tbfm_parser import DC_METER_FIXES as _LEGACY_FIXES

log = logging.getLogger(__name__)

SKILL_NAME = "tbfm-arrival-enrichment"

_FLIGHT_EVENTS_LOOKBACK_S = 7200   # 2h -- only actively-tracked flights
_TBFM_LOOKBACK_S = 10800           # 3h -- only fresh TBFM sequencing data

# Selects CANDIDATE flights only -- the arrival estimate is computed in
# Python from batch-loaded data (see run_enrichment()).
#
# 2026-09-05, PERFORMANCE: an intermediate version JOINed all of
# tbfm_sequences against the computed (airline||flight_num) expression and
# then DISTINCTed it. That expression cannot use an index, so it built a
# huge intermediate against a 935k-row flight_events on a 23 GB, write-
# contended DB -- it did not finish in 200s, blew the unit's start timeout,
# and the skill was SIGKILLed. Driving off flight_events.updated_at instead
# lets idx_flight_events_updated_at cut to the 2h working set FIRST, and
# reduces tbfm_sequences to a small DISTINCT id set before any comparison.
_ENRICH_QUERY = """
SELECT fe.flight_id, (fe.airline || fe.flight_num) AS callsign, fe.destination
FROM flight_events fe
WHERE fe.updated_at > ?
  AND fe.destination IS NOT NULL
  AND (fe.airline || fe.flight_num) IN (
        SELECT DISTINCT flight_id FROM tbfm_sequences WHERE last_seen > ?)
"""


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
        rows = conn.execute(_ENRICH_QUERY, (fe_cutoff, tbfm_cutoff_iso)).fetchall()
    finally:
        conn.close()

    # This used to take tbfm_sequences.eta VERBATIM as the arrival time --
    # wrong in a way that never surfaced as an error: an "mfx" eta is an ETA
    # to the METER FIX, still tens of miles out, so arrival_time landed
    # systematically early. It also fought fdps_parser's inline writer, which
    # applied CIFP transit geometry, flipping the same column between two
    # meanings every 15 min. All consumers now share one arc-aware
    # implementation in cifp_lookup.
    #
    # 2026-09-05, PERFORMANCE: the first version of this called
    # cifp_lookup.runway_eta_epoch() once per candidate, and each of those
    # issued its own sequences query plus a resolve_fix() query per
    # sequence -- thousands of round trips against a 23 GB, single-writer,
    # already write-contended DB. It blew the unit's start timeout and the
    # skill was SIGKILLed (confirmed live, 3.5 min then killed). Batch it:
    # sequences and fix coordinates are each loaded ONCE, then the identical
    # arc-selection logic runs in memory via the shared pure helper.
    fix_coords = cifp_lookup.load_fix_coords()

    seqs_by_flight: dict[str, list[dict]] = {}
    conn = sqlite3.connect(config.db_path())
    conn.row_factory = sqlite3.Row
    try:
        for r in conn.execute(
            """SELECT flight_id, meter_fix, eta, eta_kind, last_seen
                 FROM tbfm_sequences WHERE last_seen > ?
                ORDER BY flight_id, last_seen DESC""", (tbfm_cutoff_iso,)):
            seqs_by_flight.setdefault(r["flight_id"], []).append(dict(r))
    finally:
        conn.close()

    def _resolve(meter_fix):
        return fix_coords.get(meter_fix) or _LEGACY_FIXES.get(meter_fix)

    updates: list[tuple[float, str]] = []
    for flight_id, callsign, destination in rows:
        seqs = seqs_by_flight.get(callsign)
        if not seqs:
            continue
        epoch = cifp_lookup.select_arrival_epoch(seqs, destination, _resolve)
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
