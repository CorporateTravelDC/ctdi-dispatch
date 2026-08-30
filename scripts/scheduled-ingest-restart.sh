#!/bin/bash
# scripts/scheduled-ingest-restart.sh
# Threshold-triggered, controlled restart of corporatetraveldc-ingest --
# runs as corporatetraveldc via a user systemd timer, checking every 2
# minutes (same cadence as container-mem-watch.timer).
#
# Why threshold-triggered instead of a flat interval (2026-07-19): a live
# check while building this showed ingest sitting at 99.48%% of its 1536m
# cap. Crash-to-crash gaps observed range from ~10 minutes (busy days) to
# ~3 hours (calm days). A fixed-interval restart (e.g. every 90 min) would
# almost never win the race against the OOM-killer on a busy day, and
# would restart needlessly often on a calm one. Checking memory % on a
# short cadence and restarting only when a safety threshold is crossed
# adapts to both cases without guessing at a schedule.
#
# corporatetraveldc-ingest has a confirmed, not-yet-root-caused memory
# leak that runs it up against its cgroup cap and gets it SIGKILL'd by
# the kernel OOM-killer -- 86+ times observed. See container-mem-watch.sh
# for the parallel observation/alerting side of this (it also tracks
# ingest but takes no action -- "observation only -- no restarts"). This
# script is the action side, scoped ONLY to ingest.
#
# A SIGKILL at the ceiling is worse than a controlled restart: it can hit
# mid-message, takes down all six Solace sessions with no warning, and
# systemd has to notice and restart it anyway (Restart=on-failure) after
# the fact. A threshold-triggered `systemctl --user restart` gets a clean
# SIGTERM (main.py already handles SIGTERM/SIGINT) before the ceiling.
#
# This is a mitigation for the SYMPTOM, not a fix for the leak itself.
# Once the leak is root-caused (see the profiling task), this script
# should be reconsidered -- either removed or its threshold relaxed.
#
# Usage:
#   scheduled-ingest-restart.sh          # normal run (called by the timer)
#   scheduled-ingest-restart.sh --dry-run
#   scheduled-ingest-restart.sh --status
#
# ASCII output only -- no Unicode symbols (repo convention).

set -uo pipefail

STATE_DIR="/var/lib/corporatetraveldc/scheduled-ingest-restart"
LOG_FILE="${STATE_DIR}/restart.log"
# One state file per service now (was a single shared state.json) so each
# container's cooldown is tracked independently.
state_file_for() { echo "${STATE_DIR}/state-${1%.service}.json"; }
ENV_FILE="/etc/corporatetraveldc/dispatch.env"

# Updated 2026-07-26 for the ingest container split. This used to hardcode
# a single SERVICE name -- that stopped protecting anything the moment the
# old monolithic corporatetraveldc-ingest.service was disabled in favor of
# seven independent containers (core + one per SWIM feed). Each of the
# seven has its own, much smaller memory cap (256-448m vs. the old 1536m),
# so the original 86+-observed leak's blast radius per-container is
# different and not yet re-characterized -- but until it is, every
# container still deserves the same safety net the monolith had. Checked
# and restarted independently, with its own cooldown, so one leaking
# container getting recycled never touches the other six.
SERVICES=(
    corporatetraveldc-ingest-core.service
    corporatetraveldc-ingest-fdps.service
    corporatetraveldc-ingest-stdds.service
    corporatetraveldc-ingest-tfms.service
    corporatetraveldc-ingest-tbfm.service
    corporatetraveldc-ingest-itws.service
    corporatetraveldc-ingest-notam.service
)

SECRETS_FILE="/etc/corporatetraveldc/dispatch-secrets.env"

mkdir -p "${STATE_DIR}"

# NOTE: deliberately NOT `source`-ing dispatch.env -- it's a podman
# --env-file (simple KEY=VALUE, not bash-parsed), and at least one value
# in it is unquoted with a space (AMTRAK_CORE_ROUTES=Acela,Northeast
# Regional), which a literal bash `source` word-splits and tries to run
# "Regional" as a command. Pull out just the keys we need instead.
read_env_var() {
    local key="$1" file="$2"
    [[ -f "${file}" ]] || return 0
    grep -m1 "^${key}=" "${file}" 2>/dev/null | cut -d'=' -f2-
}

NTFY_BASE="$(read_env_var NTFY_BASE_URL "${ENV_FILE}")"
NTFY_BASE="${NTFY_BASE:-http://127.0.0.1:2586}"
NTFY_OPS="$(read_env_var NTFY_OPS_TOPIC "${ENV_FILE}")"
NTFY_OPS="${NTFY_OPS:-ops-health}"
# The self-hosted ntfy instance runs auth-default-access=deny-all (tiered
# auth lockdown -- Tailscale/CF proximity is never treated as implicit
# trust, every publish needs its own token). NTFY_TOKEN actually lives in
# dispatch-secrets.env, not dispatch.env -- confirmed 2026-07-19 as the
# reason no alerts from this script or container-mem-watch.sh ever landed
# (curl -f + `|| true` swallowed the resulting 403s silently).
NTFY_TOKEN="$(read_env_var NTFY_TOKEN "${SECRETS_FILE}")"
NTFY_TOKEN="${NTFY_TOKEN%%:*}"

# Restart once ingest crosses this %% of its cgroup memory cap.
RESTART_THRESHOLD_PCT="${INGEST_RESTART_THRESHOLD_PCT:-90}"
# Minimum seconds between restarts, so a post-restart reconnect burst
# (queues replaying backlog) can't trigger a restart-loop.
COOLDOWN_SECS="${INGEST_RESTART_COOLDOWN_SECS:-300}"

# 2026-08-27 (operator directive): coordinate with thermal-ingest-guard.py,
# an independent watchdog that also restarts these same ingest containers
# (via ingest-feed-ctl.sh) as part of its own LOCKDOWN shed/restore cycle.
# Without this, a guard-triggered restore's own reconnect/backlog memory
# burst could look identical to this script's genuine-leak signal,
# triggering a second, compounding restart on top of a stack that's
# already mid-recovery -- a real foot-gun, since each restart's own
# startup cost can itself be enough to re-trip the guard's load
# threshold, flapping the two watchdogs against each other. Skip this
# entire run whenever the guard is currently shed (tier > 0) or restored
# within the last GUARD_GRACE_SECS.
THERMAL_GUARD_STATE="/var/lib/corporatetraveldc/thermal_ingest_guard_state.json"
GUARD_GRACE_SECS="${INGEST_RESTART_GUARD_GRACE_SECS:-300}"

MODE="run"
case "${1:-}" in
    --dry-run) MODE="dry-run" ;;
    --status)  MODE="status" ;;
esac

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

mem_pct_now() {
    local service="$1"
    podman stats --no-stream --format '{{.Name}}|{{.MemPerc}}' 2>/dev/null \
        | awk -F'|' -v svc="systemd-${service%.service}" '$1==svc {gsub("%","",$2); print $2}'
}

last_restart_epoch() {
    local state_file="$1"
    [[ -f "${state_file}" ]] || { echo 0; return; }
    grep -o '"last_restart_epoch":[0-9]*' "${state_file}" 2>/dev/null | grep -o '[0-9]*$' || echo 0
}

write_state() {
    local state_file="$1" epoch="$2" pct="$3"
    printf '{"last_restart_epoch":%s,"last_restart_pct":"%s","last_restart_iso":"%s"}\n' \
        "${epoch}" "${pct}" "$(date -d "@${epoch}" '+%Y-%m-%dT%H:%M:%S%z' 2>/dev/null || date '+%Y-%m-%dT%H:%M:%S%z')" \
        > "${state_file}"
}

# See the GUARD_GRACE_SECS comment above for why this exists. Returns
# 0 (true, "back off") if the guard is currently shed (tier > 0) or
# restored the stack within the last GUARD_GRACE_SECS seconds.
guard_recently_active() {
    [[ -f "${THERMAL_GUARD_STATE}" ]] || return 1
    local tier restored_at now
    # Note the space after ":" -- Python's json.dump (thermal-ingest-guard.py's
    # save_state()) uses ": " as its default key-value separator, not ":".
    # A grep pattern without \s* here silently never matches at all, always
    # returning "not active" regardless of real guard state -- confirmed
    # live against thermal_ingest_guard_state.json before shipping this.
    tier=$(grep -oE '"tier": *[0-9]+' "${THERMAL_GUARD_STATE}" 2>/dev/null | grep -oE '[0-9]+$')
    if [[ -n "${tier}" && "${tier}" -gt 0 ]]; then
        return 0
    fi
    restored_at=$(grep -oE '"restored_at": *[0-9.]+' "${THERMAL_GUARD_STATE}" 2>/dev/null | grep -oE '[0-9.]+$')
    [[ -n "${restored_at}" ]] || return 1
    now=$(date +%s)
    # restored_at is a Python time.time() float epoch -- integer-truncate
    # for a plain bash arithmetic comparison, sub-second precision doesn't
    # matter for a 300s grace window.
    restored_at="${restored_at%%.*}"
    (( now - restored_at < GUARD_GRACE_SECS ))
}

if [[ "${MODE}" != "status" ]] && guard_recently_active; then
    log "info" "thermal-ingest-guard is shed or restored within the last ${GUARD_GRACE_SECS}s -- skipping this entire run to avoid a compounding restart on a stack already mid-recovery"
    exit 0
fi

if [[ "${MODE}" == "status" ]]; then
    echo "threshold:          ${RESTART_THRESHOLD_PCT}%"
    echo "cooldown (s):       ${COOLDOWN_SECS}"
    for SERVICE in "${SERVICES[@]}"; do
        state_file="$(state_file_for "${SERVICE}")"
        pct="$(mem_pct_now "${SERVICE}")"
        last_epoch="$(last_restart_epoch "${state_file}")"
        since_last=$(( $(date +%s) - last_epoch ))
        echo "--- ${SERVICE} ---"
        echo "  current mem pct:  ${pct:-unknown (not running)}%"
        echo "  seconds since last restart: ${since_last} (0 = never recorded)"
    done
    exit 0
fi

for SERVICE in "${SERVICES[@]}"; do
    STATE_FILE="$(state_file_for "${SERVICE}")"
    pct="$(mem_pct_now "${SERVICE}")"
    pct_int="${pct%%.*}"
    now_epoch="$(date +%s)"
    last_epoch="$(last_restart_epoch "${STATE_FILE}")"
    since_last=$(( now_epoch - last_epoch ))

    if [[ -z "${pct_int}" ]]; then
        log "warn" "could not read mem%% for ${SERVICE} (container not running / podman stats empty) -- skipping this check"
        continue
    fi

    if (( pct_int < RESTART_THRESHOLD_PCT )); then
        continue
    fi

    if (( last_epoch > 0 && since_last < COOLDOWN_SECS )); then
        log "info" "${SERVICE}: mem at ${pct}%% (>= ${RESTART_THRESHOLD_PCT}%% threshold) but only ${since_last}s since last restart (cooldown ${COOLDOWN_SECS}s) -- skipping to avoid restart-loop"
        continue
    fi

    log "info" "-------- threshold-triggered preventive restart: ${SERVICE} --------"
    log "info" "${SERVICE}: mem at ${pct}%% >= ${RESTART_THRESHOLD_PCT}%% threshold -- restarting (last restart: ${since_last}s ago)"

    if [[ "${MODE}" == "dry-run" ]]; then
        log "info" "[DRY-RUN] would run: systemctl --user restart ${SERVICE}"
        continue
    fi

    if systemctl --user restart "${SERVICE}" 2>>"${LOG_FILE}"; then
        write_state "${STATE_FILE}" "${now_epoch}" "${pct}"
        sleep 5
        if systemctl --user is-active --quiet "${SERVICE}"; then
            log "info" "${SERVICE} active after restart"
            ntfy_send "Ingest: preventive restart" \
                "${SERVICE} hit ${pct}%% of mem cap -- restarted preventively (scheduled maintenance, not an OOM-kill)." \
                2
        else
            log "warn" "${SERVICE} NOT active 5s after restart -- check journalctl --user -u ${SERVICE}"
            ntfy_send "Ingest: preventive restart FAILED" \
                "${SERVICE} did not come back active after threshold restart -- check manually." \
                4
        fi
    else
        log "error" "systemctl --user restart ${SERVICE} failed"
        ntfy_send "Ingest: preventive restart FAILED" \
            "systemctl --user restart ${SERVICE} returned non-zero -- check manually." \
            4
    fi
    log "info" "-------- end: ${SERVICE} --------"
done
