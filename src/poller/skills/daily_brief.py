"""
daily-brief — SR-1 compliant. SR-2 exempt (time-bounded input; always new).

Model: ollama/mistral (OSINT-tier)
Schedule: 05:00 ET daily (corporatetraveldc-daily-brief.timer)
SR-1: log_usage() in finally block
SR-2: Not applicable — this skill summarizes a time window; inputs are always new.

Produces a morning operational brief covering weather, TFRs, CPS, open items.
Pushes to ntfy topic "ops-brief" at priority 3.
"""

import os
import argparse
import json
import logging
import time

from common import config, db
from common.sr1_log import log_usage

log = logging.getLogger(__name__)

SKILL_NAME = "daily-brief"
OLLAMA_BASE_URL   = os.getenv("OLLAMA_BASE_URL", "")
OLLAMA_MODEL      = (os.getenv("OLLAMA_OSINT_MODEL")
                     or os.getenv("OLLAMA_MODEL")
                     or "mistral")
MODEL             = OLLAMA_MODEL if OLLAMA_BASE_URL else "deterministic"

SYSTEM_PROMPT = """You are producing a morning operational brief for an executive chauffeur
operation in the Washington DC metropolitan area. The operator also serves as a credentialed
CERT/ARES volunteer.

Produce a concise morning brief covering:
1. **Weather outlook** — current conditions at DCA/IAD/BWI, any significant fronts or hazards
2. **Active TFRs** — any VIP/POTUS movements or security restrictions active or expected
3. **CPS status** — current Critical Predictability State for HEMS operations
4. **NAS programs** — any ground stops, GDPs, or delay programs affecting DC airports
5. **Operational notes** — anything a professional chauffeur should know for today

Keep it under 400 words. Use plain text — this will be sent as a push notification.
Lead with the most operationally significant item."""


def build_brief_content() -> str:
    metars = db.get_metar_snapshot()
    tfrs = db.get_active_tfrs()
    nas = db.get_active_nas_programs()
    cps = db.get_latest_cps()
    route = db.get_latest_route_narrative()

    sections = []

    # CPS
    if cps:
        sections.append(
            f"CPS: {cps['score']}/{cps['label']} — {cps['narrative'] or 'No narrative'}"
        )

    # VIP TFRs
    vip_tfrs = [t for t in tfrs if t["is_vip"]]
    if vip_tfrs:
        ids = ", ".join(t["tfr_id"] for t in vip_tfrs)
        sections.append(f"VIP TFRs active: {ids}")

    # METAR summary (primaries only)
    primary = [m for m in metars if m["station"] in ("KDCA", "KIAD", "KBWI")]
    if primary:
        metar_lines = [
            f"{m['station']}: {m['ceiling_ft']}ft/{m['visibility_sm']}SM/"
            f"{m['wind_kt']}kt {'(' + m['precip_code'] + ')' if m['precip_code'] else ''}"
            for m in primary
        ]
        sections.append("Weather:\n" + "\n".join(metar_lines))

    # NAS
    if nas:
        nas_lines = [f"{p['type']} {p['facility']}" for p in nas]
        sections.append("NAS delays: " + ", ".join(nas_lines))

    # Route narrative
    if route and route.get("route_narrative"):
        sections.append("Route impact:\n" + route["route_narrative"][:300])

    return "\n\n".join(sections) if sections else "No significant operational items."


def main(force: bool = False) -> None:
    gate_result = "new"
    status = "error"

    try:
        brief_text = build_brief_content()
        status = "ok"
        log.info("%s: brief generated (deterministic)", SKILL_NAME)

        import pathlib
        brief_path = pathlib.Path(config.state_dir()) / "daily-brief.txt"
        brief_path.parent.mkdir(parents=True, exist_ok=True)
        brief_path.write_text(brief_text)

    finally:
        log_usage(SKILL_NAME, MODEL, 0, 0, status, gate_result)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description=f"{SKILL_NAME} skill")
    parser.add_argument("--force", action="store_true", help="(No effect — SR-2 exempt)")
    args = parser.parse_args()
    main(force=args.force)
