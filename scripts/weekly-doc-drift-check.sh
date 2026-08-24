#!/bin/bash
# weekly-doc-drift-check.sh -- time-triggered (not commit-triggered) sibling
# of post-commit-doc-verify.sh. Runs every Monday regardless of whether
# anything was committed that week, covering BOTH repos with full live-
# system access (systemctl/podman/curl/SELinux) since this runs on the box
# itself, not a cloud sandbox.
set -uo pipefail

DATE_TAG="$(date +%Y-%m-%d)"
LOG_DIR="/var/lib/corporatetraveldc/docs-drift-check"
mkdir -p "${LOG_DIR}"

run_check() {
    local repo_dir="$1" repo_label="$2"
    local log="${LOG_DIR}/${repo_label}-${DATE_TAG}.log"
    cd "${repo_dir}" || return 1
    local prompt="Weekly documentation drift check for ${repo_label}
(${repo_dir}), part of the CorporateTravelDC dispatch platform (Raspberry
Pi 5). Find the most recent docs/LIVE_STATE_CHECK_*.md, docs/DOCS_REFRESH_*.md,
or docs/DOCS_DRIFT_CHECK_*.md file. Check whether README.md/CLAUDE.md/docs/
still match the ACTUAL current live system -- systemctl --user status of
relevant units, podman ps, real file contents, curl to local services where
applicable, not just what old docs claim. Only check things that plausibly
changed since the last such file's date (use git log to see what actually
changed in the meantime) rather than re-verifying everything from scratch
every week. Write findings to a new file docs/LIVE_STATE_CHECK_${DATE_TAG}.md
-- if nothing drifted, say so briefly, don't pad it. DO NOT commit, stage,
or push anything -- hard rule, no exceptions. Do not touch any other git
branch or run git checkout/reset/stash."
    nohup claude --model fable -p "${prompt}" --permission-mode acceptEdits \
        --allowedTools "Bash,Read,Write,Edit,Glob,Grep" \
        >> "${log}" 2>&1
}

run_check /opt/corporatetraveldc/private/ctdi-dispatch-internal ctdi-dispatch-internal
# dispatch-mcp run_check removed 2026-08-17: repo archived (GitHub repo
# archived read-only, local dir renamed dispatch-mcp.archived-20260817) --
# MCP is fully retired from this platform, nothing left to drift-check.
