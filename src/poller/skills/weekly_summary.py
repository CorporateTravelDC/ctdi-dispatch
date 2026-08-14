"""
weekly-summary — SR-1 compliant. SR-2 exempt (time-bounded weekly window).

Model: corporatetraveldc-pi5-brief:latest (mistral-nemo 12B via Ollama); deterministic fallback.
Schedule: Sunday 18:00 ET (corporatetraveldc-weekly-summary.timer)
SR-1: log_usage() in finally block
SR-2: Not applicable — summarizes the past week; inputs always new.

Produces a weekly operational summary pushed to ntfy topic "ops-brief" at priority 3.
"""

import os
import argparse
import logging
import time
import httpx

from common import config, db, ntfy_push as _ntfy
from common.llm import generate as llm_generate
from common.sr1_log import log_usage

log = logging.getLogger(__name__)

SKILL_NAME = "weekly-summary"
OLLAMA_BASE_URL   = os.getenv("OLLAMA_BASE_URL", "")
OLLAMA_MODEL      = (os.getenv("OLLAMA_WEEKLY_SUMMARY_MODEL")
                     or os.getenv("OLLAMA_MODEL")
                     or "corporatetraveldc-pi5-brief:latest")
MODEL             = OLLAMA_MODEL if OLLAMA_BASE_URL else "deterministic"
# Weekly content ~600-800 tokens; mistral-nemo Pi 5 CPU ~200s — 600s gives headroom
OLLAMA_TIMEOUT    = 900  # stopgap

SYSTEM_PROMPT = """You are producing a weekly operational summary for an executive chauffeur
operation in the Washington DC metropolitan area.

Summarize the past week covering:
1. **VIP/POTUS activity** — TFR patterns observed
2. **Weather events** — any significant weather that affected operations
3. **NAS delays** — airport delay programs and their operational impact
4. **CPS trend** — how the Critical Predictability State trended this week
5. **Operational notes** — patterns worth tracking going into next week
6. **Disruption pattern (30-day rolling)** — facility/volume- vs.
   weather-driven airports, highest-delay train routes; preserve the
   flight side's real weather/facility split vs. the train side's
   regional-proxy-only weather context, don't blur the two.

Keep it under 500 words. Plain text for push notification.
Be analytical — note patterns, not just events.

NOTE: the real baked-in system prompt for this skill's Ollama model lives
in corporatetraveldc.weekly-summary (repo root) -- this constant is
vestigial documentation only (see _call_ollama's system=None), kept in
sync with that Modelfile by hand."""


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

    # Disruption / weather-vs-facility pattern -- 2026-08-09/10 catch-up
    # session work, same 30-day analyze_*() functions the new
    # disruption_weather_digest.py daily skill uses. Called directly here
    # (matching this file's existing style of direct db queries) rather
    # than reading that skill's vault output, so weekly-summary has no
    # dependency on the daily digest having already run.
    disruption = db.analyze_disruption_weather_split(days=30)
    train_disruption = db.analyze_train_disruption_summary(days=30)
    top_facilities = disruption["facility_breakdown"][:8]
    if top_facilities:
        sections.append(
            "30-day disruption (flights, real FAA/SWIM weather-vs-facility split):\n"
            + "\n".join(
                f"- {x['facility']}: {x['total_programs']} programs, {x['pct_weather']}% weather-driven"
                for x in top_facilities
            )
        )
    top_trains = train_disruption["delay_summary"][:6]
    if top_trains:
        wx_ctx = train_disruption["regional_weather_context"]
        sections.append(
            "30-day train delay rate (regional weather proxy only, NOT a per-train "
            f"cause attribution -- {wx_ctx['wx_flagged_days']}/{wx_ctx['window_days']} days "
            "regionally weather-flagged near the NEC):\n"
            + "\n".join(
                f"- Train {x['train_number']} ({x['route_name']}): "
                f"{x['pct_over_threshold']}% of {x['samples']} obs delayed, avg {x['avg_delay_minutes']}min"
                for x in top_trains
            )
        )

    return "\n\n".join(sections)


def _call_ollama(content: str) -> str | None:
    """Call LLM (Ollama-first, Anthropic fallback) for weekly narrative.
    Returns narrative text or None (caller falls back to deterministic).
    """
    return llm_generate(
        system=None,  # dedicated Modelfile carries this now
        prompt=content,
        ollama_model=OLLAMA_MODEL,
        max_tokens=400,
        temperature=0.3,
        # 2026-08-12: belt-and-suspenders close of the Anthropic fallback --
        # see dispatch.env's ANTHROPIC_FALLBACK_ENABLED comment for the full
        # rationale.
        allow_anthropic=False,
    )


def main(force: bool = False) -> None:
    gate_result = "new"
    status = "error"

    try:
        raw_content = build_weekly_content()

        ollama_result = _call_ollama(raw_content)
        if ollama_result:
            summary = ollama_result
            status = "ok"
            log.info("%s: narrative generated via Ollama/%s", SKILL_NAME, OLLAMA_MODEL)
        else:
            # 2026-08-06: narrow safety net around the fallback ITSELF --
            # same pattern applied identically across every skill with an
            # Ollama fallback. See route_impact.py for the full note.
            try:
                summary = raw_content
                status = "fallback"
                log.info("%s: Ollama unavailable — using deterministic content", SKILL_NAME)
            except Exception as fallback_err:
                log.error("%s: fallback also failed — %s", SKILL_NAME, fallback_err)
                summary = (
                    f"[{SKILL_NAME.upper()}] Generation failed -- both Ollama and the "
                    f"deterministic fallback errored. See logs."
                )
                status = "fallback_error"

        import pathlib
        p = pathlib.Path(config.state_dir()) / "weekly-summary.txt"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(summary)

        # Archive to DB so BriefView can show weekly tab with history
        db.archive_brief(summary, brief_type="weekly", source="skill")

        title = f"Weekly Ops Summary{' [FALLBACK]' if status == 'fallback' else ''}"
        # Use same topics as ops_brief so subscribers don't need a separate topic
        _ntfy.send_dual(summary, summary[:280], title=title)
        log.info("%s: pushed to ops-brief", SKILL_NAME)

    finally:
        log_usage(SKILL_NAME, MODEL, 0, 0, status, gate_result)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    main(force=args.force)
