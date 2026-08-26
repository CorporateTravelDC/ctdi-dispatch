"""poller.skills.retention_prune -- daily retention sweep for tables that
had no prune path at all (Opus blind review C-33, 2026-08-26).

Same rationale as poller/skills/audit_log_prune.py (see that file's
docstring for the flight_events_cleanup precedent this follows): a
retention window written but never wired to actually run daily is the
failure mode to avoid. nas_programs is deliberately excluded here --
explicitly required to retain long-term, a real design decision, not an
oversight this skill is meant to fix.

No LLM call, so SR-1/SR-2 do not apply.
"""
from __future__ import annotations

import logging

from common import db

log = logging.getLogger(__name__)

_PRUNE_JOBS = (
    ("train_events", db.prune_train_events),
    ("board_messages", db.prune_board_messages),
    ("webhook_events", db.prune_webhook_events),
    ("flight_ooooi_times", db.prune_flight_ooooi_times),
    ("stdds_safety_status_history", db.prune_stdds_safety_status_history),
    ("local_airspace_alerts", db.prune_local_airspace_alerts),
    ("international_aviation_feed", db.prune_international_aviation_feed),
    ("session_grants", db.prune_expired_session_grants),
    ("board_enroll_nonces + board_tokens", db.prune_expired_board_auth),
)


def run() -> None:
    total = 0
    for label, prune_fn in _PRUNE_JOBS:
        try:
            deleted = prune_fn()
        except Exception as e:
            log.error("retention_prune: %s failed: %s", label, e)
            continue
        total += deleted
        if deleted:
            log.info("retention_prune: %s -- deleted %d row(s)", label, deleted)
        else:
            log.debug("retention_prune: %s -- nothing to prune", label)
    log.info("retention_prune: complete, %d row(s) deleted total", total)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run()
