#!/bin/bash
# scripts/container-mem-watch.sh
# Observation-only container memory watcher -- runs as corporatetraveldc user
# via a user systemd timer every 2 minutes.
#
# Does NOT restart or touch any container. It only samples memory usage,
# watches for sustained high-water-mark pressure and real OOM-kill events,
# and pushes an ntfy alert to ops-health when a container looks like it's
# routinely running out of control -- so a human can decide whether a
# scheduled restart, a code fix, or nothing at all is the right response.
#
# OOM detection (rewritten 2026-07-19): originally used
# `podman events --filter event=oom`, which never fired even once in this
# environment despite 86+ confirmed real kernel OOM kills for
# corporatetraveldc-ingest -- podman's own internal "oom" event
# classification does not reliably fire under rootless + cgroupv2 +
# --cgroups=split here (a podman/conmon-level limitation, not a config
# bug on our side). What IS reliably present: the ordinary "died" event
# always carries ContainerExitCode, and 137 (SIGKILL) shows up there even
# when "oom" never gets emitted. So OOM detection now cross-references
# died-events with ContainerExitCode=137 against the kernel's own
# authoritative oom-kill log line (`oom-kill:constraint=CONSTRAINT_MEMCG`,
# via journalctl -k) for the same systemd unit in the same window -- the
# exact manual verification method used all session to confirm real kills.
# A 137 with no kernel corroboration is logged but NOT alerted on (could be
# a manual kill/stop timeout), keeping false positives low.
#
# ntfy auth (fixed 2026-07-19): the self-hosted ntfy instance runs
# auth-default-access=deny-all -- Tailscale/CF network proximity is never
# treated as implicit trust at any layer, every publish needs its own
# token. This script's ntfy_send() had no Authorization header, so every
# alert it ever tried to send was silently 403'd (curl -f + `|| true`
# swallowed the failure) since the day this script was written
# (2026-07-11). Fixed by sending NTFY_TOKEN (from dispatch.env) as a
# bearer token, same convention as the rest of the codebase
# (common/ntfy_push.py, shared/watchlist.py, runner/main.py).
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
# NTFY_TOKEN lives in dispatch-secrets.env, not dispatch.env -- confirmed
# 2026-07-19 this is why it was never actually in scope for either watcher
# script. ntfy runs auth-default-access=deny-all, so no token == silent 403.
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
        -H "Tags: floppy_disk" \
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

# Prior run timestamp, for the events --since window. Default to 5 min
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

since_iso=$(date -d "@${prior_ts}" --iso-8601=seconds)
until_iso=$(date -d "@${now_ts}" --iso-8601=seconds)

# ---------------------------------------------------------------------------
# 1. "died" events since the last run -- carries ContainerExitCode, which is
#    what we actually have available (podman's own "oom" event type does
#    not fire in this environment -- see header note).
# ---------------------------------------------------------------------------
died_tmpf=$(mktemp "${STATE_DIR}/.died.XXXXXX")
podman events --format json --filter event=died \
    --since "${since_iso}" --until "${until_iso}" --stream=false > "${died_tmpf}" 2>/dev/null || true

# ---------------------------------------------------------------------------
# 2. Kernel's own authoritative oom-kill log lines for the same window --
#    used to cross-confirm which died/137 events were genuinely OOM (vs.
#    a manual stop/kill timeout also producing exit 137).
# ---------------------------------------------------------------------------
kernel_oom_tmpf=$(mktemp "${STATE_DIR}/.koom.XXXXXX")
journalctl -k --since "${since_iso}" --until "${until_iso}" -o cat 2>/dev/null \
    | grep "oom-kill:constraint=CONSTRAINT_MEMCG" > "${kernel_oom_tmpf}" 2>/dev/null || true

# ---------------------------------------------------------------------------
# 3. Current memory snapshot for every live container
# ---------------------------------------------------------------------------
stats_tmpf=$(mktemp "${STATE_DIR}/.stats.XXXXXX")
podman stats --no-stream --format '{{.Name}}|{{.MemPerc}}' > "${stats_tmpf}" 2>/dev/null || true

trap 'rm -f "${LOCK_FILE}" "${died_tmpf}" "${kernel_oom_tmpf}" "${stats_tmpf}"' EXIT INT TERM

# ---------------------------------------------------------------------------
# 4. Update state, decide on alerts -- all logic in one python pass so the
#    ring buffers / cooldowns stay consistent.
# ---------------------------------------------------------------------------
alert_output=$(python3 - "${STATE_FILE}" "${now_ts}" "${HIGH_PCT}" "${SUSTAINED_SECS}" \
    "${ALERT_COOLDOWN_SECS}" "${OOM_ALERT_COOLDOWN_SECS}" "${SAMPLE_KEEP}" "${OOM_KEEP}" \
    "${died_tmpf}" "${kernel_oom_tmpf}" "${stats_tmpf}" \
    <<'PYEOF'
import json, re, sys

(state_file, now_ts, high_pct, sustained_secs, alert_cd, oom_cd, sample_keep, oom_keep,
 died_tmpf, kernel_oom_tmpf, stats_tmpf) = sys.argv[1:12]
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

with open(died_tmpf) as f:
    died_raw = f.read()
with open(kernel_oom_tmpf) as f:
    kernel_raw = f.read()
with open(stats_tmpf) as f:
    stats_raw = f.read()

# Which systemd units did the kernel actually OOM-kill in this window?
# Lines look like:
#   oom-kill:constraint=CONSTRAINT_MEMCG,...,oom_memcg=/user.slice/.../app.slice/<unit>.service/libpod-payload-...,task_memcg=.../<unit>.service/libpod-payload-...,task=...
UNIT_RE = re.compile(r"app\.slice/([A-Za-z0-9_.@-]+\.service)/")
kernel_confirmed_units = set()
for line in kernel_raw.splitlines():
    m = UNIT_RE.search(line)
    if m:
        kernel_confirmed_units.add(m.group(1))

# died events with exit code 137, mapped to their systemd unit + container name
oom_by_container = {}          # name -> [timestamps] (kernel-confirmed)
unconfirmed_137 = []            # (name, unit, ts) -- logged only, not alerted
for line in died_raw.splitlines():
    line = line.strip()
    if not line:
        continue
    try:
        ev = json.loads(line)
    except Exception:
        continue
    if str(ev.get("ContainerExitCode")) != "137":
        continue
    name = ev.get("Name")
    ts = ev.get("time")
    unit = (ev.get("Attributes") or {}).get("PODMAN_SYSTEMD_UNIT")
    if not name or not ts:
        continue
    if unit and unit in kernel_confirmed_units:
        oom_by_container.setdefault(name, []).append(int(ts))
    else:
        unconfirmed_137.append((name, unit, ts))

alerts = []
log_lines = []

for name, unit, ts in unconfirmed_137:
    log_lines.append(
        f"{name}: died with exit 137 (unit={unit}) but no kernel oom-kill line found in this window -- "
        f"not counted as OOM (could be a manual stop/kill timeout)"
    )

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

    # Kernel-confirmed OOM events for this container
    events = oom_by_container.get(name, [])
    if events:
        oom_list = c.setdefault("oom_events", [])
        oom_list.extend(events)
        c["oom_events"] = oom_list[-oom_keep:]

        last_alert = c.get("last_oom_alert_ts") or 0
        if now_ts - last_alert >= oom_cd:
            recent = [t for t in c["oom_events"] if now_ts - t < 86400]
            alerts.append(
                f"{name}: OOM-killed at memory cap ({len(recent)}x in trailing 24h, kernel-confirmed)"
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

for line in log_lines:
    print("LOG:" + line)
for a in alerts:
    print("ALERT:" + a)
PYEOF
)

if [[ -n "${alert_output}" ]]; then
    alerts_only=""
    while IFS= read -r line; do
        [[ -z "${line}" ]] && continue
        if [[ "${line}" == LOG:* ]]; then
            log "info" "${line#LOG:}"
        elif [[ "${line}" == ALERT:* ]]; then
            log "warn" "ALERT: ${line#ALERT:}"
            alerts_only+="${line#ALERT:}; "
        fi
    done <<< "${alert_output}"
    if [[ -n "${alerts_only}" ]]; then
        ntfy_send "Container memory watch" "${alerts_only}" 3
    fi
else
    log "info" "No containers over ${HIGH_PCT}% sustained, no new kernel-confirmed OOM events"
fi

log "info" "-------- mem-watch run end ----------"
