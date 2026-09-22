"""poller.skills.audit_log_prune -- RETIRED 2026-09-22. Does not prune.

Superseded by poller/skills/audit_log_archive.py. This file is kept as a
refusing stub rather than deleted, deliberately: the schedule entry that
used to point here lived in poller/main.py for a month, and the name
still appears in src/auth/auth.py's comments,
scripts/migrate-sqlite-to-pg.py's retention map, and the compliance docs.
Deleting the module would turn a re-added schedule entry into a "skill
script not found" warning that reads like a packaging bug; a stub that
says WHY makes the removal self-explaining at the one moment anyone
looks at it.

WHAT CHANGED
The old behaviour was a bare `DELETE FROM audit_log WHERE event_time <
cutoff` at 90 days. The operator retention decision (2026-09-21) is that
audit rows are never destroyed: past the horizon they are checkpointed,
signed with the agent key, uploaded off-box, and reduced on-device to a
signed stub carrying the actors, privilege tiers, window, row count, and
the digest proving the exported detail authentic. That keeps the audit
question answerable after the rows leave the disk, which a plain DELETE
does not.

WHY THIS MUST NEVER RUN ALONGSIDE THE ARCHIVE
Both jobs select on the same cutoff. If the prune won the race, rows
would be deleted before the archive could sign them -- destroying exactly
the evidence the archive exists to preserve, silently, with both jobs
reporting success. That is the failure this stub exists to prevent.

To change the retention horizon set AUDIT_ARCHIVE_RETENTION_DAYS in
dispatch.env. Do not re-enable this skill.

No LLM call, so SR-1/SR-2 do not apply.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

SKILL_NAME = "audit-log-prune"


def run() -> None:
    log.warning(
        "%s is RETIRED and did nothing. Audit retention is handled by "
        "poller/skills/audit_log_archive.py, which signs rows and moves them "
        "off-box instead of deleting them. If you are seeing this, something "
        "re-added the skill to SKILL_SCHEDULE in poller/main.py -- remove "
        "that entry rather than restoring the old prune.", SKILL_NAME)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run()
