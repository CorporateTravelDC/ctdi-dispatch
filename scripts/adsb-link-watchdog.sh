#!/bin/bash
# scripts/adsb-link-watchdog.sh
# (Renamed 2026-08-03 from adsbhub-link-watchdog.sh -- generalized to cover
# every UltraFeeder outbound ADS-B target, not just adsbhub.org.)
#
# Watches all UltraFeeder -> aggregator outbound BeastReduce TCP links for
# sustained rapid-reconnect flapping -- runs as corporatetraveldc user via a
# user systemd timer every 5 minutes.
#
# 2026-08-03: data.adsbhub.org is a known-flaky third-party outbound feed --
# it disconnects the BeastReduce TCP output every 15-30s under normal
# conditions, which is expected and not alerted on. The operator's actual
# concern is faster flapping than that: if reconnects start happening in
# under 5 seconds of each other for a sustained run (not a single blip),
# that's a sign a link degraded further and worth a heads-up so the
# operator can decide whether to keep that aggregator as an outbound
# source. Same day, operator asked to widen this to every outbound feeder
# -- for ADS-B (this script) all three configured targets in
# ULTRAFEEDER_CONFIG (see corporatetraveldc-ultrafeeder.container) get the
# exact same treatment, since readsb logs every one of them through the
# same "Remote server disconnected: <host>" line, just to a different
# hostname. ACARS/VDLM/HFDL do NOT get this treatment in this script --
# see scripts/acars-feed-silence-watchdog.sh and its header for why (their
# outbound sends are UDP, which has no TCP-disconnect concept to watch;
# they get a silence detector instead, a genuinely different mechanism).
#
# This script doesn't take any corrective action itself -- it only watches
# and alerts.
#
# Usage:
#   adsb-link-watchdog.sh            # normal run (called by the timer)
#   adsb-link-watchdog.sh --status   # print current flap state per target, no alerting
#
# ASCII output only -- no Unicode symbols (repo convention).

set -uo pipefail

STATE_DIR="/var/lib/corporatetraveldc/adsb-link-watch"
LOG_FILE="${STATE_DIR}/watch.log"
LOCK_FILE="/run/user/$(id -u)/adsb-link-watchdog.lock"
ENV_FILE="/etc/corporatetraveldc/dispatch.env"
SECRETS_FILE="/etc/corporatetraveldc/dispatch-secrets.env"

UNIT="corporatetraveldc-ultrafeeder.service"
LOOKBACK="-10 minutes"

# One state file per target (not one shared file) so a flapping FlightAware
# link and a healthy ADSBHub link don't share a peak_streak/cooldown and
# mask each other. Label -> hostname exactly as it appears in readsb's own
# "Remote server disconnected: <host>" log line -- read straight out of
# ULTRAFEEDER_CONFIG in corporatetraveldc-ultrafeeder.container. Add a
# fourth entry here the day another Beast/MLAT TCP target is added to that
# config; nothing else in this script needs to change.
declare -A TARGETS=(
    ["flightaware"]="piaware.flightaware.com"
    ["flightradar24"]="data-out.flightradar24.com"
    ["adsbhub"]="data.adsbhub.org"
    ["airplaneslive"]="feed.airplanes.live"
)

# Thresholds -- see header note. FLAP_GAP_SECS is the operator's stated
# line ("less than 5 seconds"). FLAP_STREAK_MIN is how many consecutive
# sub-threshold gaps count as "sustained" rather than one-off noise --
# 3 consecutive gaps under 5s means 4 disconnects inside roughly 15s or
# less, clearly faster than the routine 15-30s cadence.
FLAP_GAP_SECS=5
FLAP_STREAK_MIN=3
ALERT_COOLDOWN_SECS=1800   # 30 min between repeat reminders while still flapping

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

# Pull disconnect-event unix timestamps for a given hostname in the
# lookback window from the ultrafeeder unit's journal. journalctl
# --output=short-unix gives "<epoch>.<micros> <rest of line>" which is
# easy to grep+cut without a second timestamp-parsing step. Cached per
# run (called once per target, journal read is the expensive part).
recent_disconnect_timestamps() {
    local host="$1"
    journalctl --user -u "${UNIT}" --since "${LOOKBACK}" --output=short-unix --no-pager 2>/dev/null \
        | grep -F "Remote server disconnected: ${host}" \
        | awk '{print $1}'
}

process_target() {
    local label="$1" host="$2" mode="$3"   # mode: "status" or "run"
    local state_file="${STATE_DIR}/${label}.state.json"

    local ts_list
    ts_list="$(recent_disconnect_timestamps "${host}")"
    local event_count
    event_count=$(echo "${ts_list}" | grep -c . || true)

    if [[ "${mode}" == "status" ]]; then
        echo "-- ${label} (${host}) --"
        echo "disconnect events in last 10 min: ${event_count}"
        if [[ -f "${state_file}" ]]; then
            python3 -c "import json; s=json.load(open('${state_file}')); print(f'peak_streak={s.get(\"peak_streak\", 0)} last_alert_ts={s.get(\"last_alert_ts\", 0)}')"
        else
            echo "no state recorded yet"
        fi
        return 0
    fi

    if [[ "${event_count}" -lt 2 ]]; then
        log "info" "${label}: only ${event_count} disconnect event(s) in lookback window -- nothing to compute, skipping"
        return 0
    fi

    local now_ts
    now_ts=$(date +%s)

    # Timestamps go to a temp file, not a pipe -- python3 - <<'EOF' already
    # occupies stdin reading its own script source from the heredoc, so a
    # piped `echo ... | python3 - <<EOF` silently hands the script zero
    # bytes on sys.stdin. Caught this in testing the single-target version
    # of this script (main run logged "0 disconnects" while --status,
    # which doesn't go through python, saw the real count) before it ever
    # reached a live alert decision.
    local ts_tmpfile
    ts_tmpfile=$(mktemp)

    echo "${ts_list}" > "${ts_tmpfile}"

    local alert_output
    alert_output=$(python3 - "${state_file}" "${now_ts}" "${FLAP_GAP_SECS}" "${FLAP_STREAK_MIN}" "${ALERT_COOLDOWN_SECS}" "${ts_tmpfile}" "${label}" "${host}" <<'PYEOF'
import json, sys

state_file, now_ts, gap_secs, streak_min, cooldown, ts_file, label, host = sys.argv[1:9]
now_ts = int(now_ts)
gap_secs = float(gap_secs)
streak_min = int(streak_min)
cooldown = int(cooldown)

with open(ts_file) as f:
    timestamps = sorted(float(line.strip()) for line in f if line.strip())

# Longest run of consecutive gaps each under gap_secs, and how many
# disconnects that run actually covers (streak of N sub-threshold gaps =
# N+1 disconnect events).
best_streak = 0
current_streak = 0
for a, b in zip(timestamps, timestamps[1:]):
    if (b - a) < gap_secs:
        current_streak += 1
        best_streak = max(best_streak, current_streak)
    else:
        current_streak = 0

try:
    with open(state_file) as f:
        state = json.load(f)
except Exception:
    state = {}

state["last_run_ts"] = now_ts
state["last_event_count"] = len(timestamps)
state["peak_streak"] = max(state.get("peak_streak", 0), best_streak)

message = None
if best_streak >= streak_min:
    last_alert = state.get("last_alert_ts") or 0
    if now_ts - last_alert >= cooldown:
        message = (
            f"{host} ({label}) link flapping fast: {best_streak + 1} "
            f"disconnects with under {gap_secs:.0f}s between them in the "
            f"last 10 min (routine cadence is 15-30s) -- link degraded "
            f"further, worth a look before deciding whether to keep them "
            f"as an outbound feed"
        )
        state["last_alert_ts"] = now_ts

with open(state_file, "w") as f:
    json.dump(state, f)

if message:
    print("ALERT:" + message)
elif best_streak > 0:
    print(f"LOG:{label}: best sub-{gap_secs:.0f}s-gap streak this run: {best_streak} (threshold {streak_min}) -- no action")
else:
    print(f"LOG:{label}: no sub-{gap_secs:.0f}s gaps this run ({len(timestamps)} disconnects, routine cadence) -- no action")
PYEOF
)

    rm -f "${ts_tmpfile}"

    echo "${alert_output}" | while IFS= read -r line; do
        [[ -z "${line}" ]] && continue
        if [[ "${line}" == LOG:* ]]; then
            log "info" "${line#LOG:}"
        elif [[ "${line}" == ALERT:* ]]; then
            log "warn" "${line#ALERT:}"
            ntfy_send "${label} link flapping" "${line#ALERT:}" 3
        fi
    done
}

if [[ "${1:-}" == "--status" ]]; then
    for label in "${!TARGETS[@]}"; do
        process_target "${label}" "${TARGETS[${label}]}" "status"
    done
    exit 0
fi

check_lock
log "info" "-------- adsb-link-watchdog run start (${#TARGETS[@]} targets) --------"

for label in "${!TARGETS[@]}"; do
    process_target "${label}" "${TARGETS[${label}]}" "run"
done

log "info" "-------- adsb-link-watchdog run end ----------"
