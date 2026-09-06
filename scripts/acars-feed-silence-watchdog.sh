#!/bin/bash
# scripts/acars-feed-silence-watchdog.sh
# Watches acarsrouter's per-channel message throughput (ACARS, VDLM, HFDL)
# for a channel that WAS receiving real traffic going sustained-silent --
# runs as corporatetraveldc user via a user systemd timer every 5 minutes.
#
# 2026-08-03: this is the ACARS/VDLM/HFDL counterpart to
# scripts/adsb-link-watchdog.sh, but it is NOT the same mechanism, on
# purpose. UltraFeeder's ADS-B outbound feeds are TCP (Beast format), so
# readsb logs a clean "Remote server disconnected" event every time the
# link drops, which is what that script watches for rapid-reconnect
# flapping. acarsrouter's outbound sends (AR_SEND_UDP_ACARS,
# AR_SEND_UDP_VDLM2 in corporatetraveldc-acarsrouter.container) are UDP --
# fire-and-forget datagrams with no connection state, no handshake, and
# nothing logged locally when a remote drops a packet. There is no
# "disconnect" event for a flap detector to watch. Checked the actual
# logs before building this (2026-08-03): acarsrouter never logged a
# single WARN/ERROR line in a 6-hour sample, and dumpvdl2's own log is
# just periodic receive-side message counts, same shape.
#
# What acarsrouter DOES log, every 5 minutes per channel (ACARS, VDLM,
# HFDL, IMSL, IRDM), is a running total and a since-last-update delta:
#   INFO acars_connection_manager::message_handler: VDLM in the last 5 minute(s):
#   Total messages processed: 30942
#   Total messages processed since last update: 14
# That delta is a real, meaningful signal, just a different kind of one --
# not "the link is flapping" but "a channel that was receiving real
# traffic has gone quiet." This script watches for 3 consecutive 5-minute
# periods with a zero delta on a channel that has previously accumulated
# a nonzero running total (the "armed" gate below) -- e.g. VDLM going from
# steady tens-per-cycle to nothing for 15 straight minutes.
#
# The armed-gate matters specifically for HFDL (and, less urgently, IMSL/
# IRDM): there is no HFDL decoder container running yet (2026-08-03), so
# HFDL's running total sits at 0 forever and would trip a naive "delta is
# zero" check immediately and permanently -- a channel that never had a
# source isn't a silence event. This script only arms a channel's alerting
# once its own running total has gone above zero at least once, so it
# is inert (never alerts) for HFDL today, and automatically starts
# actually watching HFDL the day a real decoder gets added and starts
# feeding it -- no config change needed here when that happens.
#
# ACARS decoding is also currently pending re-enable (see
# corporatetraveldc-acarsrouter.container's own comment,
# "acarsdec re-enable pending") -- same reasoning, same automatic
# behavior: inert until ACARS actually starts accumulating a real total.
#
# This script doesn't take any corrective action itself -- it only
# watches and alerts.
#
# Usage:
#   acars-feed-silence-watchdog.sh            # normal run (called by the timer)
#   acars-feed-silence-watchdog.sh --status   # print current per-channel state, no alerting
#
# ASCII output only -- no Unicode symbols (repo convention).

set -uo pipefail

STATE_DIR="/var/lib/corporatetraveldc/acars-feed-silence-watch"
LOG_FILE="${STATE_DIR}/watch.log"
LOCK_FILE="/run/user/$(id -u)/acars-feed-silence-watchdog.lock"
ENV_FILE="/etc/corporatetraveldc/dispatch.env"
SECRETS_FILE="/etc/corporatetraveldc/dispatch-secrets.env"

UNIT="corporatetraveldc-acarsrouter.service"
# acarsrouter emits its own summary every 5 minutes internally; a 20-minute
# lookback comfortably covers at least 3 of those cycles even if this
# watchdog's own timer tick lands a little off from acarsrouter's.
LOOKBACK="-20 minutes"

# Channels tracked -- add HFDL's siblings (IMSL, IRDM) here the day there's
# an operator reason to watch them too; acarsrouter already reports them
# in the same log shape, this script just isn't reading them yet.
CHANNELS=(ACARS VDLM HFDL)

SILENCE_STREAK_MIN=3        # consecutive zero-delta periods before alerting
ALERT_COOLDOWN_SECS=1800    # 30 min between repeat reminders while still silent

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

# Emit "<epoch> <channel> <total> <delta>" one line per summary block found
# in the lookback window, oldest first. journalctl -A2 keeps each 3-line
# block (header + 2 totals) together so the parser below never has to
# reassociate lines across a truncated read.
raw_summary_lines() {
    journalctl --user -u "${UNIT}" --since "${LOOKBACK}" --output=short-unix --no-pager 2>/dev/null \
        | grep -A2 -F "message_handler:"
}

parse_summaries() {
    raw_summary_lines | python3 -c '
import re, sys

ts = None
channel = None
total = None
for raw in sys.stdin:
    line = raw.rstrip("\n")
    if "message_handler:" in line:
        m = re.match(r"^(\d+\.\d+)\s", line)
        chm = re.search(r"message_handler:\s+(\S+)\s+in the last", line)
        if m and chm:
            ts = m.group(1)
            channel = chm.group(1)
            total = None
        continue
    m = re.search(r"Total messages processed:\s+(\d+)$", line)
    if m and channel is not None:
        total = m.group(1)
        continue
    m = re.search(r"Total messages processed since last update:\s+(\d+)$", line)
    if m and channel is not None and total is not None:
        print(f"{ts} {channel} {total} {m.group(1)}")
        channel = None
        total = None
'
}

if [[ "${1:-}" == "--status" ]]; then
    parsed="$(parse_summaries)"
    for ch in "${CHANNELS[@]}"; do
        echo "-- ${ch} --"
        echo "${parsed}" | awk -v c="${ch}" '$2 == c {print "  " $1, "total="$3, "delta="$4}'
        state_file="${STATE_DIR}/${ch}.state.json"
        if [[ -f "${state_file}" ]]; then
            python3 -c "import json; s=json.load(open('${state_file}')); print(f'  armed={s.get(\"armed\", False)} last_alert_ts={s.get(\"last_alert_ts\", 0)}')"
        else
            echo "  no state recorded yet"
        fi
    done
    exit 0
fi

check_lock
log "info" "-------- acars-feed-silence-watchdog run start (${#CHANNELS[@]} channels) --------"

parsed="$(parse_summaries)"
now_ts=$(date +%s)

for ch in "${CHANNELS[@]}"; do
    ch_lines="$(echo "${parsed}" | awk -v c="${ch}" '$2 == c {print $3, $4}')"
    ch_count=$(echo "${ch_lines}" | grep -c . || true)

    if [[ "${ch_count}" -eq 0 ]]; then
        log "info" "${ch}: no summary blocks in lookback window -- skipping (unit may have just restarted)"
        continue
    fi

    state_file="${STATE_DIR}/${ch}.state.json"
    tmpfile=$(mktemp)
    echo "${ch_lines}" > "${tmpfile}"

    alert_output=$(python3 - "${state_file}" "${now_ts}" "${SILENCE_STREAK_MIN}" "${ALERT_COOLDOWN_SECS}" "${tmpfile}" "${ch}" <<'PYEOF'
import json, sys

state_file, now_ts, streak_min, cooldown, ts_file, channel = sys.argv[1:7]
now_ts = int(now_ts)
streak_min = int(streak_min)
cooldown = int(cooldown)

rows = []
with open(ts_file) as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        total_s, delta_s = line.split()
        rows.append((int(total_s), int(delta_s)))

latest_total = rows[-1][0]
trailing = rows[-streak_min:] if len(rows) >= streak_min else None

try:
    with open(state_file) as f:
        state = json.load(f)
except Exception:
    state = {}

# Arm the gate the first time this channel's running total goes above
# zero -- before that, a "silent" reading just means there was never a
# decoder feeding it (HFDL today, ACARS until acarsdec is re-enabled),
# not an outage.
was_armed = state.get("armed", False)
armed = was_armed or latest_total > 0

# 2026-08-10: peak_total tracked SEPARATELY from latest_total, found
# investigating a real VDLM alert whose own message read "after previously
# accumulating 0 total" -- self-contradictory (armed=True is only supposed
# to mean it once had real traffic). Root cause: acarsrouter's own
# "Total messages processed" counter resets to 0 on container/process
# restart, but this script's `armed` flag is a separate, persisted state
# that survives that restart -- so a channel that had 30000+ messages
# before a restart, then genuinely goes silent (a real outage, as VDLM's
# was), reports latest_total=0 and the message reads as if it never had
# any traffic at all. peak_total is the max ever observed across restarts,
# so the alert can say what the channel actually achieved historically,
# and separately flag when a restart happened (current total dropped from
# a nonzero peak) as a distinct, useful data point rather than erasing it.
peak_total = max(state.get("peak_total", 0), latest_total)
restart_detected = peak_total > 0 and latest_total < peak_total
state["armed"] = armed
state["peak_total"] = peak_total
state["last_run_ts"] = now_ts
state["last_total"] = latest_total

message = None
if armed and trailing is not None and all(delta == 0 for _, delta in trailing):
    last_alert = state.get("last_alert_ts") or 0
    if now_ts - last_alert >= cooldown:
        minutes = streak_min * 5
        restart_note = (
            f" (NOTE: acarsrouter's own counter shows {latest_total}, below "
            f"the {peak_total} peak previously seen -- acarsrouter itself "
            f"likely restarted; this does NOT mean the outage is benign, "
            f"just that 'total' resets on restart -- check the decoder "
            f"container/SDR hardware directly)"
            if restart_detected else ""
        )
        message = (
            f"{channel} feed has gone quiet: 0 new messages for the last "
            f"{minutes} min ({streak_min} consecutive 5-min periods) -- "
            f"peak total ever seen: {peak_total}, current: {latest_total}."
            f"{restart_note} Decoder or upstream source may have dropped"
        )
        state["last_alert_ts"] = now_ts

with open(state_file, "w") as f:
    json.dump(state, f)

if message:
    print("ALERT:" + message)
elif not armed:
    print(f"LOG:{channel}: not armed yet (total={latest_total}, no decoder/traffic seen so far) -- no action")
elif trailing is None:
    print(f"LOG:{channel}: only {len(rows)} summary period(s) in window, need {streak_min} to evaluate silence -- no action")
else:
    print(f"LOG:{channel}: armed, recent deltas {[d for _, d in trailing]} -- not silent, no action")
PYEOF
)

    rm -f "${tmpfile}"

    echo "${alert_output}" | while IFS= read -r line; do
        [[ -z "${line}" ]] && continue
        if [[ "${line}" == LOG:* ]]; then
            log "info" "${line#LOG:}"
        elif [[ "${line}" == ALERT:* ]]; then
            log "warn" "${line#ALERT:}"
            ntfy_send "${ch} feed silent" "${line#ALERT:}" 3
        fi
    done
done

log "info" "-------- acars-feed-silence-watchdog run end ----------"
