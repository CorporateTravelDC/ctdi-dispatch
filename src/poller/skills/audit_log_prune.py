"""poller.skills.audit_log_prune -- 90-day retention for audit_log.

2026-08-19: added alongside require_admin's new per-action audit writes
(src/auth/auth.py). Before that change, audit_log held only guardrail
(SR-1/SR-2) and Tier-2 CUI-read rows -- 12 rows total in its whole
history, growing slowly enough that unbounded retention was never a
practical problem. Auditing all ~23 admin endpoints (including request
payloads for POST/PUT/PATCH) changes that, so this prune job ships in
the same change rather than as a follow-up gap -- see the
flight_events_cleanup precedent (docs/COMPLIANCE_SECURITY.md /
CLAUDE.md) for what happens when a retention job is written but never
actually wired to run.

No LLM call, so SR-1/SR-2 do not apply (see CLAUDE.md "Skill runtime
rules" -- both are scoped to skills that call an LLM).

90 days matches the retention figure docs/COMPLIANCE_SECURITY.md has
described since before this job existed; kept as the default here so
the doc's claim becomes true instead of being changed to match a
shorter window.
"""
from __future__ import annotations

import logging

from common import db

log = logging.getLogger(__name__)

RETENTION_DAYS = 90


def run() -> None:
    deleted = db.prune_audit_log(days=RETENTION_DAYS)
    if deleted:
        log.info("audit_log_prune: deleted %d row(s) older than %dd", deleted, RETENTION_DAYS)
    else:
        log.debug("audit_log_prune: nothing older than %dd to prune", RETENTION_DAYS)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run()
