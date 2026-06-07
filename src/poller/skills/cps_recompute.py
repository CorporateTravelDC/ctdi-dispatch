"""
cps-recompute — SR-1 + SR-2 compliant.

Model: claude-haiku-4-5-20251001 (Haiku — lower spend; hourly schedule)
Schedule: hourly at :05 (corporatetraveldc-cps-recompute.timer)
SR-1: log_usage() in finally block
SR-2: hash_gate() on raw numeric METAR inputs + active NAS programs

Produces a CPS (Critical Predictability State) traffic-light score
mapped to Part 135.609 HEMS minimums.
Writes result to cps_scores table; pusher fires ntfy topic "cps".
"""

import argparse
import json
import logging
import sys

import anthropic

from common import config, db
from common.sr1_log import log_usage
from common.sr2_gate import hash_gate

log = logging.getLogger(__name__)

SKILL_NAME = "cps-recompute"
MODEL = "claude-haiku-4-5-20251001"

# Part 135.609 HEMS VFR minimums (DC area / Class B):
#   Ceiling >= 1000 ft, Visibility >= 3 SM, Wind <= 30 kt
CPS_MINIMUMS = {"ceiling_ft": 1000, "visibility_sm": 3.0, "wind_kt": 30}

SYSTEM_PROMPT = """You are evaluating HEMS (helicopter EMS) flight condition scores
for the Washington DC metropolitan area against FAA Part 135.609 VFR minimums.

Minimums: ceiling ≥ 1000 ft, visibility ≥ 3 SM, winds ≤ 30 kt.

Given weather observations and NAS delay programs, output ONLY valid JSON:
{
  "score": "GREEN" | "YELLOW" | "RED",
  "label": "GO" | "MARGINAL" | "NO-GO",
  "factors": {
    "ceiling": "ok" | "marginal" | "violated",
    "visibility": "ok" | "marginal" | "violated",
    "wind": "ok" | "marginal" | "violated",
    "precip": "ok" | "marginal" | "violated",
    "airspace": "ok" | "marginal" | "violated",
    "gdp": "ok" | "marginal" | "violated"
  },
  "narrative": "One sentence summary of limiting factor(s)"
}

GREEN/GO: all factors ok.
YELLOW/MARGINAL: any factor marginal (ceiling 1000-1500, vis 3-5, wind 25-30).
RED/NO-GO: any factor violated.
Output JSON only. No preamble."""


def build_inputs() -> dict:
    metars = db.get_metar_snapshot()
    nas = db.get_active_nas_programs()

    # Hash only the numeric content fields — not obs_time or fetched_at.
    return {
        "metar": sorted([
            {
                "station": m["station"],
                "ceiling_ft": m["ceiling_ft"],
                "visibility_sm": m["visibility_sm"],
                "wind_kt": m["wind_kt"],
                "precip": m["precip_code"],
            }
            for m in metars
        ], key=lambda x: x["station"]),
        "nas_programs": sorted([
            {"type": p["type"], "facility": p["facility"]}
            for p in nas
        ], key=lambda x: (x["type"], x["facility"])),
    }


def build_user_message(inputs: dict) -> str:
    metar_lines = [
        f"  {m['station']}: ceiling={m['ceiling_ft']}ft "
        f"vis={m['visibility_sm']}SM wind={m['wind_kt']}kt "
        f"precip={m['precip'] or 'nil'}"
        for m in inputs["metar"]
    ] or ["  No METAR data"]

    nas_lines = [
        f"  {p['type']} at {p['facility']}"
        for p in inputs["nas_programs"]
    ] or ["  None active"]

    # Compute worst-case summary for primary stations.
    primaries = {m["station"]: m for m in inputs["metar"]
                 if m["station"] in ("KDCA", "KIAD", "KBWI")}
    summary = []
    if primaries:
        ceilings = [m["ceiling_ft"] for m in primaries.values()
                    if m["ceiling_ft"] is not None]
        viss = [m["visibility_sm"] for m in primaries.values()
                if m["visibility_sm"] is not None]
        winds = [m["wind_kt"] for m in primaries.values()
                 if m["wind_kt"] is not None]
        if ceilings:
            summary.append(f"Lowest ceiling: {min(ceilings)} ft")
        if viss:
            summary.append(f"Lowest visibility: {min(viss)} SM")
        if winds:
            summary.append(f"Max wind: {max(winds)} kt")

    return (
        "METAR observations:\n" + "\n".join(metar_lines) + "\n\n"
        "NAS delay programs:\n" + "\n".join(nas_lines) + "\n\n"
        "Primary station summary: " + ("; ".join(summary) or "No data")
    )


def main(force: bool = False) -> None:
    inputs = build_inputs()
    gate_result = hash_gate(SKILL_NAME, inputs, force=force)

    if gate_result == "skipped":
        log.debug("%s: inputs unchanged — skipping API call", SKILL_NAME)
        sys.exit(0)

    client = anthropic.Anthropic(api_key=config.anthropic_api_key())
    input_tokens = output_tokens = 0
    status = "error"

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=400,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": build_user_message(inputs)}],
        )
        input_tokens = response.usage.input_tokens
        output_tokens = response.usage.output_tokens
        status = "ok"

        raw = response.content[0].text.strip()
        # Strip any accidental markdown fences.
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw)

        db.insert_cps(
            score=data["score"],
            label=data["label"],
            factors=data.get("factors", {}),
            narrative=data.get("narrative", ""),
        )
        log.info("%s: %s/%s — %d+%d tokens",
                 SKILL_NAME, data["score"], data["label"],
                 input_tokens, output_tokens)

    except json.JSONDecodeError as e:
        log.error("%s: JSON parse error: %s", SKILL_NAME, e)
        status = "error"
    finally:
        log_usage(SKILL_NAME, MODEL, input_tokens, output_tokens, status, gate_result)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description=f"{SKILL_NAME} skill")
    parser.add_argument("--force", action="store_true",
                        help="Bypass hash gate; invoke API regardless of input state")
    args = parser.parse_args()
    main(force=args.force)
