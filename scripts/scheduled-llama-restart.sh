#!/bin/bash
# scripts/scheduled-llama-restart.sh
# Daily preventive restart of corporatetraveldc-llama.service -- the ONE
# llama-server (2026-09-06: replaces the hot/chat/report tiers; see that
# unit's header for the operator directive). Restart cadence capped at
# <=24h so the resident model gets kicked fresh once a day (originally
# 2026-08-30, mitigating the multi-day-uptime slowdown investigation).
#
# 2026-09-06 rule, non-negotiable: "NOTHING running kills another ongoing
# run only you or I." This script therefore NEVER restarts a server that
# is mid-generation. It waits for both slots to go idle (up to
# IDLE_WAIT_MAX_SEC) and, if they don't, it SKIPS the restart, logs it and
# notifies -- the next day's timer tries again. The old "restarting
# anyway" branch is gone.
#
# Usage:
#   scheduled-llama-restart.sh          # normal run (called by the timer)
#   scheduled-llama-restart.sh --dry-run

set -uo pipefail

STATE_DIR="/var/lib/corporatetraveldc/scheduled-llama-restart"
LOG_FILE="${STATE_DIR}/restart.log"
ENV_FILE="/etc/corporatetraveldc/dispatch.env"
SECRETS_FILE="/etc/corporatetraveldc/dispatch-secrets.env"

# Port -> service, so we can idle-wait each one before restarting it.
declare -A SERVICE_PORT=(
    [corporatetraveldc-llama.service]=8093
)
SERVICES=(
    corporatetraveldc-llama.service
)
LLAMA_HOST="100.x.x.x"
# 03:00 ET sits between the overnight report slots; a long runner can
# legitimately still be in flight. Wait up to 45 min for idle, then skip.
IDLE_WAIT_MAX_SEC=2700

mkdir -p "${STATE_DIR}"

# 2026-08-30 (H1 fix): this timer's slot is NOT actually quiet -- it
# collides to the second with aam-daily-watch every night (see the
# timer's own Description), and more generally there is no gap on this
# box's schedule where SOME llama skill isn't running. A mid-skill
# restart previously killed gig-economy-daily-watch's in-flight call at
# 08:08:55 ET (one second before chat came back). Wait (bounded) for the
# target port to report idle before restarting it, instead of assuming
# the clock slot is safe.
wait_for_idle() {
    local port="$1" waited=0
    while (( waited < IDLE_WAIT_MAX_SEC )); do
        local slots
        slots="$(curl -sf --max-time 3 "http://${LLAMA_HOST}:${port}/slots" 2>/dev/null)"
        if [[ -n "${slots}" ]] && ! grep -q '"is_processing":true' <<<"${slots}"; then
            return 0
        fi
        sleep 3
        (( waited += 3 ))
    done
    log "warn" "port ${port} still processing (or unreachable) after ${IDLE_WAIT_MAX_SEC}s idle-wait -- SKIPPING restart (never kill an in-flight run)"
    return 1
}

# See scheduled-ingest-restart.sh's identical comment -- dispatch.env is a
# podman --env-file (simple KEY=VALUE), not bash-source-safe.
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

MODE="run"
[[ "${1:-}" == "--dry-run" ]] && MODE="dry-run"

log() {
    local level="$1"; shift
    local ts
    ts=$(date '+%Y-%m-%d %H:%M:%S')
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
        -H "Tags: recycle" \
        -d "${msg}" \
        "${NTFY_BASE}/${NTFY_OPS}" >/dev/null 2>&1 || log "warn" "ntfy_send failed (token_set=$([[ -n \"${NTFY_TOKEN}\" ]] && echo yes || echo no))"
}

log "info" "-------- daily preventive llama restart starting --------"

FAILED=()
SKIPPED=()
for SERVICE in "${SERVICES[@]}"; do
    if [[ "${MODE}" == "dry-run" ]]; then
        log "info" "[DRY-RUN] would run: systemctl --user restart ${SERVICE}"
        continue
    fi

    port="${SERVICE_PORT[${SERVICE}]:-}"
    if [[ -n "${port}" ]]; then
        log "info" "waiting for ${SERVICE} (port ${port}) to go idle before restart"
        if ! wait_for_idle "${port}"; then
            SKIPPED+=("${SERVICE}")
            continue
        fi
    fi

    log "info" "restarting ${SERVICE}"
    if systemctl --user restart "${SERVICE}" 2>>"${LOG_FILE}"; then
        if systemctl --user is-active --quiet "${SERVICE}"; then
            log "info" "${SERVICE} active after restart"
        else
            log "warn" "${SERVICE} NOT active immediately after restart -- check journalctl --user -u ${SERVICE}"
            FAILED+=("${SERVICE}")
        fi
    else
        log "error" "systemctl --user restart ${SERVICE} failed (likely ExecStartPre health-wait timeout upstream)"
        FAILED+=("${SERVICE}")
    fi
done

if [[ "${MODE}" == "dry-run" ]]; then
    log "info" "-------- dry-run complete --------"
    exit 0
fi

if (( ${#SKIPPED[@]} > 0 )); then
    ntfy_send "llama: daily preventive restart SKIPPED" \
        "A generation was still in flight after ${IDLE_WAIT_MAX_SEC}s -- not restarted (rule: nothing kills an ongoing run). Next attempt: tomorrow's timer." \
        3
    log "warn" "-------- daily preventive restart complete: skipped ${SKIPPED[*]} (in-flight run) --------"
    exit 0
fi
if (( ${#FAILED[@]} == 0 )); then
    ntfy_send "llama: daily preventive restart" \
        "corporatetraveldc-llama.service restarted and healthy (24h freshness cycle)." \
        2
    log "info" "-------- daily preventive restart complete: healthy --------"
else
    ntfy_send "llama: daily preventive restart FAILED" \
        "Failed/unhealthy after restart: ${FAILED[*]} -- check journalctl --user -u <service>." \
        4
    log "error" "-------- daily preventive restart complete: failures: ${FAILED[*]} --------"
    exit 1
fi
