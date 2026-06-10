"""
train-impact — SR-1 + SR-2 compliant.

Model: ollama/llama3.2:3b (chat-tier)
Schedule: every 15 minutes normally; drops to 5 minutes when a 'train' watchlist
          session is active (SKILL_SCHEDULE active_interval/active_check).
SR-1: log_usage() in finally block
SR-2: hash_gate() on train IDs + statuses + scheduled times

Reads ustrains_departures from DB. Skips cleanly if the ustrains feed is
awaiting_credentials or has no departures.

Produces a train-status narrative and writes it to hot_alerts via
db.insert_route_narrative so the pusher can decide whether to push.
"""

import os
import argparse
import logging
import sys
import time

from common import db
from common.sr1_log import log_usage
from common.sr2_gate import hash_gate

log = logging.getLogger(__name__)

SKILL_NAME = "train-impact"
OLLAMA_BASE_URL   = os.getenv("OLLAMA_BASE_URL", "")
OLLAMA_MODEL      = (os.getenv("OLLAMA_CHAT_MODEL")
                     or os.getenv("OLLAMA_MODEL")
                     or "llama3.2:3b")
MODEL             = OLLAMA_MODEL if OLLAMA_BASE_URL else "deterministic"

# DC-area Amtrak stations served by exec chauffeur operation.
DC_STATIONS = ["US_WAS", "US_BWI", "US_ABE"]  # WAS=Union, BWI Rail, Alexandria

SYSTEM_PROMPT = """You are a ground-transportation dispatch analyst for executive chauffeur operations
in the Washington DC metropolitan area.

Given current Amtrak departure statuses at DC-area stations, produce a concise operational brief:
1. Which trains are delayed and by how much.
2. Whether any delays affect imminent pickups (departure within 90 minutes).
3. Recommended action: hold driver, adjust pickup time, or no change needed.

Maximum 150 words. Direct and operational. No preamble."""


def build_inputs() -> dict | None:
    """
    Return hash-gated inputs, or None if the feed is not live.
    Hash on: train_id + status + scheduled (not fetched_at).
    """
    # Check feed health — skip if awaiting credentials or no data.
    with db.conn() as c:
        row = c.execute(
            "SELECT error, fetched_at FROM feed_state WHERE feed_name='ustrains'"
        ).fetchone()
    if not row:
        return None
    if row["error"] and "awaiting_credentials" in (row["error"] or ""):
        return None
    # Skip if data is stale (>30 min old).
    if time.time() - (row["fetched_at"] or 0) > 1800:
        return None

    departures = db.get_ustrains_departures()
    if not departures:
        return None

    return {
        "departures": sorted([
            {
                "train_id":    d["train_id"],
                "station_id":  d["station_id"],
                "destination": d["destination"],
                "scheduled":   d["scheduled"],
                "status":      d["status"],
            }
            for d in departures
        ], key=lambda x: (x["station_id"], x["scheduled"] or "")),
    }


def build_user_message(inputs: dict) -> str:
    lines = []
    last_station = None
    for d in inputs["departures"]:
        if d["station_id"] != last_station:
            lines.append(f"\nStation: {d['station_id']}")
            last_station = d["station_id"]
        status = d["status"] or "unknown"
        sched = d["scheduled"] or "TBD"
        lines.append(
            f"  Train {d['train_id']} → {d['destination'] or '?'} "
            f"dep {sched} | {status}"
        )
    return "Current Amtrak departures:\n" + "\n".join(lines)


def main(force: bool = False) -> None:
    inputs = build_inputs()
    if inputs is None:
        log.debug("%s: ustrains feed not live — skipping", SKILL_NAME)
        sys.exit(0)

    gate_result = hash_gate(SKILL_NAME, inputs, force=force)
    if gate_result == "skipped":
        log.debug("%s: inputs unchanged — skipping API call", SKILL_NAME)
        sys.exit(0)

    status = "error"

    try:
        # Deterministic narrative — no API dependency.
        narrative = build_user_message(inputs)
        train_ids = [d["train_id"] for d in inputs["departures"]]
        db.insert_route_narrative(narrative, train_ids, [])
        status = "ok"
        log.info("%s: OK — %d departures", SKILL_NAME, len(train_ids))

    finally:
        log_usage(SKILL_NAME, MODEL, 0, 0, status, gate_result)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description=f"{SKILL_NAME} skill")
    parser.add_argument("--force", action="store_true",
                        help="Bypass hash gate; invoke API regardless of input state")
    args = parser.parse_args()
    main(force=args.force)