# CLAUDE.md drift report -- moved

Since 2026-10-12 the daily drift backstop (`scripts/claude-md-drift-daily.sh`) writes its report to
`/var/lib/corporatetraveldc/reports/docs/CLAUDE_MD_DRIFT_REPORT.md` on the box, not into the repo
(override: `CTDC_REPORTS_DIR`). The weekly and post-commit live-state checks write
`LIVE_STATE_CHECK_<date>.md` to the same directory. Writing into the tracked tree made every relay
set these files aside, and one relay failed its clean-tree check on them.

Earlier dated `docs/LIVE_STATE_CHECK_*.md` records stay in the repo as history.
