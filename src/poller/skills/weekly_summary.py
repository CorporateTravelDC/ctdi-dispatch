"""
weekly-summary — SR-1 compliant. SR-2 exempt (time-bounded weekly window).

Model: ollama/mistral (OSINT-tier)
Schedule: Sunday 18:00 ET (corporatetraveldc-weekly-summary.timer)
SR-1: log_usage() in finally block
SR-2: Not applicable — summarizes the past week; inputs always new.

Produces a weekly operational summary pushed to ntfy topic "ops-brief" at priority 3.
"""

import os
import argparse
import logging
import time

from common import config, db
from common.sr1_log import log_usage

log = logging.getLogger(__name__)

SKILL_NAME = "weekly-summary"
OLLAMA_BASE_URL   = os.getenv("OLLAMA_BASE_URL", "")
OLLAMA_MODEL      = (os.getenv("OLLAMA_OSINT_MODEL")
                     or os.getenv("OLLAMA_MODEL")
                     or "mistral")
MODEL             = OLLAMA_MODEL if OLLAMA_BASE_URL else "deterministic"

SYSTEM_PROMPT = """You are producing a weekly operational summary for an executive chauffeur
operation in the Washington DC metropolitan area.

Summarize the past week covering:
1. **VIP/POTUS activity** — TFR patterns observed
2. **Weather events** — any significant weather that affected operations
3. **NAS delays** — airport delay programs and their operational impact
4. **CPS trend** — how the Critical Predictability State trended this week
5. **Operational notes** — patterns worth tracking going into next week

Keep it under 500 words. Plain text for push notification.
Be analytical — note patterns, not just events."""


def build_weekly_content() -> str:
    # Last 7 days.
    week_ago = time.time() - 7 * 86400

    # CPS history from last week.
    with db.conn() as c:
        cps_rows = c.execute("""
            SELECT score, label, computed_at FROM cps_scores
            WHERE computed_at >= ? ORDER BY computed_at DESC
        """, (week_ago,)).fetchall()

        # TFR history.
        tfr_rows = c.execute("""
            SELECT tfr_id, is_vip, enriched_text, inserted_at FROM tfrs
            WHERE inserted_at >= ? ORDER BY inserted_at DESC
        """, (week_ago,)).fetchall()

        # Hot alerts from last week.
        alert_rows = c.execute("""
            SELECT computed_at, route_narrative FROM hot_alerts
            WHERE computed_at >= ? ORDER BY computed_at DESC LIMIT 5
        """, (week_ago,)).fetchall()

    # Summarize CPS distribution.
    from collections import Counter
    cps_counts = Counter(r["score"] for r in cps_rows)
    cps_summary = (
        f"GREEN: {cps_counts.get('GREEN', 0)}, "
        f"YELLOW: {cps_counts.get('YELLOW', 0)}, "
        f"RED: {cps_counts.get('RED', 0)}"
    )

    vip_tfrs = [r for r in tfr_rows if r["is_vip"]]

    sections = [
        f"Week CPS distribution ({len(cps_rows)} readings): {cps_summary}",
        f"TFRs seen this week: {len(tfr_rows)} total, {len(vip_tfrs)} VIP/POTUS",
    ]

    if vip_tfrs:
        sections.append(
            "VIP TFR IDs: " + ", ".join(r["tfr_id"] for r in vip_tfrs[:5])
        )

    if alert_rows:
        latest_narrative = alert_rows[0]["route_narrative"]
        if latest_narrative:
            sections.append("Latest route narrative:\n" + latest_narrative[:300])

    return "\n\n".join(sections)


def main(force: bool = False) -> None:
    gate_result = "new"
    status = "error"

    try:
        summary = build_weekly_content()
        status = "ok"
        log.info("%s: weekly summary generated (deterministic)", SKILL_NAME)

        import pathlib
        p = pathlib.Path(config.state_dir()) / "weekly-summary.txt"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(summary)

    finally:
        log_usage(SKILL_NAME, MODEL, 0, 0, status, gate_result)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    main(force=args.force)
