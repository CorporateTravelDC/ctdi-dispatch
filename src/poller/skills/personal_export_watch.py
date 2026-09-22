"""
personal_export_watch -- periodic (weekly) check for new personal data
exports dropped into EXPORT_DROP_DIR. 2026-08-07 operator directive.

LinkedIn is the only source wired in today; Uber/Lyft plug into
common.export_analysis's same shared pipeline when those parsers are
built -- this skill just calls whichever per-source process_* functions
exist, it isn't LinkedIn-specific itself.

Weekly, not real-time -- matches the actual pace personal exports get
generated (you request them manually), no reason to check more often.

SR-1: log_usage() in finally block.
SR-2: Exempt -- non-fatal by construction, idempotent (re-processing an
unchanged export just re-derives the same digest).
"""
import logging

from common import export_analysis
from common.sr1_log import log_usage

log = logging.getLogger(__name__)

SKILL_NAME = "personal-export-watch"


def main() -> None:
    status = "ok"
    written = []
    try:
        written = export_analysis.process_linkedin_export()
        if written:
            log.info("%s: wrote %d digest note(s): %s", SKILL_NAME, len(written), written)
        else:
            log.info("%s: no new export content to process this run", SKILL_NAME)
    except Exception as e:
        log.error("%s: run failed: %s", SKILL_NAME, e)
        status = "error"
    finally:
        log_usage(SKILL_NAME, "deterministic", 0, 0, status, "new")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
