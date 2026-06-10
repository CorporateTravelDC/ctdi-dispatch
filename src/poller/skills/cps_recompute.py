"""
cps-recompute — SR-1 + SR-2 compliant. API-free deterministic rule engine.

Schedule: hourly at :05 (corporatetraveldc-cps-recompute.timer)
SR-1: log_usage() in finally block
SR-2: hash_gate() on raw numeric METAR inputs + active NAS programs

Produces a CPS (Critical Predictability State) traffic-light score
mapped to Part 135.609 HEMS minimums using a deterministic rule engine.
No AI API calls. Always produces a result.
Writes result to cps_scores table; pusher fires ntfy topic "cps".
"""

import argparse
import logging
import sys

from common import db
from common.sr1_log import log_usage
from common.sr2_gate import hash_gate

log = logging.getLogger(__name__)

SKILL_NAME = "cps-recompute"
MODEL = "deterministic"   # no model; kept for SR-1 log_usage signature

# Part 135.609 HEMS VFR minimums (DC area / Class B):
#   Ceiling >= 1000 ft, Visibility >= 3 SM, Wind <= 30 kt
CPS_MINIMUMS = {"ceiling_ft": 1000, "visibility_sm": 3.0, "wind_kt": 30}

# Primary observation stations for DC-area HEMS scoring (in priority order)
PRIMARY_STATIONS = ("KDCA", "KIAD", "KBWI")

# Precip codes that violate or degrade minimums
PRECIP_VIOLATED = frozenset({
    "TS", "TSRA", "TSGR", "TSPL", "TSSN", "FZRA", "FZDZ", "FZFG",
    "SN", "SG", "PL", "IC", "GR", "GS",
})
PRECIP_MARGINAL = frozenset({
    "RA", "DZ", "BR", "FG", "HZ", "FU", "VA", "SHRA", "RASN",
})


def build_inputs() -> dict:
    metars = db.get_metar_snapshot()
    nas = db.get_active_nas_programs()

    return {
        "metar": sorted([
            {
                "station":       m["station"],
                "ceiling_ft":    m["ceiling_ft"],
                "visibility_sm": m["visibility_sm"],
                "wind_kt":       m["wind_kt"],
                "precip":        m["precip_code"],
            }
            for m in metars
        ], key=lambda x: x["station"]),
        "nas_programs": sorted([
            {"type": p["type"], "facility": p["facility"]}
            for p in nas
        ], key=lambda x: (x["type"], x["facility"])),
    }


def _compute_cps(inputs: dict) -> dict:
    """
    Deterministic Part 135.609 HEMS go/no-go rule engine.
    Returns dict with score, label, factors, narrative.
    """
    primaries = {m["station"]: m for m in inputs["metar"]
                 if m["station"] in PRIMARY_STATIONS}
    nas = inputs["nas_programs"]

    factors = {
        "ceiling":    "ok",
        "visibility": "ok",
        "wind":       "ok",
        "precip":     "ok",
        "airspace":   "ok",
        "gdp":        "ok",
    }

    worst = "ok"
    limiting: list[str] = []

    def degrade(factor: str, level: str, msg: str) -> None:
        nonlocal worst
        prev = factors[factor]
        # Never downgrade severity
        if level == "violated" or (level == "marginal" and prev == "ok"):
            factors[factor] = level
        if level == "violated":
            if worst != "violated":
                worst = "violated"
            if msg not in limiting:
                limiting.append(msg)
        elif level == "marginal":
            if worst == "ok":
                worst = "marginal"
            if msg not in limiting:
                limiting.append(msg)

    for sta in PRIMARY_STATIONS:
        m = primaries.get(sta)
        if not m:
            continue

        # Ceiling check
        c = m.get("ceiling_ft")
        if c is not None:
            if c < CPS_MINIMUMS["ceiling_ft"]:
                degrade("ceiling", "violated",
                        f"{sta} ceiling {c}ft (min {CPS_MINIMUMS['ceiling_ft']}ft)")
            elif c < 1500:
                degrade("ceiling", "marginal",
                        f"{sta} ceiling {c}ft marginal (1000–1500ft range)")

        # Visibility check
        v = m.get("visibility_sm")
        if v is not None:
            if v < CPS_MINIMUMS["visibility_sm"]:
                degrade("visibility", "violated",
                        f"{sta} vis {v}SM (min {CPS_MINIMUMS['visibility_sm']}SM)")
            elif v < 5.0:
                degrade("visibility", "marginal",
                        f"{sta} vis {v}SM marginal (3–5SM range)")

        # Wind check
        w = m.get("wind_kt")
        if w is not None:
            if w > CPS_MINIMUMS["wind_kt"]:
                degrade("wind", "violated",
                        f"{sta} wind {w}kt (max {CPS_MINIMUMS['wind_kt']}kt)")
            elif w >= 25:
                degrade("wind", "marginal",
                        f"{sta} wind {w}kt marginal (25–30kt range)")

        # Precipitation check
        p = str(m.get("precip") or "").upper()
        precip_tokens = set(p.split())
        if precip_tokens & PRECIP_VIOLATED:
            codes = " ".join(sorted(precip_tokens & PRECIP_VIOLATED))
            degrade("precip", "violated", f"{sta} {codes}")
        elif precip_tokens & PRECIP_MARGINAL:
            codes = " ".join(sorted(precip_tokens & PRECIP_MARGINAL))
            degrade("precip", "marginal", f"{sta} {codes}")

    # NAS ground programs
    gsps = [p for p in nas
            if "GSP" in p.get("type", "").upper()
            or "GROUND_STOP" in p.get("type", "").upper()]
    gdps = [p for p in nas
            if "GDP" in p.get("type", "").upper()
            and p not in gsps]

    if gsps:
        facilities = ", ".join(p["facility"] for p in gsps[:3])
        degrade("gdp", "violated", f"Ground Stop at {facilities}")
    elif gdps:
        facilities = ", ".join(p["facility"] for p in gdps[:3])
        degrade("gdp", "marginal", f"GDP at {facilities}")

    # Score
    if worst == "violated":
        score, label = "RED", "NO-GO"
    elif worst == "marginal":
        score, label = "YELLOW", "MARGINAL"
    else:
        score, label = "GREEN", "GO"

    # Narrative
    if limiting:
        narrative = limiting[0]
        if len(limiting) > 1:
            extra = len(limiting) - 1
            narrative += f" (+{extra} other factor{'s' if extra > 1 else ''})"
    elif not primaries:
        narrative = "No primary station METAR data — CPS based on NAS programs only"
    else:
        narrative = "All factors within Part 135.609 HEMS minimums"

    return {
        "score":     score,
        "label":     label,
        "factors":   factors,
        "narrative": narrative,
    }


def main(force: bool = False) -> None:
    inputs = build_inputs()
    gate_result = hash_gate(SKILL_NAME, inputs, force=force)

    if gate_result == "skipped":
        log.debug("%s: inputs unchanged — skipping recompute", SKILL_NAME)
        sys.exit(0)

    status = "error"
    try:
        data = _compute_cps(inputs)

        db.insert_cps(
            score=data["score"],
            label=data["label"],
            factors=data["factors"],
            narrative=data["narrative"],
        )
        status = "ok"
        log.info("%s: %s/%s — %s", SKILL_NAME, data["score"], data["label"], data["narrative"])

    except Exception as e:
        log.error("%s: compute error: %s", SKILL_NAME, e)
        status = "error"
        raise
    finally:
        # SR-1: log with 0 tokens (deterministic — no API usage)
        log_usage(SKILL_NAME, MODEL, 0, 0, status, gate_result)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description=f"{SKILL_NAME} skill")
    parser.add_argument("--force", action="store_true",
                        help="Bypass hash gate; recompute regardless of input state")
    args = parser.parse_args()
    main(force=args.force)
