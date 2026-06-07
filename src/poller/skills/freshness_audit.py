"""
freshness-audit — SR-1 compliant. SR-2 exempt (time-bounded).

Model: claude-haiku-4-5-20251001
Schedule: 06:00 ET daily (corporatetraveldc-freshness-audit.timer)
SR-1: log_usage() in finally block
SR-2: Not applicable — summarizes a time window; always new inputs.

Audits per-feed freshness against stale thresholds.
Pushes to ntfy topic "ops-health" at priority 2.
"""

import argparse
import logging
import time

import anthropic

from common import config, db
from common.sr1_log import log_usage

log = logging.getLogger(__name__)

SKILL_NAME = "freshness-audit"
MODEL = "claude-haiku-4-5-20251001"

# Stale thresholds in seconds — per SKILL.md feed freshness reference.
STALE_THRESHOLDS = {
    "metar":          900,    # 15 min
    "tfr":            900,
    "nws":            2700,   # 45 min
    "nas":            900,
    "notam":          900,
    "runsheet":       900,
    "atcscc_opsplan": 7200,   # 2 hr — daily cadence, updated hourly
}

SYSTEM_PROMPT = """You are auditing feed freshness for a dispatch platform.
Given a list of feeds with their last fetch time and stale status,
produce a brief health report:
- List any stale feeds with their lag time
- Note any feeds with consecutive failures
- Give an overall health assessment (OK / DEGRADED / CRITICAL)
Keep it under 150 words. Plain text for push notification."""


def build_audit_data() -> list[dict]:
    feeds = db.get_feed_states()
    now = time.time()
    result = []
    for f in feeds:
        threshold = STALE_THRESHOLDS.get(f["feed_name"], 3600)
        age = now - (f["fetched_at"] or 0) if f["fetched_at"] else None
        stale = age is None or age > threshold
        result.append({
            "feed": f["feed_name"],
            "age_seconds": round(age) if age else None,
            "stale": stale,
            "consecutive_failures": f["consecutive_failures"],
            "error": f["error"],
        })
    return result


def main(force: bool = False) -> None:
    gate_result = "new"
    client = anthropic.Anthropic(api_key=config.anthropic_api_key())
    input_tokens = output_tokens = 0
    status = "error"

    try:
        audit_data = build_audit_data()
        stale_count = sum(1 for f in audit_data if f["stale"])
        fail_count = sum(1 for f in audit_data if f["consecutive_failures"] > 0)

        feed_lines = [
            f"  {f['feed']}: age={f['age_seconds']}s stale={f['stale']} "
            f"failures={f['consecutive_failures']}"
            + (f" err={f['error'][:60]}" if f["error"] else "")
            for f in audit_data
        ]

        content = (
            f"Feed health snapshot ({len(audit_data)} feeds, "
            f"{stale_count} stale, {fail_count} with failures):\n"
            + "\n".join(feed_lines)
        )

        response = client.messages.create(
            model=MODEL,
            max_tokens=300,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": content}],
        )
        input_tokens = response.usage.input_tokens
        output_tokens = response.usage.output_tokens
        status = "ok"

        report = response.content[0].text
        log.info("%s: audit complete — %d stale, %d failures, %d+%d tokens",
                 SKILL_NAME, stale_count, fail_count, input_tokens, output_tokens)

        # Write to state file.
        import pathlib
        p = pathlib.Path(config.state_dir()) / "freshness-audit.txt"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(report)

    finally:
        log_usage(SKILL_NAME, MODEL, input_tokens, output_tokens, status, gate_result)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    main(force=args.force)
