#!/bin/bash
# scripts/adsb-feed-silence-watchdog.sh
# Sibling of scripts/acars-feed-silence-watchdog.sh, for the LOCAL ADS-B
# SDR receive feed (UltraFeeder/readsb + tar1090) -- built 2026-08-10 per
# operator request, after investigating a real VDLM-silence alert
# uncovered that ultrafeeder had ALSO been crash-looping for 9+ hours
# ("Error: stat /dev/rtl_sdr_adsb: no such file or directory") with
# nothing watching for it at all.
#
# Deliberately NOT the same mechanism as acars-feed-silence-watchdog.sh
# (acarsrouter's own periodic "Total messages processed" log lines) --
# ultrafeeder/readsb doesn't log an equivalent periodic summary this
# script could scrape, and when it's crash-looping (as found live
# tonight) there's no useful log line to parse at all, just a repeating
# startup failure. Also distinct from scripts/adsb-link-watchdog.sh,
# which watches OUTBOUND aggregator TCP flapping (FlightAware/FR24/
# ADSBHub/airplanes.live) -- a completely different failure class from
# "is the local receive hardware even producing anything."
#
# Instead, this reuses the heartbeat file ingest/local_airspace.py ALREADY
# stamps every 30s while UltraFeeder's tar1090 aircraft.json is reachable
# (see that module's _stamp_heartbeat("ultrafeeder") call) -- a proven,
# already-live mechanism, not a new integration guessed at tonight. Found
# live during this investigation: ultrafeeder.heartbeat was 32648 seconds
# (9+ hours) stale at the exact moment the container's crash-loop was
# discovered -- confirms this signal correctly reflects a real, current
# outage, not just a hypothetical one.
#
# This script doesn't take any corrective action itself -- it only
# watches and alerts. It also cannot distinguish "container crash-looping"
# from "container fine but genuinely no aircraft observed in 80nm for a
# while" from "SDR hardware physically failed" -- all three make the
# heartbeat go stale identically. That's fine: any of the three is worth
# a human look, and the alert says exactly what it knows (heartbeat age),
# not more.
#
# Usage:
#   adsb-feed-silence-watchdog.sh            # normal run (called by the timer)
#   adsb-feed-silence-watchdog.sh --status   # print current state, no alerting
#
# ASCII output only -- no Unicode symbols (repo convention).

set -uo pipefail

STATE_DIR="/var/lib/corporatetraveldc/adsb-feed-silence-watch"
LOG_FILE="${STATE_DIR}/watch.log"
LOCK_FILE="/run/user/$(id -u)/adsb-feed-silence-watchdog.lock"
ENV_FILE="/etc/corporatetraveldc/dispatch.env"
SECRETS_FILE="/etc/corporatetraveldc/dispatch-secrets.env"

HEARTBEAT_FILE="/var/lib/corporatetraveldc/feed_state/ultrafeeder.heartbeat"

# 90s: 3x the 30s heartbeat-stamp interval, same "3 consecutive periods"
# discipline as acars-feed-silence-watchdog.sh's SILENCE_STREAK_MIN, just
# expressed directly in seconds since this checks one file's mtime rather
# than counting discrete log-derived periods.
STALE_THRESHOLD_SECS=90
ALERT_COOLDOWN_SECS=1800    # 30 min between repeat reminders while still stale

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
        -H "Tags: satellite" \
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

heartbeat_age_secs() {
    if [[ ! -f "${HEARTBEAT_FILE}" ]]; then
        echo "-1"
        return
    fi
    echo "$(( $(date +%s) - $(stat -c %Y "${HEARTBEAT_FILE}" 2>/dev/null || echo 0) ))"
}

if [[ "${1:-}" == "--status" ]]; then
    age=$(heartbeat_age_secs)
    if [[ "${age}" -lt 0 ]]; then
        echo "ultrafeeder heartbeat: never seen (file does not exist)"
    else
        echo "ultrafeeder heartbeat age: ${age}s (stale threshold ${STALE_THRESHOLD_SECS}s)"
    fi
    state_file="${STATE_DIR}/ultrafeeder.state.json"
    if [[ -f "${state_file}" ]]; then
        python3 -c "import json; s=json.load(open('${state_file}')); print(f'stale_streak={s.get(\"stale_streak\", 0)} last_alert_ts={s.get(\"last_alert_ts\", 0)}')"
    else
        echo "no state recorded yet"
    fi
    exit 0
fi

check_lock
log "info" "-------- adsb-feed-silence-watchdog run start --------"

age=$(heartbeat_age_secs)
state_file="${STATE_DIR}/ultrafeeder.state.json"
now_ts=$(date +%s)

if [[ "${age}" -lt 0 ]]; then
    log "info" "ultrafeeder: heartbeat file does not exist yet -- local_airspace.py may not have run since boot, skipping"
else
    alert_output=$(python3 - "${state_file}" "${now_ts}" "${age}" "${STALE_THRESHOLD_SECS}" "${ALERT_COOLDOWN_SECS}" <<'PYEOF'
import json, sys

state_file, now_ts, age, threshold, cooldown = sys.argv[1:6]
now_ts = int(now_ts)
age = int(age)
threshold = int(threshold)
cooldown = int(cooldown)

try:
    with open(state_file) as f:
        state = json.load(f)
except Exception:
    state = {}

is_stale = age >= threshold
state["stale_streak"] = state.get("stale_streak", 0) + 1 if is_stale else 0
state["last_run_ts"] = now_ts
state["last_age_secs"] = age

message = None
if is_stale:
    last_alert = state.get("last_alert_ts") or 0
    if now_ts - last_alert >= cooldown:
        message = (
            f"UltraFeeder/ADS-B local receive feed has gone quiet: heartbeat "
            f"last stamped {age // 60} min ago (threshold {threshold}s) -- "
            f"tar1090/aircraft.json unreachable, container may be crash-"
            f"looping (check for a missing/disconnected RTL-SDR device) or "
            f"the SDR itself may have failed. This does not distinguish a "
            f"software crash-loop from a physical hardware disconnect -- "
            f"check `podman logs corporatetraveldc-ultrafeeder` and `lsusb` "
            f"directly."
        )
        state["last_alert_ts"] = now_ts

with open(state_file, "w") as f:
    json.dump(state, f)

if message:
    print("ALERT:" + message)
elif is_stale:
    print(f"LOG:ultrafeeder: heartbeat stale ({age}s, streak {state['stale_streak']}) -- within cooldown, no new push")
else:
    print(f"LOG:ultrafeeder: heartbeat fresh ({age}s old) -- no action")
PYEOF
)

    echo "${alert_output}" | while IFS= read -r line; do
        [[ -z "${line}" ]] && continue
        if [[ "${line}" == LOG:* ]]; then
            log "info" "${line#LOG:}"
        elif [[ "${line}" == ALERT:* ]]; then
            log "warn" "${line#ALERT:}"
            ntfy_send "ADS-B feed silent" "${line#ALERT:}" 4
        fi
    done
fi

log "info" "-------- adsb-feed-silence-watchdog run end ----------"
