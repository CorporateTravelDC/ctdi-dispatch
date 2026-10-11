"""
common.claude_weekly_fallback -- failure-marker bookkeeping for the two
Claude-CLI-backed weekly jobs (docs-drift-weekly / second-brain-weekly-dump)
and their overnight Llama fallback.

2026-09-17 operator directive: those two jobs stay on Claude CLI as primary
(manual/out-of-cycle Claude use is unrestricted; this is not a routing
change). The ask is a BACKSTOP: if the Claude call fails for any reason
(subscription lapse, outage, rate limit), a degraded Llama-based pass should
pick it up automatically, bounded to a 6-12h worst-case delay, "overnight,
based off load" -- see corporatetraveldc-claude-weekly-llama-fallback.timer/
.service for how that cadence is chosen.

State is a plain JSON file under /var/lib/corporatetraveldc, matching the
existing pattern scripts/scheduled-integrity-sweep.sh already uses for
"job status" (STATE_FILE=.../last-result.json) rather than a new db.py
table -- this is simple, dependency-light, and consistent with every other
bash-invoked job's status-tracking convention on this box, and doesn't
require a schema migration for two boolean-ish flags.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

STATE_DIR = Path("/var/lib/corporatetraveldc/claude-weekly-fallback")

# Marker states, in order of severity:
#   "pending"                -- Claude run failed, Llama fallback owed.
#   "llama-fallback-partial" -- Llama fallback produced best-effort output,
#                                but a real Claude-confirmed run is still
#                                owed (used ONLY by second-brain-weekly-dump,
#                                whose destructive CLAUDE.md/memory reset
#                                must never happen off a Llama-drafted note).
STATE_PENDING = "pending"
STATE_LLAMA_PARTIAL = "llama-fallback-partial"


def _marker_path(job_name: str) -> Path:
    return STATE_DIR / f"{job_name}.json"


def mark_failure(job_name: str, reason: str) -> None:
    """Record that job_name's Claude-CLI run failed and a Llama fallback is
    owed. Idempotent/best-effort -- never raises (a marker write failing
    must not crash the caller's own failure-handling path)."""
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        _marker_path(job_name).write_text(json.dumps({
            "state": STATE_PENDING,
            "failed_at": time.time(),
            "failed_at_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "reason": reason[:2000],
        }))
    except OSError:
        pass


def get_marker(job_name: str) -> dict | None:
    """Current marker for job_name, or None if no fallback is owed."""
    try:
        return json.loads(_marker_path(job_name).read_text())
    except (OSError, ValueError):
        return None


def clear_marker(job_name: str) -> None:
    """A confirmed-successful Claude run clears any owed fallback entirely
    -- the normal/common case (nothing pending most weeks)."""
    try:
        _marker_path(job_name).unlink(missing_ok=True)
    except OSError:
        pass


def set_llama_partial(job_name: str, reason: str) -> None:
    """The Llama fallback produced best-effort output but must NOT clear
    the marker outright for jobs with a destructive confirmed-only step
    (second-brain-weekly-dump's CLAUDE.md/memory wipe) -- downgrade to
    'llama-fallback-partial' so a human/future Claude run still sees a
    real Opus pass is owed, while the fallback runner itself knows not to
    re-attempt Llama again until a NEW failure re-arms it as 'pending'."""
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        _marker_path(job_name).write_text(json.dumps({
            "state": STATE_LLAMA_PARTIAL,
            "updated_at": time.time(),
            "updated_at_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "reason": reason[:2000],
        }))
    except OSError:
        pass
