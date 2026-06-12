"""
freshness-audit — SR-1 compliant. SR-2 exempt (time-bounded).

Model: deterministic (feed health metrics; no inference needed)
Schedule: 06:00 ET daily (corporatetraveldc-freshness-audit.timer)
SR-1: log_usage() in finally block
SR-2: Not applicable — summarizes a time window; always new inputs.

Audits per-feed freshness against stale thresholds.
Pushes to ntfy topic "ops-health" at priority 2.
"""

import os
import argparse
import logging
import time

from common import config, db
from common.sr1_log import log_usage

log = logging.getLogger(__name__)

SKILL_NAME = "freshness-audit"
OLLAMA_BASE_URL   = os.getenv("OLLAMA_BASE_URL", "")
OLLAMA_MODEL      = (os.getenv("OLLAMA_OSINT_MODEL")
                     or os.getenv("OLLAMA_CHAT_MODEL")
                     or os.getenv("OLLAMA_MODEL")
                     or "csexec-osint:latest")
MODEL             = OLLAMA_MODEL if OLLAMA_BASE_URL else "deterministic"

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


def _build_audit_report(audit_data: list[dict]) -> str:
    """Generate deterministic freshness audit report."""
    stale_count = sum(1 for f in audit_data if f["stale"])
    fail_count  = sum(1 for f in audit_data if f["consecutive_failures"] > 0)

    if stale_count == 0 and fail_count == 0:
        overall = "OK"
    elif stale_count > len(audit_data) // 2 or fail_count > 2:
        overall = "CRITICAL"
    else:
        overall = "DEGRADED"

    lines = [f"Feed health: {overall} ({len(audit_data)} feeds, "
             f"{stale_count} stale, {fail_count} with failures)"]

    stale_feeds = [f for f in audit_data if f["stale"]]
    if stale_feeds:
        lines.append("Stale feeds:")
        for f in stale_feeds:
            age = f"{f['age_seconds']}s" if f["age_seconds"] else "unknown"
            err = f" [{f['error'][:60]}]" if f["error"] else ""
            lines.append(f"  {f['feed']}: {age} lag, {f['consecutive_failures']} failures{err}")
    else:
        lines.append("All feeds current.")

    return "\n".join(lines)


def main(force: bool = False) -> None:
    gate_result = "new"
    status = "error"

    try:
        audit_data = build_audit_data()
        stale_count = sum(1 for f in audit_data if f["stale"])
        fail_count  = sum(1 for f in audit_data if f["consecutive_failures"] > 0)

        report = _build_audit_report(audit_data)
        status = "ok"
        log.info("%s: audit complete — %d stale, %d failures",
                 SKILL_NAME, stale_count, fail_count)

        import pathlib
        p = pathlib.Path(config.state_dir()) / "freshness-audit.txt"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(report)

    finally:
        log_usage(SKILL_NAME, MODEL, 0, 0, status, gate_result)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    main(force=args.force)