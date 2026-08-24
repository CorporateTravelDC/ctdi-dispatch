"""
SR-1: API usage logger.
Every automated skill calls log_usage() in a finally block. No exceptions.
Log: /var/lib/corporatetraveldc/api-usage.csv
"""

import csv
from datetime import datetime, timezone
from pathlib import Path

USAGE_LOG = Path("/var/lib/corporatetraveldc/api-usage.csv")

_FIELDS = [
    "timestamp", "skill", "model",
    "input_tokens", "output_tokens",
    "cache_read_tokens", "cache_write_tokens",
    "status", "gate_result",
]


def log_usage(
    skill: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    status: str,            # "ok" | "error"
    gate_result: str,       # "new" | "skipped" | "forced"
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> None:
    """Append one row to the API usage log. Called in a finally block — never raises."""
    try:
        USAGE_LOG.parent.mkdir(parents=True, exist_ok=True)
        write_header = not USAGE_LOG.exists() or USAGE_LOG.stat().st_size == 0
        with USAGE_LOG.open("a", newline="") as f:
            writer = csv.writer(f)
            if write_header:
                writer.writerow(_FIELDS)
            writer.writerow([
                datetime.now(timezone.utc).isoformat(),
                skill,
                model,
                input_tokens,
                output_tokens,
                cache_read_tokens,
                cache_write_tokens,
                status,
                gate_result,
            ])
    except Exception:
        # Never let logging kill a skill.
        pass
