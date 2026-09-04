#!/usr/bin/env bash
# scripts/scheduled-integrity-sweep.sh
# Periodic collective signed-manifest check (docs/COMPLIANCE_SECURITY.md
# "Signed Manifest Integrity"), run on a timer as defense in depth beyond
# the entrypoint-level checks (fail2ban actions, build-models.sh,
# scripts/verified-exec.sh wrapping every skill container, llm.py's
# per-call hook). Those catch tampering at the moment something is about
# to run; this catches tampering that landed on disk but hasn't been
# exercised by any of those entrypoints yet (or ever, for files nothing
# currently invokes) -- and it's the one thing that actually re-checks the
# whole tree, not just whatever path happened to run.
#
# Usage:
#   scripts/scheduled-integrity-sweep.sh          # normal run (timer)
#   scripts/scheduled-integrity-sweep.sh --status # last result, no new check
set -uo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
STATE_DIR="/var/lib/corporatetraveldc/integrity-sweep"
LOG_FILE="${STATE_DIR}/sweep.log"
STATE_FILE="${STATE_DIR}/last-result.json"
ENV_FILE="/etc/corporatetraveldc/dispatch.env"
SECRETS_FILE="/etc/corporatetraveldc/dispatch-secrets.env"

mkdir -p "${STATE_DIR}"

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

log() {
    local level="$1"; shift
    local ts; ts=$(date '+%Y-%m-%d %H:%M:%S')
    echo "[${ts}] [${level^^}] $*" >> "${LOG_FILE}" 2>/dev/null
    echo "[${ts}] [${level^^}] $*"
}

ntfy_send() {
    local title="$1" msg="$2" priority="${3:-2}"
    local auth_args=()
    [[ -n "${NTFY_TOKEN}" ]] && auth_args=(-H "Authorization: Bearer ${NTFY_TOKEN}")
    curl -sf --max-time 5 \
        "${auth_args[@]}" \
        -H "Title: ${title}" \
        -H "Priority: ${priority}" \
        -H "Tags: warning" \
        -d "${msg}" \
        "${NTFY_BASE}/${NTFY_OPS}" >/dev/null 2>&1 \
        || log "warn" "ntfy_send failed (token_set=$([[ -n \"${NTFY_TOKEN}\" ]] && echo yes || echo no))"
}

if [[ "${1:-}" == "--status" ]]; then
    if [[ -f "${STATE_FILE}" ]]; then cat "${STATE_FILE}"; else echo '{"status":"never run"}'; fi
    exit 0
fi

output="$("${REPO_DIR}/scripts/verify-manifest.sh" 2>&1)"
rc=$?
now_epoch=$(date +%s)

if [[ ${rc} -eq 0 ]]; then
    log "info" "sweep OK: ${output}"
    printf '{"status":"ok","last_run_epoch":%s,"last_run_iso":"%s"}\n' \
        "${now_epoch}" "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" > "${STATE_FILE}"
else
    log "error" "SWEEP FAILED -- integrity check did not pass:"
    log "error" "${output}"
    printf '{"status":"FAILED","last_run_epoch":%s,"last_run_iso":"%s","detail":%s}\n' \
        "${now_epoch}" "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" \
        "$(printf '%s' "${output}" | python3 -c 'import json,sys;print(json.dumps(sys.stdin.read()))' 2>/dev/null || echo '"(unavailable)"')" \
        > "${STATE_FILE}"
    ntfy_send "INTEGRITY SWEEP FAILED" \
        "$(basename "${REPO_DIR}"): signed-manifest verification failed -- possible tampering or corruption. Check ${LOG_FILE}." \
        5
    exit 1
fi
