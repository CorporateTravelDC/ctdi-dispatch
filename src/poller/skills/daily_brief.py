"""
daily-brief — SR-1 compliant. SR-2 exempt (time-bounded input; always new).

Model: claude-sonnet-4-6
Schedule: 05:00 ET daily (corporatetraveldc-daily-brief.timer)
SR-1: log_usage() in finally block
SR-2: Not applicable — this skill summarizes a time window; inputs are always new.

Produces a morning operational brief covering weather, TFRs, CPS, open items.
Pushes to ntfy topic "ops-brief" at priority 3.
"""

import argparse
import json
import logging
import time

import anthropic

from common import config, db
from common.sr1_log import log_usage

log = logging.getLogger(__name__)

SKILL_NAME = "daily-brief"
MODEL = "claude-sonnet-4-6"

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
    # SR-2 explicitly exempt for daily-brief — time-bounded, inputs always new.
    gate_result = "new"  # Always new for time-bounded brief.

    client = anthropic.Anthropic(api_key=config.anthropic_api_key())
    input_tokens = output_tokens = 0
    status = "error"

    try:
        content = build_brief_content()
        response = client.messages.create(
            model=MODEL,
            max_tokens=600,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": f"Current operational data:\n\n{content}"}],
        )
        input_tokens = response.usage.input_tokens
        output_tokens = response.usage.output_tokens
        status = "ok"

        brief_text = response.content[0].text
        log.info("%s: brief generated, %d+%d tokens", SKILL_NAME, input_tokens, output_tokens)

        # Write to state file for /api/v1/brief endpoint.
        import pathlib
        brief_path = pathlib.Path(config.state_dir()) / "daily-brief.txt"
        brief_path.parent.mkdir(parents=True, exist_ok=True)
        brief_path.write_text(brief_text)

    finally:
        log_usage(SKILL_NAME, MODEL, input_tokens, output_tokens, status, gate_result)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description=f"{SKILL_NAME} skill")
    parser.add_argument("--force", action="store_true", help="(No effect — SR-2 exempt)")
    args = parser.parse_args()
    main(force=args.force)
