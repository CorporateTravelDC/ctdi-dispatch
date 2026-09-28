#!/bin/bash
# scripts/swim-session-health-watch.sh
# Observation-only SWIM/ACARS session health watcher -- runs as
# corporatetraveldc via a user systemd timer every 5 minutes.
#
# 2026-09-01: OOOI confirmation is SWIM + ACARS aggregator authority only
# (never the local receiver). That authority is worthless if the SWIM
# Solace sessions (FDPS/TBFM/TFMS/STDDS/ITWS, corporatetraveldc-ingest-*)
# are silently flapping -- a real live investigation today found AAL2077's
# missing OOOI "off" event traced to exactly that: FDPS never logged the
# flight at all while all five sessions were throwing
# SOLCLIENT_SUBCODE_KEEP_ALIVE_FAILURE at a sustained ~1 every 13s, live-
# correlated against real swap/load pressure on the box (see
# maintenance-window-on.sh for the CPUWeight side of this same incident).
# That degradation was silent -- nothing paged anyone. This closes that
# gap: same sustained/cooldown/state-file pattern as
# container-mem-watch.sh, applied to SWIM keep-alive failure rate instead
# of container memory.
#
# Does NOT restart or touch any ingest container. Observation + alert only.
#
# Usage:
#   swim-session-health-watch.sh            # normal run (called by the timer)
#   swim-session-health-watch.sh --status    # print current tracked state, no alerting
#
# ASCII output only -- no Unicode symbols (repo convention).

set -uo pipefail

STATE_DIR="/var/lib/corporatetraveldc/swim-session-health"
STATE_FILE="${STATE_DIR}/state.json"
LOG_FILE="${STATE_DIR}/watch.log"
LOCK_FILE="/run/user/$(id -u)/swim-session-health-watch.lock"
ENV_FILE="/etc/corporatetraveldc/dispatch.env"
SECRETS_FILE="/etc/corporatetraveldc/dispatch-secrets.env"

# Thresholds -- tuned against today's live incident: degraded state ran
# ~1450-1690 keep-alive failures per VPN in 6h (~4-4.7/min PER VPN,
# summed across 5 VPNs). Healthy baseline should be near-zero.
FAIL_THRESHOLD=15        # summed KEEP_ALIVE_FAILURE count across all ingest-* units, per 5min sample, to count this sample as "degraded"
SUSTAINED_SECS=600       # must stay degraded this long (2 samples at the 5min timer cadence) before alerting -- avoids single-blip noise
ALERT_COOLDOWN_SECS=1800 # don't repeat the same still-ongoing alert more than this often

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
    local title="$1" msg="$2" priority="${3:-3}" tags="${4:-satellite}"
    local auth_args=()
    [[ -n "${NTFY_TOKEN}" ]] && auth_args=(-H "Authorization: Bearer ${NTFY_TOKEN}")
    curl -sf --max-time 5 \
        "${auth_args[@]}" \
        -H "Title: ${title}" \
        -H "Priority: ${priority}" \
        -H "Tags: ${tags}" \
        -d "${msg}" \
        "${NTFY_BASE}/${NTFY_OPS}" >/dev/null 2>&1 || log "warn" "ntfy_send failed (base=${NTFY_BASE} topic=${NTFY_OPS} token_set=$([[ -n "${NTFY_TOKEN}" ]] && echo yes || echo no))"
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

# ---------------------------------------------------------------------------
# Status mode: print tracked state, no sampling, no alerting
# ---------------------------------------------------------------------------
if [[ "${1:-}" == "--status" ]]; then
    python3 - "${STATE_FILE}" <<'PYEOF'
import json, sys, time
try:
    with open(sys.argv[1]) as f:
        state = json.load(f)
except Exception:
    print("no state recorded yet")
    sys.exit(0)
now = time.time()
since = state.get("degraded_since")
print(f"degraded_since={'never' if not since else time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(since))}")
print(f"last_sample_count={state.get('last_sample_count', '?')}")
print(f"last_alert_ts={'never' if not state.get('last_alert_ts') else time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(state['last_alert_ts']))}")
print(f"last_run_ts={'never' if not state.get('last_run_ts') else time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(state['last_run_ts']))}")
PYEOF
    exit 0
fi

check_lock
log "info" "-------- swim-session-health run start --------"

now_ts=$(date +%s)

prior_ts=$(python3 - "${STATE_FILE}" <<'PYEOF'
import json, sys
try:
    with open(sys.argv[1]) as f:
        print(int(json.load(f).get("last_run_ts", 0)))
except Exception:
    print(0)
PYEOF
)
if [[ "${prior_ts}" -le 0 ]]; then
    prior_ts=$(( now_ts - 300 ))
fi
since_iso=$(date -d "@${prior_ts}" --iso-8601=seconds)

fail_tmpf=$(mktemp "${STATE_DIR}/.fails.XXXXXX")
journalctl --user -u 'corporatetraveldc-ingest-*' --no-pager -S "${since_iso}" 2>/dev/null \
    | grep "KEEP_ALIVE_FAILURE" > "${fail_tmpf}" 2>/dev/null || true

trap 'rm -f "${LOCK_FILE}" "${fail_tmpf}"' EXIT INT TERM

alert_output=$(python3 - "${STATE_FILE}" "${now_ts}" "${FAIL_THRESHOLD}" "${SUSTAINED_SECS}" \
    "${ALERT_COOLDOWN_SECS}" "${fail_tmpf}" <<'PYEOF'
import json, re, sys
from collections import Counter

state_file, now_ts, threshold, sustained_secs, alert_cd, fail_tmpf = sys.argv[1:7]
now_ts = int(now_ts)
threshold = int(threshold)
sustained_secs = int(sustained_secs)
alert_cd = int(alert_cd)

try:
    with open(state_file) as f:
        state = json.load(f)
except Exception:
    state = {}

with open(fail_tmpf) as f:
    lines = f.readlines()

count = len(lines)
vpn_re = re.compile(r"VPN name '([A-Z]+)'")
by_vpn = Counter()
for line in lines:
    m = vpn_re.search(line)
    if m:
        by_vpn[m.group(1)] += 1

state["last_sample_count"] = count
degraded_this_sample = count >= threshold

if degraded_this_sample:
    if not state.get("degraded_since"):
        state["degraded_since"] = now_ts
else:
    was_degraded = bool(state.get("degraded_since"))
    state["degraded_since"] = None
    if was_degraded and state.get("last_alert_ts"):
        # Was alerting, now recovered -- one recovery notice, then clear.
        print("RECOVER:SWIM sessions recovered -- keep-alive failure rate back under threshold")
        state["last_alert_ts"] = None

since = state.get("degraded_since")
if since:
    duration = now_ts - since
    if duration >= sustained_secs:
        last_alert = state.get("last_alert_ts") or 0
        if now_ts - last_alert >= alert_cd:
            vpn_str = ", ".join(f"{v}={n}" for v, n in by_vpn.most_common())
            print(f"ALERT:SWIM session degraded -- {count} keep-alive failures in last "
                  f"sample ({duration // 60}min sustained). By VPN: {vpn_str or 'n/a'}. "
                  f"Not gating OOOI on ADS-B -- but SWIM/ACARS coverage is currently unreliable.")
            state["last_alert_ts"] = now_ts

state["last_run_ts"] = now_ts
with open(state_file, "w") as f:
    json.dump(state, f)
PYEOF
)

if [[ -n "${alert_output}" ]]; then
    while IFS= read -r line; do
        [[ -z "${line}" ]] && continue
        if [[ "${line}" == ALERT:* ]]; then
            msg="${line#ALERT:}"
            log "warn" "${msg}"
            ntfy_send "SWIM session health: DEGRADED" "${msg}" 4 "warning,satellite"
        elif [[ "${line}" == RECOVER:* ]]; then
            msg="${line#RECOVER:}"
            log "info" "${msg}"
            ntfy_send "SWIM session health: recovered" "${msg}" 2 "white_check_mark,satellite"
        fi
    done <<< "${alert_output}"
else
    log "info" "No sustained SWIM keep-alive degradation"
fi

log "info" "-------- swim-session-health run end ----------"
