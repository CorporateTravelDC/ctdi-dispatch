#!/bin/bash
# scripts/runner-health-watchdog.sh
#
# Watches corporatetraveldc-runner.service (the dispatch-runner PWA backend
# -- everything the PWA frontend depends on, including the knowledge-graph
# proxy, wx-config, and the CPS/live-state SSE stream, rides through this
# one process) and alerts loudly + attempts self-heal if it's not active.
#
# Added 2026-09-05 after a live incident: the unit was cleanly stopped
# (an explicit `systemctl stop`, not a crash) and sat dead for over a day
# with nothing noticing. corporatetraveldc-runner.container already has
# Restart=always -- but Restart= only fires on an unexpected process exit,
# never on an explicit stop, by systemd design (otherwise you could never
# stop a service normally). So Restart=always was never going to catch
# this class of outage; a periodic external check is a genuinely
# different, necessary layer, not a duplicate of what's already there.
#
# 2026-09-05, same day (real finding, caught by an automated doc-drift
# pass on this very commit before the LOCKDOWN-unaware version ever hit
# a real LOCKDOWN): the first version of this script unconditionally
# restarted a not-active runner -- but a not-active runner is a DESIGNED
# state during a thermal-ingest-guard LOCKDOWN (runner is one of the
# units `_lockdown_stop_stack()` deliberately sheds at temp>=79C or
# load1>=40, see thermal-ingest-guard.py's LOCKDOWN_USER_UNITS). Left
# alone, this watchdog would have restarted runner mid-emergency,
# defeating that shed and paging a misleading "auto-restarted" success
# notice for what was actually a defeated safety action. This is
# precisely the "watchdog-vs-LOCKDOWN collision" class already
# root-caused in August for the root-scope scripts/watchdog.sh -- ported
# that script's exact fail-open _guard_tier() mitigation here rather
# than inventing a new one. See _guard_tier()'s own docstring below.
#
# Runs as corporatetraveldc via a user systemd timer every 5 minutes
# (matches the cadence of the other watchdogs in this directory).
#
# Usage:
#   runner-health-watchdog.sh            # normal run (called by the timer)
#   runner-health-watchdog.sh --status   # print current state, no alerting/restart
#
# ASCII output only -- no Unicode symbols (repo convention).

set -uo pipefail

STATE_DIR="/var/lib/corporatetraveldc/runner-health-watch"
STATE_FILE="${STATE_DIR}/state"
LOG_FILE="${STATE_DIR}/watch.log"
LOCK_FILE="/run/user/$(id -u)/runner-health-watchdog.lock"
ENV_FILE="/etc/corporatetraveldc/dispatch.env"
SECRETS_FILE="/etc/corporatetraveldc/dispatch-secrets.env"

UNIT="corporatetraveldc-runner.service"
RESTART_SETTLE_SECS=6
ALERT_COOLDOWN_SECS=1800   # 30 min -- don't re-page every 5-min cycle if a restart keeps failing

# Same state file thermal-ingest-guard.py itself writes -- see that
# script's own STATE_FILE constant.
GUARD_STATE_FILE="/var/lib/corporatetraveldc/thermal_ingest_guard_state.json"

mkdir -p "${STATE_DIR}"

log() {
    local level="$1"; shift
    local ts
    ts=$(date '+%Y-%m-%d %H:%M:%S')
    echo "[${ts}] [${level^^}] $*" >> "${LOG_FILE}" 2>/dev/null
    echo "[${ts}] [${level^^}] $*"
}

read_env_var() {
    local key="$1" file="$2"
    [[ -f "${file}" ]] || return 0
    grep -m1 "^${key}=" "${file}" 2>/dev/null | cut -d'=' -f2- | sed -E "s/^'(.*)'\$/\1/; s/^\"(.*)\"\$/\1/"
}

NTFY_BASE="$(read_env_var NTFY_BASE_URL "${ENV_FILE}")"
NTFY_BASE="${NTFY_BASE:-http://127.0.0.1:2586}"
NTFY_HOT="$(read_env_var NTFY_HOT_TOPIC "${ENV_FILE}")"
NTFY_HOT="${NTFY_HOT:-hot-alerts}"
NTFY_TOKEN="$(read_env_var NTFY_TOKEN "${SECRETS_FILE}")"
NTFY_TOKEN="${NTFY_TOKEN%%:*}"
OPERATOR_EMAIL="$(read_env_var OPERATOR_EMAIL "${ENV_FILE}")"
OPERATOR_EMAIL="${OPERATOR_EMAIL:-csexecutiveservices@gmail.com}"

# ntfy's own X-Email relay header (same mechanism common/ntfy_push.py's
# send(..., email=True) uses) -- one push fans out to both channels, per
# operator direction ("ntfy and email actually").
ntfy_send() {
    local title="$1" msg="$2" priority="${3:-5}"
    local auth_args=()
    [[ -n "${NTFY_TOKEN}" ]] && auth_args=(-H "Authorization: Bearer ${NTFY_TOKEN}")
    curl -sf --max-time 5 \
        "${auth_args[@]}" \
        -H "Title: ${title}" \
        -H "Priority: ${priority}" \
        -H "Tags: rotating_light" \
        -H "X-Email: ${OPERATOR_EMAIL}" \
        -d "${msg}" \
        "${NTFY_BASE}/${NTFY_HOT}" >/dev/null 2>&1 \
        || log "warn" "ntfy_send failed (base=${NTFY_BASE} topic=${NTFY_HOT} token_set=$([[ -n "${NTFY_TOKEN}" ]] && echo yes || echo no))"
}

# Reads thermal-ingest-guard.py's own state file and echoes its "tier"
# field (0 = normal, 1 = mild temp-only shed, 2 = LOCKDOWN -- runner is
# only ever shed at tier 2). Echoes 0 -- fails OPEN, not closed -- if the
# file is missing, unreadable, or unparseable, so a broken/stale state
# file can never permanently mask a real outage; it just means this
# specific LOCKDOWN-aware suppression doesn't apply for that one cycle,
# same as if the guard had never existed. Ported verbatim (same logic,
# same fail-open reasoning) from scripts/watchdog.sh's own _guard_tier().
_guard_tier() {
    python3 -c "
import json
try:
    with open('${GUARD_STATE_FILE}') as f:
        print(int(json.load(f).get('tier', 0)))
except Exception:
    print(0)
" 2>/dev/null || echo 0
}

read_state() {
    [[ -f "${STATE_FILE}" ]] && cat "${STATE_FILE}" || echo "0 0"  # down_since_epoch last_alert_epoch
}

write_state() {
    echo "$1 $2" > "${STATE_FILE}"
}

if [[ "${1:-}" == "--status" ]]; then
    systemctl --user is-active "${UNIT}"
    read -r down_since last_alert < <(read_state)
    echo "down_since=${down_since} last_alert=${last_alert}"
    exit 0
fi

# Single-instance lock -- same pattern as adsb-link-watchdog.sh, avoids two
# overlapping timer fires (e.g. a slow run backing up against the next
# 5-min tick) racing each other's restart attempt.
if [[ -f "${LOCK_FILE}" ]]; then
    pid=$(cat "${LOCK_FILE}" 2>/dev/null || echo "")
    if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
        log "warn" "previous run (pid ${pid}) still active -- skipping"
        exit 0
    fi
fi
echo $$ > "${LOCK_FILE}"
trap 'rm -f "${LOCK_FILE}"' EXIT

now=$(date +%s)

if systemctl --user is-active --quiet "${UNIT}"; then
    log "info" "${UNIT} active -- ok"
    write_state 0 0
    exit 0
fi

guard_tier="$(_guard_tier)"
if [[ "${guard_tier}" == "2" ]]; then
    log "info" "${UNIT} is not active, but thermal-ingest-guard tier=2 (LOCKDOWN) --" \
        "this is a deliberate shed, not an outage. Skipping restart+page so this" \
        "watchdog doesn't defeat the guard's own load-shedding mid-emergency." \
        "Will re-check next cycle; the guard's own restore logic (or its" \
        "stale-tier reconciliation) brings runner back once conditions clear."
    write_state 0 0
    exit 0
fi

read -r down_since last_alert < <(read_state)
if [[ "${down_since}" == "0" ]]; then
    down_since="${now}"
fi

log "warn" "${UNIT} is NOT active -- attempting restart"
systemctl --user start "${UNIT}" 2>/dev/null
sleep "${RESTART_SETTLE_SECS}"

if systemctl --user is-active --quiet "${UNIT}"; then
    down_secs=$(( now - down_since ))
    log "info" "${UNIT} restarted successfully (was down ~${down_secs}s)"
    ntfy_send \
        "Runner PWA was DOWN -- auto-restarted" \
        "corporatetraveldc-runner.service was not active (down roughly ${down_secs}s) -- restart command ran and it is back up now. This is why the PWA (knowledge graph, weather, CPS/live-state feed) may have looked broken. Verify at https://corporatetraveldc-dispatch.tailxxxxxxx.ts.net/ if you want to confirm by eye." \
        4
    write_state 0 "${now}"
else
    log "err" "${UNIT} restart FAILED -- still not active"
    if (( now - last_alert >= ALERT_COOLDOWN_SECS )); then
        ntfy_send \
            "Runner PWA DOWN -- restart FAILED, needs a human" \
            "corporatetraveldc-runner.service is not active and the automatic restart attempt did not bring it back. This is not a routine flap -- check 'systemctl --user status corporatetraveldc-runner' and the container logs by hand." \
            5
        write_state "${down_since}" "${now}"
    else
        log "warn" "still down but in alert cooldown ($(( ALERT_COOLDOWN_SECS - (now - last_alert) ))s remaining) -- not re-paging"
        write_state "${down_since}" "${last_alert}"
    fi
fi
