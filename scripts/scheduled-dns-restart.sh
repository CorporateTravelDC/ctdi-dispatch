#!/bin/bash
# scripts/scheduled-dns-restart.sh
# Daily preventive restart of pihole-FTL.service + unbound.service -- sibling
# to scripts/scheduled-llama-restart.sh's 24h freshness cycle, added
# 2026-09-17 after ~7.5 days of uptime left both services swapped out
# (pihole-FTL swap peak 27.7M, unbound swap peak 40.7M) and DNS lookups
# measurably sluggish (a cold `dig` took 1.94s vs 0.02s once warm).
#
# Unlike the llama restart, these are stock system units (not this user's
# podman containers), so restarting them needs `sudo systemctl restart
# <unit>` -- requires a NOPASSWD sudoers grant for exactly these two
# commands (see docs/COMPLIANCE_SECURITY.md or ask the operator to add one
# mirroring the existing ollama.service grant in `sudo -l`). Until that
# grant exists, this script's restarts will fail with "a password is
# required" and it will log+notify that clearly rather than hang.
#
# No idle-wait needed here (unlike llama): pihole-FTL/unbound are
# stateless per-query and restart in well under a second, so a restart is
# safe at any time of day. Scheduled overnight anyway for consistency with
# the rest of the preventive-restart grid and to keep the one-time query
# hiccup as unnoticed as possible.
#
# Also logs pre-restart memory/swap for both processes and the host overall
# on every run -- closing a real gap found while investigating this: there
# was no host-level swap history anywhere on the box (container-mem-watch
# only tracks podman containers), so "when did this actually start
# degrading" couldn't be answered empirically from logs. Going forward, this
# log is that history.
#
# Usage:
#   scheduled-dns-restart.sh          # normal run (called by the timer)
#   scheduled-dns-restart.sh --dry-run

set -uo pipefail

STATE_DIR="/var/lib/corporatetraveldc/scheduled-dns-restart"
LOG_FILE="${STATE_DIR}/restart.log"
ENV_FILE="/etc/corporatetraveldc/dispatch.env"
SECRETS_FILE="/etc/corporatetraveldc/dispatch-secrets.env"

SERVICES=(pihole-FTL.service unbound.service)

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

log_mem_snapshot() {
    log "info" "pre-restart memory snapshot:"
    log "info" "  host: $(free -h | awk '/^Mem:/{printf "mem used=%s free=%s avail=%s", $3,$4,$7} /^Swap:/{printf " | swap used=%s", $3}')"
    for svc in "${SERVICES[@]}"; do
        local pid rss swap
        pid="$(systemctl show -p MainPID --value "${svc}" 2>/dev/null)"
        if [[ -n "${pid}" && "${pid}" != "0" && -r "/proc/${pid}/status" ]]; then
            rss="$(awk '/VmRSS/{print $2, $3}' "/proc/${pid}/status" 2>/dev/null)"
            swap="$(awk '/VmSwap/{print $2, $3}' "/proc/${pid}/status" 2>/dev/null)"
            log "info" "  ${svc} (pid ${pid}): RSS=${rss:-unknown} VmSwap=${swap:-unknown}"
        else
            log "warn" "  ${svc}: could not read /proc/<pid>/status (pid=${pid:-unknown})"
        fi
    done
}

log "info" "-------- daily preventive DNS restart starting --------"
log_mem_snapshot

if [[ "${MODE}" == "dry-run" ]]; then
    for SERVICE in "${SERVICES[@]}"; do
        log "info" "[DRY-RUN] would run: sudo systemctl restart ${SERVICE}"
    done
    log "info" "-------- dry-run complete --------"
    exit 0
fi

FAILED=()
for SERVICE in "${SERVICES[@]}"; do
    log "info" "restarting ${SERVICE}"
    if sudo -n systemctl restart "${SERVICE}" 2>>"${LOG_FILE}"; then
        if systemctl is-active --quiet "${SERVICE}"; then
            log "info" "${SERVICE} active after restart"
        else
            log "warn" "${SERVICE} NOT active immediately after restart -- check journalctl -u ${SERVICE}"
            FAILED+=("${SERVICE}")
        fi
    else
        log "error" "sudo systemctl restart ${SERVICE} failed -- likely missing the NOPASSWD sudoers grant this script needs (see header comment); nothing was restarted"
        FAILED+=("${SERVICE}")
    fi
done

if (( ${#FAILED[@]} == 0 )); then
    ntfy_send "dns: daily preventive restart" \
        "pihole-FTL + unbound restarted and healthy (24h freshness cycle)." \
        2
    log "info" "-------- daily preventive DNS restart complete: healthy --------"
else
    ntfy_send "dns: daily preventive restart FAILED" \
        "Failed/unhealthy after restart: ${FAILED[*]} -- likely missing sudoers NOPASSWD grant, check ${LOG_FILE}." \
        4
    log "error" "-------- daily preventive DNS restart complete: failures: ${FAILED[*]} --------"
    exit 1
fi
