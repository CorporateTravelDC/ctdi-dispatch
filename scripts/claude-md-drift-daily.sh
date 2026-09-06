#!/usr/bin/env bash
# scripts/claude-md-drift-daily.sh
# Daily backstop for scripts/check-claude-md-drift.sh -- catches drift the
# commit-boundary gate in sign-manifest.sh can't see: changes made outside
# a signing pass (live deploys, hand-edited units, a retired service nobody
# documented). Runs the full checker (no --pre-sign, so check 8 -- manifest
# vs signature -- runs here too) and turns its output into an edit list, not
# prose: this repo's existing drift reports are readable narrative and were
# never actioned, and format is part of why.
#
# Deliberately does not fail the systemd unit just because drift was found --
# "drift exists" is this tool's normal, expected finding, not an execution
# fault. Failing the unit on every day CLAUDE.md has caught-up-to-do would
# make check-claude-md-drift.sh's own failed-unit check (section 5) flag
# THIS unit as unexplained drift the next day, which is a pointless loop.
# The unit only fails if the checker itself couldn't run at all.
set -uo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "${REPO_DIR}"

CHECKER="scripts/check-claude-md-drift.sh"
REPORT="docs/CLAUDE_MD_DRIFT_REPORT.md"
ENV_FILE="/etc/corporatetraveldc/dispatch.env"
SECRETS_FILE="/etc/corporatetraveldc/dispatch-secrets.env"

read_env_var() {
    local key="$1" file="$2"
    [[ -f "${file}" ]] || return 0
    grep -m1 "^${key}=" "${file}" 2>/dev/null | cut -d'=' -f2-
}

NTFY_BASE="$(read_env_var NTFY_BASE_URL "${ENV_FILE}")"
NTFY_BASE="${NTFY_BASE:-http://127.0.0.1:2586}"
NTFY_OPS="$(read_env_var NTFY_OPS_TOPIC "${ENV_FILE}")"
NTFY_OPS="${NTFY_OPS:-ops-health}"
NTFY_TOKEN="$(read_env_var NTFY_TOKEN "${SECRETS_FILE}")"
NTFY_TOKEN="${NTFY_TOKEN%%:*}"

ntfy_send() {
    local title="$1" msg="$2" priority="${3:-3}"
    local auth_args=()
    [[ -n "${NTFY_TOKEN}" ]] && auth_args=(-H "Authorization: Bearer ${NTFY_TOKEN}")
    curl -sf --max-time 5 \
        "${auth_args[@]}" \
        -H "Title: ${title}" \
        -H "Priority: ${priority}" \
        -H "Tags: memo" \
        -d "${msg}" \
        "${NTFY_BASE}/${NTFY_OPS}" >/dev/null 2>&1
}

if [[ ! -x "${CHECKER}" ]]; then
    echo "XX ${CHECKER} missing or not executable -- cannot run daily drift check" >&2
    exit 1
fi

NOW_ISO="$(date '+%Y-%m-%d %H:%M:%S %Z')"
OUTPUT="$("${CHECKER}" 2>&1)"
RC=$?

{
    echo "# CLAUDE.md drift report"
    echo
    echo "Generated ${NOW_ISO} by corporatetraveldc-claude-md-drift-daily."
    echo "Edit list from ${CHECKER} -- one line per finding, file:line where the"
    echo "checker has it. Not prose; don't add narrative here."
    echo
    if [[ ${RC} -eq 0 ]]; then
        echo "No drift found."
    else
        echo "${OUTPUT}" | grep '^\[DRIFT\]' | sed 's/^\[DRIFT\] /- /'
    fi
} > "${REPORT}"

if [[ ${RC} -ne 0 ]]; then
    N=$(echo "${OUTPUT}" | grep -c '^\[DRIFT\]')
    ntfy_send "CLAUDE.md drift found" \
        "${N} finding(s) in ${REPO_DIR} -- see ${REPORT}" \
        3
fi

exit 0
