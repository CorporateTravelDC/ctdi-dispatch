#!/bin/bash
# scripts/container-mem-watch.sh
# Observation-only container memory watcher -- runs as corporatetraveldc user
# via a user systemd timer every 2 minutes.
#
# Does NOT restart or touch any container. It only samples memory usage,
# watches for sustained high-water-mark pressure and real OOM-kill events
# (via `podman events --filter event=oom`), and pushes an ntfy alert to
# ops-health when a container looks like it's routinely running out of
# control -- so a human can decide whether a scheduled restart, a code fix,
# or nothing at all is the right response.
#
# Usage:
#   container-mem-watch.sh            # normal run (called by the timer)
#   container-mem-watch.sh --status   # print current tracked state, no alerting
#
# ASCII output only -- no Unicode symbols (repo convention).

set -uo pipefail

STATE_DIR="/var/lib/corporatetraveldc/container-mem-watch"
STATE_FILE="${STATE_DIR}/state.json"
LOG_FILE="${STATE_DIR}/watch.log"
LOCK_FILE="/run/user/$(id -u)/container-mem-watch.lock"
ENV_FILE="/etc/corporatetraveldc/dispatch.env"

# Thresholds
HIGH_PCT=80            # % of cap considered "under pressure"
SUSTAINED_SECS=600     # must stay >= HIGH_PCT this long, continuously, before alerting
ALERT_COOLDOWN_SECS=1800   # don't repeat the same still-ongoing alert more than this often
OOM_ALERT_COOLDOWN_SECS=900
SAMPLE_KEEP=60          # ring buffer length per container (~2hr at 2min cadence)
OOM_KEEP=20             # OOM event timestamps kept per container

mkdir -p "${STATE_DIR}"

[[ -f "${ENV_FILE}" ]] && source "${ENV_FILE}" 2>/dev/null || true
NTFY_BASE="${NTFY_BASE_URL:-http://127.0.0.1:2586}"
NTFY_OPS="${NTFY_OPS_TOPIC:-ops-health}"

log() {
    local level="$1"; shift
    local ts
    ts=$(date '+%Y-%m-%d %H:%M:%S')
    echo "[${ts}] [${level^^}] $*" >> "${LOG_FILE}" 2>/dev/null
    echo "[${ts}] [${level^^}] $*"
}

ntfy_send() {
    local title="$1" msg="$2" priority="${3:-3}"
    curl -sf --max-time 5 \
        -H "Title: ${title}" \
        -H "Priority: ${priority}" \
        -H "Tags: floppy_disk" \
        -d "${msg}" \
        "${NTFY_BASE}/${NTFY_OPS}" >/dev/null 2>&1 || true
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
containers = state.get("containers", {})
if not containers:
    print("no containers tracked yet")
    sys.exit(0)

for name, c in sorted(containers.items()):
    samples = c.get("samples", [])
    last_pct = samples[-1][1] if samples else None
    oom_events = c.get("oom_events", [])
    recent_oom = [t for t in oom_events if now - t < 86400]
    sustained_since = c.get("sustained_high_since")
    sustained_str = ""
    if sustained_since:
        sustained_str = f" sustained-high for {int((now - sustained_since) / 60)}m"
    print(f"{name:38s} mem={last_pct if last_pct is not None else '?'}%"
          f"  oom(24h)={len(recent_oom)}{sustained_str}")
PYEOF
    exit 0
fi

check_lock
log "info" "-------- mem-watch run start --------"

now_ts=$(date +%s)

# Prior run timestamp, for the OOM events --since window. Default to 5 min
# lookback on first run so we don't miss anything between install and first fire.
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

# ---------------------------------------------------------------------------
# 1. Real OOM-kill events since the last run (authoritative -- not sampled)
# ---------------------------------------------------------------------------
oom_tmpf=$(mktemp "${STATE_DIR}/.oom.XXXXXX")
since_iso=$(date -d "@${prior_ts}" --iso-8601=seconds)
until_iso=$(date -d "@${now_ts}" --iso-8601=seconds)
podman events --format json --filter event=oom \
    --since "${since_iso}" --until "${until_iso}" --stream=false > "${oom_tmpf}" 2>/dev/null || true

# ---------------------------------------------------------------------------
# 2. Current memory snapshot for every live container
# ---------------------------------------------------------------------------
stats_tmpf=$(mktemp "${STATE_DIR}/.stats.XXXXXX")
podman stats --no-stream --format '{{.Name}}|{{.MemPerc}}' > "${stats_tmpf}" 2>/dev/null || true

trap 'rm -f "${LOCK_FILE}" "${oom_tmpf}" "${stats_tmpf}"' EXIT INT TERM

# ---------------------------------------------------------------------------
# 3. Update state, decide on alerts -- all logic in one python pass so the
#    ring buffers / cooldowns stay consistent.
# ---------------------------------------------------------------------------
alert_output=$(python3 - "${STATE_FILE}" "${now_ts}" "${HIGH_PCT}" "${SUSTAINED_SECS}" \
    "${ALERT_COOLDOWN_SECS}" "${OOM_ALERT_COOLDOWN_SECS}" "${SAMPLE_KEEP}" "${OOM_KEEP}" \
    "${oom_tmpf}" "${stats_tmpf}" \
    <<'PYEOF'
import json, sys

(state_file, now_ts, high_pct, sustained_secs, alert_cd, oom_cd, sample_keep, oom_keep,
 oom_tmpf, stats_tmpf) = sys.argv[1:11]
now_ts = int(now_ts)
high_pct = float(high_pct)
sustained_secs = int(sustained_secs)
alert_cd = int(alert_cd)
oom_cd = int(oom_cd)
sample_keep = int(sample_keep)
oom_keep = int(oom_keep)

try:
    with open(state_file) as f:
        state = json.load(f)
except Exception:
    state = {}
containers = state.setdefault("containers", {})

with open(oom_tmpf) as f:
    oom_raw = f.read()
with open(stats_tmpf) as f:
    stats_raw = f.read()

oom_by_container = {}
for line in oom_raw.splitlines():
    line = line.strip()
    if not line:
        continue
    try:
        ev = json.loads(line)
    except Exception:
        continue
    name = ev.get("Name")
    ts = ev.get("time")
    if name and ts:
        oom_by_container.setdefault(name, []).append(int(ts))

alerts = []

seen_names = set()
for line in stats_raw.splitlines():
    line = line.strip()
    if not line or "|" not in line:
        continue
    name, pct_raw = line.split("|", 1)
    name = name.strip()
    seen_names.add(name)
    try:
        pct = float(pct_raw.strip().rstrip("%"))
    except ValueError:
        continue

    c = containers.setdefault(name, {})
    samples = c.setdefault("samples", [])
    samples.append([now_ts, pct])
    c["samples"] = samples[-sample_keep:]

    # Sustained-high tracking
    if pct >= high_pct:
        if not c.get("sustained_high_since"):
            c["sustained_high_since"] = now_ts
    else:
        c["sustained_high_since"] = None

    since = c.get("sustained_high_since")
    if since:
        duration = now_ts - since
        if duration >= sustained_secs:
            last_alert = c.get("last_sustained_alert_ts") or 0
            if now_ts - last_alert >= alert_cd:
                alerts.append(
                    f"{name}: sustained {pct:.0f}% of cap for {duration // 60}min"
                )
                c["last_sustained_alert_ts"] = now_ts

    # OOM events for this container
    events = oom_by_container.get(name, [])
    if events:
        oom_list = c.setdefault("oom_events", [])
        oom_list.extend(events)
        c["oom_events"] = oom_list[-oom_keep:]

        last_alert = c.get("last_oom_alert_ts") or 0
        if now_ts - last_alert >= oom_cd:
            recent = [t for t in c["oom_events"] if now_ts - t < 86400]
            alerts.append(
                f"{name}: OOM-killed at memory cap ({len(recent)}x in trailing 24h)"
            )
            c["last_oom_alert_ts"] = now_ts

# Drop containers that are no longer running at all (stopped/removed) to
# keep the state file from growing forever with dead entries.
for stale in [n for n in list(containers) if n not in seen_names]:
    c = containers[stale]
    # keep OOM history for a stopped container for a while in case it restarts
    # under a different podman-generated name; otherwise prune after 7 days
    last_seen = c.get("samples", [[0, 0]])[-1][0] if c.get("samples") else 0
    if now_ts - last_seen > 7 * 86400:
        del containers[stale]

state["last_run_ts"] = now_ts
with open(state_file, "w") as f:
    json.dump(state, f)

for a in alerts:
    print(a)
PYEOF
)

if [[ -n "${alert_output}" ]]; then
    while IFS= read -r line; do
        [[ -z "${line}" ]] && continue
        log "warn" "ALERT: ${line}"
    done <<< "${alert_output}"
    summary=$(echo "${alert_output}" | tr '\n' '; ')
    ntfy_send "Container memory watch" "${summary}" 3
else
    log "info" "No containers over ${HIGH_PCT}% sustained, no new OOM events"
fi

log "info" "-------- mem-watch run end ----------"
