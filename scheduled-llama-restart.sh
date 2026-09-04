#!/bin/bash
# scripts/scheduled-llama-restart.sh
# Unconditional daily preventive restart of llama-hot + llama-chat
# (operator directive 2026-08-30, alongside the llama-chat swap-thrashing
# investigation in CLAUDE.md -- restart cadence capped at <=24h so both
# tiers get kicked fresh at least once a day regardless of whether the
# 0.21 tok/s root cause turns out to be memory fragmentation from
# multi-day uptime. This is a preventive mitigation for a still-open
# investigation, not a fix -- see CLAUDE.md's "OPEN, root cause..." entry.
#
# Restarts hot THEN chat, one at a time via blocking `systemctl --user
# restart` calls, deliberately in this order and never in parallel:
# chat.service's own ExecStartPre blocks on hot's /health before loading
# the model, so restarting hot first and waiting for it to report active
# (systemctl restart already blocks until ExecStartPre+ExecStart both
# succeed) means chat's restart lands on an already-healthy hot instead
# of racing it -- the exact thundering-herd pattern from the 2026-08-27
# incident (load1=41, two llama-server processes cold-reading the same
# 2.1GB GGUF at once) this ordering is designed to avoid. report-1/
# report-2 are NOT touched here -- operator directive scoped this to
# hot/chat only; the report tier isn't implicated in the open investigation.
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
    [corporatetraveldc-llama-hot.service]=8093
    [corporatetraveldc-llama-chat.service]=8094
)
SERVICES=(
    corporatetraveldc-llama-hot.service
    corporatetraveldc-llama-chat.service
)
LLAMA_HOST="100.x.x.x"
IDLE_WAIT_MAX_SEC=120

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
    log "warn" "port ${port} still processing (or unreachable) after ${IDLE_WAIT_MAX_SEC}s idle-wait -- restarting anyway"
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

log "info" "-------- daily preventive llama-hot/chat restart starting --------"

FAILED=()
for SERVICE in "${SERVICES[@]}"; do
    if [[ "${MODE}" == "dry-run" ]]; then
        log "info" "[DRY-RUN] would run: systemctl --user restart ${SERVICE}"
        continue
    fi

    port="${SERVICE_PORT[${SERVICE}]:-}"
    if [[ -n "${port}" ]]; then
        log "info" "waiting for ${SERVICE} (port ${port}) to go idle before restart"
        wait_for_idle "${port}"
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

if (( ${#FAILED[@]} == 0 )); then
    ntfy_send "llama-hot/chat: daily preventive restart" \
        "Both tiers restarted and healthy (24h freshness cycle, mitigating the open swap-thrashing investigation -- see CLAUDE.md)." \
        2
    log "info" "-------- daily preventive restart complete: all healthy --------"
else
    ntfy_send "llama-hot/chat: daily preventive restart FAILED" \
        "Failed/unhealthy after restart: ${FAILED[*]} -- check journalctl --user -u <service>." \
        4
    log "error" "-------- daily preventive restart complete: failures: ${FAILED[*]} --------"
    exit 1
fi
