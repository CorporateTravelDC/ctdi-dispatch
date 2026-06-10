"""
flight-impact — SR-1 + SR-2 compliant.

Model: ollama/llama3.2:3b (chat-tier)
Schedule: every 15 minutes normally; drops to 5 minutes when a 'flight' watchlist
          session is active (SKILL_SCHEDULE active_interval/active_check).
SR-1: log_usage() in finally block
SR-2: hash_gate() on flight IDs + statuses + arrival times

Reads flight_events from DB (populated by FAA SWIM / SFDPS push feed).
Skips cleanly when SFDPS has no data yet (parse_sfdps stub, awaiting live sample).

Produces an airport-impact narrative for DCA/IAD/BWI and writes it to hot_alerts.

TODO(sfdps-live): once parse_sfdps is wired and a real SFDPS sample confirmed,
remove the early-exit guard and enable the full narrative path.
"""

import os
import argparse
import logging
import sys

from common import db
from common.sr1_log import log_usage
from common.sr2_gate import hash_gate

log = logging.getLogger(__name__)

SKILL_NAME = "flight-impact"
OLLAMA_BASE_URL   = os.getenv("OLLAMA_BASE_URL", "")
OLLAMA_MODEL      = (os.getenv("OLLAMA_CHAT_MODEL")
                     or os.getenv("OLLAMA_MODEL")
                     or "llama3.2:3b")
MODEL             = OLLAMA_MODEL if OLLAMA_BASE_URL else "deterministic"

DC_AIRPORTS = ["KDCA", "KIAD", "KBWI"]

SYSTEM_PROMPT = """You are a ground-transportation dispatch analyst for executive chauffeur operations
in the Washington DC metropolitan area.

Given active flights arriving or departing DCA, IAD, and BWI, produce a concise operational brief:
1. Which arrivals are imminent (within 60 minutes) at each airport.
2. Any delays, diversions, or cancellations affecting scheduled pickups.
3. Recommended timing adjustments for driver dispatch.

Maximum 200 words. Direct and operational. No preamble."""


def build_inputs() -> dict | None:
    """
    Return hash-gated inputs, or None if SFDPS data is not yet available.
    Hash on: flight_id + status + arrival_time (not position).
    """
    flights = db.get_active_flight_events(airports=DC_AIRPORTS, max_age_seconds=3600)
    if not flights:
        return None

    return {
        "flights": sorted([
            {
                "flight_id":    f["flight_id"],
                "origin":       f["origin"],
                "destination":  f["destination"],
                "arrival_time": f["arrival_time"],
                "status":       f["status"],
            }
            for f in flights
        ], key=lambda x: (x["destination"] or "", x["arrival_time"] or 0)),
    }


def build_user_message(inputs: dict) -> str:
    import datetime
    lines = []
    last_dest = None
    for f in inputs["flights"]:
        if f["destination"] != last_dest:
            lines.append(f"\n{f['destination'] or 'Unknown'}:")
            last_dest = f["destination"]
        eta = ""
        if f["arrival_time"]:
            dt = datetime.datetime.fromtimestamp(
                f["arrival_time"], tz=datetime.timezone.utc
            )
            eta = dt.strftime("%H:%MZ")
        origin = f["origin"] or "?"
        status = f["status"] or "active"
        lines.append(f"  {f['flight_id']} from {origin} ETA {eta} | {status}")
    return "Active flights — DC-area airports:\n" + "\n".join(lines)


def main(force: bool = False) -> None:
    inputs = build_inputs()
    if inputs is None:
        log.debug("%s: no SFDPS flight data — skipping (stub until live feed wired)",
                  SKILL_NAME)
        sys.exit(0)

    gate_result = hash_gate(SKILL_NAME, inputs, force=force)
    if gate_result == "skipped":
        log.debug("%s: inputs unchanged — skipping API call", SKILL_NAME)
        sys.exit(0)

    status = "error"

    try:
        # Deterministic narrative — no API dependency.
        narrative = build_user_message(inputs)
        flight_ids = [f["flight_id"] for f in inputs["flights"]]
        db.insert_route_narrative(narrative, flight_ids, [])
        status = "ok"
        log.info("%s: OK — %d flights", SKILL_NAME, len(flight_ids))

    finally:
        log_usage(SKILL_NAME, MODEL, 0, 0, status, gate_result)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description=f"{SKILL_NAME} skill")
    parser.add_argument("--force", action="store_true",
                        help="Bypass hash gate; invoke API regardless of input state")
    args = parser.parse_args()
    main(force=args.force)