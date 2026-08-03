#!/bin/bash
# scripts/ntfy-topic-count-watchdog.sh
# Lightweight ntfy topic-count early warning -- runs as corporatetraveldc
# user via a user systemd timer every 15 minutes.
#
# 2026-08-03: visitor-subscription-limit was raised from ntfy's default
# (30) to 100 as a soft working limit (topic count was already at 61 that
# day, driven by the escalating-family alert rollout -- tbfm/tfms/fdps/
# itws/aim_fns aggregates+zones alone run ~45 topics, with stdds and a
# planned FIDS-OOOI channel still to add more). 100 gives real headroom
# over that, but headroom isn't infinite, and topic count has been growing
# fast enough this week that a silent walk up to it is a real risk. This
# script doesn't enforce anything -- it just watches ntfy's own Prometheus
# gauge (ntfy_topics_total, enabled 2026-08-03 alongside the subscription
# limit change) and pushes a heads-up to ops-health well before 150, so
# there's time to raise the limit again or reconsider growth rather than
# finding out the hard way when subscriptions start silently failing.
#
# Usage:
#   ntfy-topic-count-watchdog.sh            # normal run (called by the timer)
#   ntfy-topic-count-watchdog.sh --status   # print current count + state, no alerting
#
# ASCII output only -- no Unicode symbols (repo convention).

set -uo pipefail

STATE_DIR="/var/lib/corporatetraveldc/ntfy-topic-watch"
STATE_FILE="${STATE_DIR}/state.json"
LOG_FILE="${STATE_DIR}/watch.log"
LOCK_FILE="/run/user/$(id -u)/ntfy-topic-count-watchdog.lock"
ENV_FILE="/etc/corporatetraveldc/dispatch.env"
SECRETS_FILE="/etc/corporatetraveldc/dispatch-secrets.env"

# Thresholds -- see header note. WARN_THRESHOLD is deliberately short of
# the "close to 150" ceiling the operator named, so there's real lead time
# to act; ALERT_COOLDOWN_SECS keeps this from repeating on every 15-min
# tick once crossed (still re-reminds periodically until it drops back down).
WARN_THRESHOLD=140
ALERT_COOLDOWN_SECS=21600   # 6 hours between repeat reminders while still over

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
    local ts
    ts=$(date '+%Y-%m-%d %H:%M:%S')
    echo "[${ts}] [${level^^}] $*" >> "${LOG_FILE}" 2>/dev/null
    echo "[${ts}] [${level^^}] $*"
}

ntfy_send() {
    local title="$1" msg="$2" priority="${3:-3}"
    local auth_args=()
    [[ -n "${NTFY_TOKEN}" ]] && auth_args=(-H "Authorization: Bearer ${NTFY_TOKEN}")
    curl -sf --max-time 5 \
        "${auth_args[@]}" \
        -H "Title: ${title}" \
        -H "Priority: ${priority}" \
        -H "Tags: bar_chart" \
        -d "${msg}" \
        "${NTFY_BASE}/${NTFY_OPS}" >/dev/null 2>&1 || log "warn" "ntfy_send failed (base=${NTFY_BASE} topic=${NTFY_OPS} token_set=$([[ -n \"${NTFY_TOKEN}\" ]] && echo yes || echo no))"
}

check_lock() {
    if [[ -f "${LOCK_FILE}" ]]; then
        local pid
        pid=$(cat "${LOCK_FILE}" 2>/dev/null || echo "")
        if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
            log "info" "Already running (PID ${pid}) -- exiting"
            exit 0
        fi
    fi
    echo $$ > "${LOCK_FILE}"
    trap 'rm -f "${LOCK_FILE}"' EXIT INT TERM
}

current_count() {
    curl -sf --max-time 5 "${NTFY_BASE}/metrics" 2>/dev/null \
        | grep -E '^ntfy_topics_total ' \
        | awk '{print $2}'
}

if [[ "${1:-}" == "--status" ]]; then
    count=$(current_count)
    echo "current topic count: ${count:-unavailable}"
    if [[ -f "${STATE_FILE}" ]]; then
        python3 -c "import json; s=json.load(open('${STATE_FILE}')); print(f'last_alert_ts={s.get(\"last_alert_ts\", 0)} peak_seen={s.get(\"peak_seen\", 0)}')"
    else
        echo "no state recorded yet"
    fi
    exit 0
fi

check_lock
log "info" "-------- topic-count-watchdog run start --------"

count="$(current_count)"
if [[ -z "${count}" ]]; then
    log "warn" "could not read ntfy_topics_total from ${NTFY_BASE}/metrics -- skipping this run"
    log "info" "-------- topic-count-watchdog run end ----------"
    exit 0
fi

now_ts=$(date +%s)

# Single pass: update persisted state (peak seen, last count, cooldown
# timestamp) AND decide whether to alert, so there's no risk of a second
# read seeing state this same run already mutated (that was a real bug in
# an earlier draft of this script -- a second, separate python pass reading
# back the state that a first pass had just written made every genuine
# alert look like it was still inside its own cooldown).
alert_output=$(python3 - "${STATE_FILE}" "${now_ts}" "${count}" "${WARN_THRESHOLD}" "${ALERT_COOLDOWN_SECS}" <<'PYEOF'
import json, sys

state_file, now_ts, count, warn_threshold, cooldown = sys.argv[1:6]
now_ts = int(now_ts)
count = int(count)
warn_threshold = int(warn_threshold)
cooldown = int(cooldown)

try:
    with open(state_file) as f:
        state = json.load(f)
except Exception:
    state = {}

state["last_count"] = count
state["peak_seen"] = max(state.get("peak_seen", 0), count)
state["last_run_ts"] = now_ts

message = None
if count >= warn_threshold:
    last_alert = state.get("last_alert_ts") or 0
    if now_ts - last_alert >= cooldown:
        message = (
            f"ntfy topic count at {count} (warn threshold {warn_threshold}, "
            f"soft ceiling ~150) -- consider raising visitor-subscription-limit "
            f"again or reviewing topic growth"
        )
        state["last_alert_ts"] = now_ts

with open(state_file, "w") as f:
    json.dump(state, f)

if message:
    print("ALERT:" + message)
elif count >= warn_threshold:
    print(f"LOG:topic count {count} over threshold ({warn_threshold}) but within cooldown -- no repeat alert")
else:
    print(f"LOG:topic count {count} (warn threshold {warn_threshold}) -- no action")
PYEOF
)

echo "${alert_output}" | while IFS= read -r line; do
    [[ -z "${line}" ]] && continue
    if [[ "${line}" == LOG:* ]]; then
        log "info" "${line#LOG:}"
    elif [[ "${line}" == ALERT:* ]]; then
        log "warn" "${line#ALERT:}"
        ntfy_send "ntfy topic count approaching cap" "${line#ALERT:}" 3
    fi
done

log "info" "-------- topic-count-watchdog run end ----------"
