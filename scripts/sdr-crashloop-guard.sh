#!/bin/bash
# scripts/sdr-crashloop-guard.sh
#
# 2026-08-14: built live, the same morning replacement RTL-SDR hardware
# went back in after the 2026-08-11 suspected-thermal-casualty incident
# (see stack-boot-ctl.sh's ORDER comment) and both dongles were confirmed
# enumerating (ADSB1090, ACARS0130). Within ~7 minutes of ultrafeeder
# coming back up, ADSB1090 dropped off the USB bus entirely again --
# readsb went from "FATAL: rtlsdr_read_async returned unexpectedly,
# probably lost the USB device" (one transient blip, recovered clean) to
# "FATAL: rtlsdr: no device matching 'ADSB1090' found" on a tight ~16s
# Restart=always loop that was NOT recovering. Confirmed live via lsusb/
# sysfs: the dongle had genuinely vanished from the bus, not a driver
# hiccup. Stopped ultrafeeder manually in response -- this script is the
# automated version of that same judgment call, so a human doesn't have
# to be watching live next time.
#
# Deliberately UNLIKE scripts/adsb-feed-silence-watchdog.sh and
# scripts/acars-feed-silence-watchdog.sh (both explicitly "watch and
# alert only, no corrective action") -- this script DOES take action:
# stops the affected container. Rationale: those two watch for silence,
# which can mean several benign things (no aircraft in range, upstream
# quiet) and warrants a human look, not a reflexive stop. A dongle that
# has physically left the USB bus, or a container restarting faster than
# its own RestartSec should allow, has only one honest explanation --
# hardware or driver fault -- and continuing to hammer Restart=always
# against it is pure downside: wasted CPU, log spam, and (per the
# 2026-08-11 note) a real risk of compounding whatever is already wrong
# with marginal/failing hardware. Stopping is the safe default; the
# operator re-starts manually once hardware is confirmed good again
# (systemctl --user start <unit>), which also naturally re-arms this
# guard (NRestarts resets to 0 on a fresh start).
#
# Two independent trigger signals, either one fires a stop:
#   1. Device-gone: the unit's configured RTL-SDR serial is no longer
#      enumerated on the USB bus (sysfs) while the unit itself is still
#      ActiveState=active -- the fastest, most direct signal, doesn't
#      need multiple restart cycles to build confidence.
#   2. Restart-loop: NRestarts (systemd's own Restart=always counter) has
#      increased by RESTART_THRESHOLD or more since this script's last
#      run (5 min apart, matching the timer cadence) -- catches faults
#      that aren't simple device-vanishing (USB claim conflicts, driver
#      crashes) but still show up as the same "restarting far faster
#      than normal" symptom. A single restart between checks is treated
#      as the ordinary transient blip already seen recover cleanly once
#      today; it does not trigger.
#
# Usage:
#   sdr-crashloop-guard.sh            # normal run (called by the timer)
#   sdr-crashloop-guard.sh --status   # print current state per unit, no action
#
# ASCII output only -- no Unicode symbols (repo convention).

set -uo pipefail

STATE_DIR="/var/lib/corporatetraveldc/sdr-crashloop-guard"
LOG_FILE="${STATE_DIR}/watch.log"
LOCK_FILE="/run/user/$(id -u)/sdr-crashloop-guard.lock"
ENV_FILE="/etc/corporatetraveldc/dispatch.env"
SECRETS_FILE="/etc/corporatetraveldc/dispatch-secrets.env"

# unit label -> (systemd unit name, expected RTL-SDR serial)
declare -A UNIT_NAME=(
    ["ultrafeeder"]="corporatetraveldc-ultrafeeder.service"
    ["dumpvdl2"]="corporatetraveldc-dumpvdl2.service"
)
declare -A UNIT_SERIAL=(
    ["ultrafeeder"]="ADSB1090"
    ["dumpvdl2"]="ACARS0130"
)

RESTART_THRESHOLD=2
ALERT_COOLDOWN_SECS=1800   # 30 min between repeat reminders if still down/unresolved

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
    local title="$1" msg="$2" priority="${3:-4}"
    local auth_args=()
    [[ -n "${NTFY_TOKEN}" ]] && auth_args=(-H "Authorization: Bearer ${NTFY_TOKEN}")
    curl -sf --max-time 5 \
        "${auth_args[@]}" \
        -H "Title: ${title}" \
        -H "Priority: ${priority}" \
        -H "Tags: rotating_light,satellite" \
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

# Serials of every RTL2838-family device currently enumerated on the USB
# bus, one per line. Same sysfs walk used during the live incident
# investigation, not a new mechanism guessed at here.
enumerated_serials() {
    local dev
    for dev in /sys/bus/usb/devices/*/idVendor; do
        local d vid pid
        d=$(dirname "${dev}")
        vid=$(cat "${d}/idVendor" 2>/dev/null || echo "")
        pid=$(cat "${d}/idProduct" 2>/dev/null || echo "")
        if [[ "${vid}" == "0bda" && "${pid}" == "2838" ]]; then
            cat "${d}/serial" 2>/dev/null
        fi
    done
}

device_present() {
    local serial="$1"
    enumerated_serials | grep -qxF "${serial}"
}

process_unit() {
    local label="$1" mode="$2"
    local unit="${UNIT_NAME[${label}]}"
    local serial="${UNIT_SERIAL[${label}]}"
    local state_file="${STATE_DIR}/${label}.state.json"

    local active nrestarts
    active=$(systemctl --user show "${unit}" -p ActiveState --value 2>/dev/null || echo "unknown")
    nrestarts=$(systemctl --user show "${unit}" -p NRestarts --value 2>/dev/null || echo "0")
    local present="false"
    device_present "${serial}" && present="true"

    if [[ "${mode}" == "status" ]]; then
        echo "-- ${label} (${unit}, serial=${serial}) --"
        echo "ActiveState=${active} NRestarts=${nrestarts} device_present=${present}"
        if [[ -f "${state_file}" ]]; then
            python3 -c "import json; s=json.load(open('${state_file}')); print(f'last_nrestarts={s.get(\"last_nrestarts\", 0)} auto_stopped={s.get(\"auto_stopped\", False)} last_alert_ts={s.get(\"last_alert_ts\", 0)}')"
        else
            echo "no state recorded yet"
        fi
        return 0
    fi

    local now_ts
    now_ts=$(date +%s)

    local prev_nrestarts=0 last_alert=0 auto_stopped="false"
    if [[ -f "${state_file}" ]]; then
        prev_nrestarts=$(python3 -c "import json; print(json.load(open('${state_file}')).get('last_nrestarts', 0))" 2>/dev/null || echo 0)
        last_alert=$(python3 -c "import json; print(json.load(open('${state_file}')).get('last_alert_ts', 0))" 2>/dev/null || echo 0)
        auto_stopped=$(python3 -c "import json; print(str(json.load(open('${state_file}')).get('auto_stopped', False)).lower())" 2>/dev/null || echo false)
    fi

    if [[ "${active}" != "active" ]]; then
        log "info" "${label}: unit not active (${active}) -- nothing to guard right now"
        python3 -c "
import json
json.dump({'last_nrestarts': ${nrestarts}, 'last_alert_ts': ${last_alert}, 'auto_stopped': ${auto_stopped^}}, open('${state_file}', 'w'))
"
        return 0
    fi

    # Unit is active. A fresh manual start naturally re-arms this guard:
    # NRestarts resets to 0 by systemd, and this run just records that as
    # the new baseline below.
    local delta=$(( nrestarts - prev_nrestarts ))
    [[ ${delta} -lt 0 ]] && delta=0   # unit was restarted fresh since last check

    local reason=""
    if [[ "${present}" == "false" ]]; then
        reason="RTL-SDR serial ${serial} is no longer enumerated on the USB bus (dongle physically gone), but ${unit} is still active"
    elif [[ ${delta} -ge ${RESTART_THRESHOLD} ]]; then
        reason="${unit} has restarted ${delta} times since the last check (5 min ago) -- restart-loop, not the normal single-blip recovery pattern"
    fi

    if [[ -n "${reason}" ]]; then
        log "warn" "${label}: STOPPING -- ${reason}"
        systemctl --user stop "${unit}" 2>&1 | while IFS= read -r line; do log "info" "  stop: ${line}"; done
        if (( now_ts - last_alert >= ALERT_COOLDOWN_SECS )); then
            ntfy_send "SDR stack auto-stopped: ${label}" \
                "${reason}. Stopped ${unit} to avoid hammering marginal/failing hardware (see 2026-08-11 SDR thermal-casualty incident). Check the dongle physically, then 'systemctl --user start ${unit}' once confirmed good -- that also re-arms this guard." \
                4
            last_alert=${now_ts}
        fi
        python3 -c "
import json
json.dump({'last_nrestarts': ${nrestarts}, 'last_alert_ts': ${last_alert}, 'auto_stopped': True}, open('${state_file}', 'w'))
"
    else
        log "info" "${label}: healthy (NRestarts delta=${delta}, device_present=${present})"
        python3 -c "
import json
json.dump({'last_nrestarts': ${nrestarts}, 'last_alert_ts': ${last_alert}, 'auto_stopped': False}, open('${state_file}', 'w'))
"
    fi
}

if [[ "${1:-}" == "--status" ]]; then
    for label in "${!UNIT_NAME[@]}"; do
        process_unit "${label}" "status"
    done
    exit 0
fi

check_lock
log "info" "-------- sdr-crashloop-guard run start (${#UNIT_NAME[@]} units) --------"

for label in "${!UNIT_NAME[@]}"; do
    process_unit "${label}" "run"
done

log "info" "-------- sdr-crashloop-guard run end ----------"
