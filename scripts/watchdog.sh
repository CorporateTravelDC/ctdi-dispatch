#!/bin/bash
# /opt/corporatetraveldc/scripts/watchdog.sh
# Dispatch stack health watchdog
# Runs as root via systemd timer every 90s
# Monitors: thermals, throttle, system services, containers, API liveness, feed freshness
# ASCII output only -- no Unicode symbols
#
# 2026-08-21: --allow-system-restart gate. restart_full_stack() touches
# SYSTEM_SERVICES (pihole-FTL, cloudflared, tailscaled -- real root-level
# host infrastructure, not the dispatch stack's own --user containers) via
# raw root-scope `systemctl restart`. The timer-triggered automatic runs
# must NEVER do that unattended -- operator directive, after the very
# first automatic run took the whole stack down: cloudflared is actually a
# --user unit (see user_ctl() below), so the OLD root-scope
# `systemctl is-active cloudflared.service` check always reported it
# falsely down, which alone was enough to trigger a full unattended
# restart of pihole-FTL/unbound/cloudflared/tailscaled plus the dispatch
# containers. Without this flag, a warranted full-stack restart is now
# ALERT-ONLY -- it tells the operator exactly what to run, but never
# executes it. Only an explicit, human-run
# `sudo scripts/watchdog.sh --allow-system-restart` performs the actual
# restart. Pure --user container restarts (restart_containers(), gated by
# DO_RESTART_CONTAINERS, never touches SYSTEM_SERVICES) are unaffected by
# this flag and remain safe to run unattended, same as
# corporatetraveldc-ingest-restart.timer already does elsewhere in this
# repo for a narrower case.
ALLOW_SYSTEM_RESTART=0
for arg in "$@"; do
    [[ "${arg}" == "--allow-system-restart" ]] && ALLOW_SYSTEM_RESTART=1
done

set -uo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CTDC_USER="corporatetraveldc"
CTDC_UID=$(id -u "${CTDC_USER}" 2>/dev/null || echo "")

# User systemd session paths
XDG_USER_DIR="/run/user/${CTDC_UID}"
DBUS_ADDR="unix:path=${XDG_USER_DIR}/bus"

# API -- local loopback only; never goes through Cloudflare tunnel
API_BASE="http://127.0.0.1:8000"

# Paths
LOG_DIR="/var/log/corporatetraveldc"
LOG_FILE="${LOG_DIR}/watchdog.log"
LOCK_FILE="/run/corporatetraveldc-watchdog.lock"
COOLDOWN_FILE="/run/corporatetraveldc-watchdog-cooldown"
QUADLET_DIR="/home/${CTDC_USER}/.config/containers/systemd"
ENV_FILE="/etc/corporatetraveldc/dispatch.env"

# 2026-08-23: thermal-ingest-guard.py's own state file -- see
# _guard_tier() below. Same path as that script's STATE_FILE constant.
GUARD_STATE_FILE="/var/lib/corporatetraveldc/thermal_ingest_guard_state.json"
# Units the guard's own LOCKDOWN sheds that this watchdog also manages.
# Deliberately NOT "corporatetraveldc-web" -- the guard never sheds web
# (it's the one designated LOCKDOWN survivor, see CLAUDE.md's "Ingest
# load-shedding"), so a down web is never explained by a LOCKDOWN and
# this watchdog must still act on it regardless of guard tier.
GUARD_MANAGED_CONTAINERS=("corporatetraveldc-poller" "corporatetraveldc-pusher")

# Thermal thresholds (millidegrees Celsius -- /sys/class/thermal/thermal_zone0/temp)
TEMP_WARN_MC=75000    # 75 C -- log + ntfy warn
TEMP_CRIT_MC=82000    # 82 C -- trigger full stack restart
TEMP_ZONE="/sys/class/thermal/thermal_zone0/temp"

# vcgencmd throttle bitmask positions
# Bit 0 = under-voltage  Bit 1 = freq cap  Bit 2 = currently throttled  Bit 3 = soft temp limit
THROTTLE_CURRENTLY=2
THROTTLE_SOFT_LIMIT=3
THROTTLE_UNDER_VOLTAGE=0

# Per-feed staleness thresholds (seconds) -- from dispatch-ops skill
declare -A FEED_STALE_CRIT=(
    [metar]=900        # 15 min
    [tfr]=900          # 15 min
    [nws]=2700         # 45 min
    [nas]=900          # 15 min
    [ops_plan]=10800   # 3 hr
    [atcscc_opsplan]=10800
    [amtrak]=900       # 15 min
    [runsheet]=1800    # 30 min (local file -- longer tolerance)
)
FEED_STALE_DEFAULT=900   # 15 min for any feed not in the map above

# API timeouts
CURL_TIMEOUT=5

# Restart cooldown -- prevents restart thrash (seconds)
COOLDOWN_SEC=300

# 2026-08-23: operator directive after this watchdog was caught fighting
# thermal-ingest-guard.py's LOCKDOWN mechanism twice in one afternoon
# (12:18 and 14:34) -- see CLAUDE.md's "FOURTH FINDING". A single bad
# 90s cycle used to trigger an immediate stop-all/start-all across the
# whole CONTAINERS array, which is both too twitchy (one 5s /healthz
# timeout under load reads identically to a dead API) and too broad
# (restarted web/poller/pusher together even when only poller/pusher
# were actually down). Now requires FAIL_THRESHOLD consecutive failed
# cycles for a given container/check before it counts, tracked
# per-key in STREAK_FILE across runs (this script is a oneshot timer,
# not a daemon -- state must persist on disk between invocations, same
# reasoning as COOLDOWN_FILE above). At the 90s cadence this is a ~7.5
# minute debounce.
STREAK_FILE="/run/corporatetraveldc-watchdog-streaks"
FAIL_THRESHOLD=5

# Ordered container list -- start in this order, stop in reverse
# 2026-08-23: the pre-split monolithic "corporatetraveldc-ingest" unit
# (retired when ingest became 7 per-feed Quadlets -- see CLAUDE.md's
# "Ingest load-shedding") had been sitting here dead since the split,
# silently logging "[SKIP] (no .container file)" every cycle. Removed
# rather than replaced with the real per-feed unit names on purpose --
# thermal-ingest-guard.py is the sole owner of SWIM feed lifecycle
# (shed/restore); this watchdog manages only the core process stack.
CONTAINERS=(
    "corporatetraveldc-web"
    "corporatetraveldc-poller"
    "corporatetraveldc-pusher"
)

# System services verified before starting containers
SYSTEM_SERVICES=(
    "pihole-FTL"
    "cloudflared"
    "tailscaled"
)

DNS_TARGET="dns-stack-ready.target"
DNS_WAIT_MAX=60     # seconds to wait for DNS target before proceeding
CONTAINER_WAIT=4    # seconds between container starts

# ---------------------------------------------------------------------------
# Load platform env (non-secret)
# ---------------------------------------------------------------------------

[[ -f "${ENV_FILE}" ]] && source "${ENV_FILE}" 2>/dev/null || true

NTFY_BASE="${NTFY_BASE_URL:-http://127.0.0.1:2586}"
NTFY_OPS="${NTFY_OPS_TOPIC:-ops-health}"
NTFY_HOT="${NTFY_HOT_TOPIC:-hot-alerts}"

# ---------------------------------------------------------------------------
# State flags -- set during checks, acted on in report()
# ---------------------------------------------------------------------------

ISSUES=()
DO_WARN_ONLY=0
DO_RESTART_CONTAINERS=0
DO_RESTART_STACK=0
# 2026-08-23: only the containers that actually crossed FAIL_THRESHOLD
# get touched by restart_containers() -- see the CONTAINERS/STREAK_FILE
# comment above. Populated by check_containers()/check_api().
FAILED_CONTAINERS=()
declare -A STREAKS=()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

log() {
    local level="$1"; shift
    local msg="$*"
    local ts
    ts=$(date '+%Y-%m-%d %H:%M:%S')
    local line="[${ts}] [${level^^}] ${msg}"
    mkdir -p "${LOG_DIR}"
    echo "${line}" >> "${LOG_FILE}" 2>/dev/null || true
    echo "${line}"  # captured by journald via StandardOutput=journal
    logger -t "ctdc-watchdog" -p "daemon.${level,,}" "${msg}" 2>/dev/null || true
}

ntfy_send() {
    local topic="$1" title="$2" msg="$3" priority="${4:-3}"
    curl -sf --max-time 5 \
        -H "Title: ${title}" \
        -H "Priority: ${priority}" \
        -H "Tags: gear" \
        -d "${msg}" \
        "${NTFY_BASE}/${topic}" >/dev/null 2>&1 || true
}

user_ctl() {
    # Delegate systemctl --user to corporatetraveldc from root context
    if [[ -z "${CTDC_UID}" ]]; then
        log "err" "Cannot resolve UID for ${CTDC_USER} -- container ops unavailable"
        return 1
    fi
    sudo -u "${CTDC_USER}" \
        XDG_RUNTIME_DIR="${XDG_USER_DIR}" \
        DBUS_SESSION_BUS_ADDRESS="${DBUS_ADDR}" \
        systemctl --user "$@" 2>/dev/null
}

container_active() {
    user_ctl is-active --quiet "${1}.service"
}

container_exists() {
    # A .container file exists (not .container.disabled)
    [[ -f "${QUADLET_DIR}/${1}.container" ]]
}

api_get() {
    curl -sf --max-time "${CURL_TIMEOUT}" "${API_BASE}${1}" 2>/dev/null
}

# Reads thermal-ingest-guard.py's own state file and echoes its "tier"
# field (0 = normal, 1 = mild temp-only shed, 2 = LOCKDOWN). Echoes 0 --
# fails OPEN, not closed -- if the file is missing, unreadable, or
# unparseable, so a broken/stale state file can never permanently mask a
# real container-down condition; it just means this specific guard-aware
# suppression doesn't apply for that one cycle, same as if the guard had
# never existed.
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

_in_guard_managed() {
    local needle="$1" x
    for x in "${GUARD_MANAGED_CONTAINERS[@]}"; do
        [[ "${x}" == "${needle}" ]] && return 0
    done
    return 1
}

check_lock() {
    if [[ -f "${LOCK_FILE}" ]]; then
        local pid
        pid=$(cat "${LOCK_FILE}" 2>/dev/null || echo "")
        if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
            log "info" "Watchdog already running (PID ${pid}) -- exiting"
            exit 0
        fi
    fi
    echo $$ > "${LOCK_FILE}"
    trap 'rm -f "${LOCK_FILE}"' EXIT INT TERM
}

in_cooldown() {
    if [[ -f "${COOLDOWN_FILE}" ]]; then
        local ts now
        ts=$(cat "${COOLDOWN_FILE}" 2>/dev/null || echo "0")
        now=$(date +%s)
        (( now - ts < COOLDOWN_SEC ))
        return
    fi
    return 1
}

mark_cooldown() {
    date +%s > "${COOLDOWN_FILE}"
}

cooldown_remaining() {
    if [[ -f "${COOLDOWN_FILE}" ]]; then
        local ts now
        ts=$(cat "${COOLDOWN_FILE}" 2>/dev/null || echo "0")
        now=$(date +%s)
        local remaining=$(( COOLDOWN_SEC - (now - ts) ))
        (( remaining > 0 )) && echo "${remaining}" || echo "0"
    else
        echo "0"
    fi
}

# ---------------------------------------------------------------------------
# Failure-streak tracking (persists across runs -- see STREAK_FILE above)
# ---------------------------------------------------------------------------

load_streaks() {
    STREAKS=()
    [[ -f "${STREAK_FILE}" ]] || return
    local k v
    while IFS='=' read -r k v; do
        [[ -z "${k}" ]] && continue
        STREAKS["${k}"]="${v}"
    done < "${STREAK_FILE}"
}

save_streaks() {
    local k tmpf
    tmpf=$(mktemp "${STREAK_FILE}.XXXXXX")
    for k in "${!STREAKS[@]}"; do
        echo "${k}=${STREAKS[${k}]}" >> "${tmpf}"
    done
    mv -f "${tmpf}" "${STREAK_FILE}"
}

# Increments the named streak in place. Must be called directly, NOT via
# $(bump_streak ...) -- command substitution forks a subshell, and the
# associative-array mutation would be silently discarded when it exits,
# leaving STREAKS permanently un-incremented (caught in testing before
# this shipped). Read STREAKS[key] yourself after calling.
bump_streak() {
    local key="$1"
    local cur="${STREAKS[${key}]:-0}"
    (( cur += 1 ))
    STREAKS["${key}"]="${cur}"
}

reset_streak() {
    STREAKS["$1"]=0
}

# Adds a container to FAILED_CONTAINERS if not already present.
add_failed_container() {
    local svc="$1" x
    for x in "${FAILED_CONTAINERS[@]:-}"; do
        [[ "${x}" == "${svc}" ]] && return
    done
    FAILED_CONTAINERS+=("${svc}")
}

# ---------------------------------------------------------------------------
# Check 1: Thermal state
# ---------------------------------------------------------------------------

check_thermal() {
    log "info" "CHECK thermal"

    local temp_mc=0
    if [[ -r "${TEMP_ZONE}" ]]; then
        temp_mc=$(cat "${TEMP_ZONE}" 2>/dev/null || echo "0")
    else
        log "warn" "  Thermal zone not readable: ${TEMP_ZONE}"
        return
    fi

    local temp_c=$(( temp_mc / 1000 ))

    if (( temp_mc >= TEMP_CRIT_MC )); then
        log "err" "  [FAIL] CPU temp ${temp_c}C -- CRITICAL (>= $(( TEMP_CRIT_MC / 1000 ))C)"
        ISSUES+=("temp_critical:${temp_c}C")
        DO_RESTART_STACK=1
    elif (( temp_mc >= TEMP_WARN_MC )); then
        log "warn" "  [WARN] CPU temp ${temp_c}C -- elevated (>= $(( TEMP_WARN_MC / 1000 ))C)"
        ISSUES+=("temp_warn:${temp_c}C")
        DO_WARN_ONLY=1
    else
        log "info" "  [OK] CPU temp ${temp_c}C"
    fi
}

# ---------------------------------------------------------------------------
# Check 2: vcgencmd throttle state (Pi-specific)
# ---------------------------------------------------------------------------

check_throttle() {
    log "info" "CHECK vcgencmd throttle"

    if ! command -v vcgencmd >/dev/null 2>&1; then
        log "info" "  vcgencmd not found -- skipping (non-Pi or path issue)"
        return
    fi

    local raw
    raw=$(vcgencmd get_throttled 2>/dev/null || echo "throttled=0x0")
    local hex="${raw#*=}"
    # Convert hex to decimal safely
    local val
    val=$(printf '%d' "${hex}" 2>/dev/null || echo "0")

    local throttled=$(( (val >> THROTTLE_CURRENTLY) & 1 ))
    local soft_limit=$(( (val >> THROTTLE_SOFT_LIMIT) & 1 ))
    local under_voltage=$(( (val >> THROTTLE_UNDER_VOLTAGE) & 1 ))

    if (( throttled )); then
        log "err" "  [FAIL] CPU currently throttled -- ${raw}"
        ISSUES+=("throttle_active")
        DO_RESTART_STACK=1
    elif (( soft_limit )); then
        log "warn" "  [WARN] Soft temperature limit active -- ${raw}"
        ISSUES+=("soft_temp_limit")
        DO_WARN_ONLY=1
    elif (( under_voltage )); then
        log "warn" "  [WARN] Under-voltage detected -- ${raw}"
        ISSUES+=("under_voltage")
        DO_WARN_ONLY=1
    else
        log "info" "  [OK] Throttle state clean -- ${raw}"
    fi
}

# ---------------------------------------------------------------------------
# Check 3: System services
# ---------------------------------------------------------------------------

check_system_services() {
    log "info" "CHECK system services"
    local any_failed=0

    for svc in "${SYSTEM_SERVICES[@]}"; do
        # 2026-08-21: cloudflared is a --user unit under CTDC_USER (see
        # ~/.config/systemd/user/cloudflared.service), NOT a root/system
        # service like pihole-FTL/tailscaled -- a plain root-scope
        # `systemctl is-active cloudflared.service` can never see it and
        # always reports it down, which alone was enough to trigger an
        # unattended full-stack restart the very first time this timer
        # fired (cloudflared was healthy the whole time). Route it through
        # the same user_ctl() helper the container checks already use.
        if [[ "${svc}" == "cloudflared" ]]; then
            svc_active=0
            user_ctl is-active --quiet "${svc}.service" && svc_active=1
        else
            svc_active=0
            systemctl is-active --quiet "${svc}.service" 2>/dev/null && svc_active=1
        fi
        if (( svc_active )); then
            log "info" "  [OK] ${svc}"
        else
            log "err" "  [FAIL] ${svc} not active"
            ISSUES+=("svc_down:${svc}")
            any_failed=1
        fi
    done

    # DNS readiness target
    if systemctl is-active --quiet "${DNS_TARGET}" 2>/dev/null; then
        log "info" "  [OK] ${DNS_TARGET}"
    else
        log "warn" "  [WARN] ${DNS_TARGET} not active"
        ISSUES+=("dns_target_inactive")
        # DNS target not being active by itself doesn't warrant a full restart
        # -- it should self-resolve. Flag only.
        DO_WARN_ONLY=1
    fi

    (( any_failed )) && DO_RESTART_STACK=1 || true
}

# ---------------------------------------------------------------------------
# Check 4: Container health
# ---------------------------------------------------------------------------

check_containers() {
    log "info" "CHECK containers"

    # 2026-08-23: read the guard's own state once per cycle, not once per
    # container -- cheap, and keeps all containers checked this cycle
    # consistent even if tier flips mid-cycle (it won't in practice, a
    # single 2min-cadence write, but no reason to risk it).
    local guard_tier
    guard_tier=$(_guard_tier)

    for svc in "${CONTAINERS[@]}"; do
        if ! container_exists "${svc}"; then
            log "info" "  [SKIP] ${svc} (no .container file)"
            continue
        fi

        local key="container:${svc}"
        if container_active "${svc}"; then
            log "info" "  [OK] ${svc}"
            reset_streak "${key}"
        elif (( guard_tier > 0 )) && _in_guard_managed "${svc}"; then
            # thermal-ingest-guard.py has this unit deliberately shed
            # (LOCKDOWN or tier-1) -- this is NOT a crash. Reset rather
            # than bump the streak: an in-progress LOCKDOWN can run
            # longer than FAIL_THRESHOLD*90s, and letting the streak
            # keep counting underneath the suppression would just fire
            # the instant tier drops back to 0, defeating the point.
            # Never suppressed for web -- see GUARD_MANAGED_CONTAINERS.
            log "info" "  [OK] ${svc} down but guard tier=${guard_tier} (deliberate shed, not a fault)"
            reset_streak "${key}"
        else
            local streak
            bump_streak "${key}"
            streak="${STREAKS[${key}]}"
            if (( streak >= FAIL_THRESHOLD )); then
                log "err" "  [FAIL] ${svc} not active (streak ${streak}/${FAIL_THRESHOLD} -- restart warranted)"
                ISSUES+=("container_down:${svc}:streak=${streak}")
                add_failed_container "${svc}"
            else
                log "warn" "  [WARN] ${svc} not active (streak ${streak}/${FAIL_THRESHOLD} -- not yet acting)"
                ISSUES+=("container_down_pending:${svc}:streak=${streak}")
                DO_WARN_ONLY=1
            fi
        fi
    done

    (( ${#FAILED_CONTAINERS[@]} > 0 )) && DO_RESTART_CONTAINERS=1 || true
}

# ---------------------------------------------------------------------------
# Check 5: API liveness + feed freshness
# ---------------------------------------------------------------------------

check_api() {
    log "info" "CHECK API liveness"

    local healthz
    healthz=$(api_get "/healthz" || true)

    if [[ -z "${healthz}" ]]; then
        local streak
        bump_streak "api_down"
        streak="${STREAKS[api_down]}"
        if (( streak >= FAIL_THRESHOLD )); then
            log "err" "  [FAIL] /healthz unreachable -- API down or container not responding (streak ${streak}/${FAIL_THRESHOLD} -- restart warranted)"
            ISSUES+=("api_down:streak=${streak}")
            # /healthz is served by web specifically -- only it gets
            # restarted, not the whole CONTAINERS array. A single
            # CURL_TIMEOUT=5 timeout under load is not, by itself,
            # distinguishable from a truly dead process (this is exactly
            # what caused the self-perpetuating 2026-08-22 restart loop --
            # see CLAUDE.md); FAIL_THRESHOLD consecutive misses is the
            # actual signal now.
            add_failed_container "corporatetraveldc-web"
            DO_RESTART_CONTAINERS=1
        else
            log "warn" "  [WARN] /healthz unreachable (streak ${streak}/${FAIL_THRESHOLD} -- not yet acting)"
            ISSUES+=("api_down_pending:streak=${streak}")
            DO_WARN_ONLY=1
        fi
        return  # No point checking feeds
    fi
    reset_streak "api_down"
    log "info" "  [OK] /healthz reachable"

    # Extract snapshot_age if present
    if command -v python3 >/dev/null 2>&1; then
        local snap_age
        snap_age=$(python3 -c "
import json, sys
try:
    d = json.loads('''${healthz}''')
    age = d.get('snapshot_age_seconds') or d.get('snapshot_age')
    print(int(age)) if age is not None else print(-1)
except:
    print(-1)
" 2>/dev/null || echo "-1")
        if (( snap_age > 0 && snap_age > 900 )); then
            log "warn" "  [WARN] Snapshot age ${snap_age}s -- stale"
            ISSUES+=("snapshot_stale:${snap_age}s")
            DO_WARN_ONLY=1
        elif (( snap_age > 0 )); then
            log "info" "  [OK] Snapshot age ${snap_age}s"
        fi
    fi

    log "info" "CHECK feed freshness"

    local feeds_json
    feeds_json=$(api_get "/api/v1/feeds" || true)

    if [[ -z "${feeds_json}" ]]; then
        log "warn" "  [WARN] /api/v1/feeds unreachable"
        ISSUES+=("feeds_endpoint_unreachable")
        return
    fi

    if ! command -v python3 >/dev/null 2>&1; then
        log "warn" "  python3 not found -- skipping feed staleness parse"
        return
    fi

    # Pass feeds JSON and threshold map to Python; print WARN:<name>:<age_s> or CRIT:<name>:<age_s>
    # Use temp file to avoid argument-length limits on large JSON
    local tmpf
    tmpf=$(mktemp /run/ctdc-watchdog-feeds.XXXXXX)
    echo "${feeds_json}" > "${tmpf}"

    local stale_out
    stale_out=$(python3 - "${tmpf}" <<'PYEOF'
import sys, json, time, os

tmpf = sys.argv[1]
now = time.time()

# Per-feed crit thresholds (seconds) -- mirrors FEED_STALE_CRIT in shell
THRESHOLDS = {
    "metar":         900,
    "tfr":           900,
    "nws":           2700,
    "nas":           900,
    "ops_plan":      10800,
    "atcscc_opsplan":10800,
    "amtrak":        900,
    "runsheet":      1800,
}
DEFAULT = 900

try:
    with open(tmpf) as f:
        raw = json.load(f)
except Exception as e:
    sys.exit(0)

# Normalise: API may return {"feeds": {...}}, {"feeds": [...]}, or bare list/dict
feeds = raw
if isinstance(raw, dict):
    feeds = raw.get("feeds", raw)

if isinstance(feeds, dict):
    feeds = list(feeds.values())

if not isinstance(feeds, list):
    sys.exit(0)

for feed in feeds:
    if not isinstance(feed, dict):
        continue

    name = (feed.get("name") or feed.get("feed_name") or feed.get("id") or "unknown").lower()
    last_ts = (feed.get("last_updated") or feed.get("fetched_at")
               or feed.get("updated_at") or feed.get("timestamp"))

    if last_ts is None:
        continue

    try:
        if isinstance(last_ts, (int, float)):
            age = now - float(last_ts)
        else:
            import datetime
            ts = datetime.datetime.fromisoformat(str(last_ts).replace("Z", "+00:00"))
            age = now - ts.timestamp()
    except Exception:
        continue

    if age < 0:
        continue  # clock skew

    threshold = THRESHOLDS.get(name, DEFAULT)
    warn_threshold = threshold * 0.66   # warn at 2/3 of crit threshold

    if age > threshold:
        print(f"CRIT:{name}:{int(age)}")
    elif age > warn_threshold:
        print(f"WARN:{name}:{int(age)}")
PYEOF
    )
    rm -f "${tmpf}"

    local had_stale=0
    while IFS= read -r line; do
        [[ -z "${line}" ]] && continue
        local severity="${line%%:*}"
        local rest="${line#*:}"
        local fname="${rest%%:*}"
        local age_s="${rest##*:}"
        local age_m=$(( age_s / 60 ))

        if [[ "${severity}" == "CRIT" ]]; then
            log "err" "  [STALE] ${fname}: ${age_m}min -- exceeds crit threshold"
            ISSUES+=("feed_stale_crit:${fname}:${age_m}min")
            # 2026-08-21: feed staleness is an UPSTREAM data-source problem
            # (SWIM ingest load-shedding, NWS/eurocontrol/jasdat outages,
            # etc.) -- restarting web/poller/pusher doesn't restart the
            # ingest containers or the upstream feed itself, so it can
            # never fix this. Confirmed live: a container restart
            # triggered by "nws: 1682min stale" completed cleanly and
            # /healthz was STILL degraded on the exact same feeds
            # immediately after. This used to set DO_RESTART_CONTAINERS=1,
            # which -- combined with the 300s cooldown -- meant a single
            # long-stale feed (nws had been stale for over a day) would
            # bounce the whole dispatch stack roughly every 5 minutes,
            # forever, accomplishing nothing but disruption. Alert-only
            # now, same as the WARN tier below; see "Ingest load-shedding"
            # and scripts/thermal-ingest-guard.py for the mechanisms that
            # actually own feed recovery.
            DO_WARN_ONLY=1
            had_stale=1
        else
            log "warn" "  [STALE] ${fname}: ${age_m}min -- approaching threshold"
            ISSUES+=("feed_stale_warn:${fname}:${age_m}min")
            DO_WARN_ONLY=1
            had_stale=1
        fi
    done <<< "${stale_out}"

    (( had_stale == 0 )) && log "info" "  [OK] All feeds within freshness thresholds"
}

# ---------------------------------------------------------------------------
# Restart: containers only (poller hung, single feed stale, etc.)
# ---------------------------------------------------------------------------

restart_containers() {
    # 2026-08-23: only touches FAILED_CONTAINERS (the members that actually
    # crossed FAIL_THRESHOLD), not the full CONTAINERS array -- this used
    # to be an unconditional stop-all/start-all, which is why a healthy
    # `web` got bounced twice today (12:18, 14:34) purely because poller
    # or pusher was down. Relative order within CONTAINERS is still
    # respected (stop in reverse, start in forward order) for whichever
    # subset actually needs it.
    log "info" "RESTART containers (failed only: ${FAILED_CONTAINERS[*]})"

    local i svc
    for (( i=${#CONTAINERS[@]}-1; i>=0; i-- )); do
        svc="${CONTAINERS[$i]}"
        container_exists "${svc}" || continue
        _in_failed_containers "${svc}" || continue
        log "info" "  Stopping ${svc}"
        user_ctl stop "${svc}.service" || true
        sleep 2
    done

    # Brief pause for Podman to release resources
    sleep 3

    # Confirm DNS target before bringing containers up
    _wait_dns_target

    for svc in "${CONTAINERS[@]}"; do
        container_exists "${svc}" || continue
        _in_failed_containers "${svc}" || continue
        log "info" "  Starting ${svc}"
        if ! user_ctl start "${svc}.service"; then
            log "err" "  [FAIL] ${svc} failed to start"
        fi
        sleep "${CONTAINER_WAIT}"
    done

    # Fresh streak count post-restart so a real re-crash still needs its
    # own FAIL_THRESHOLD cycles before acting again, rather than
    # inheriting the count that just triggered this restart.
    for svc in "${FAILED_CONTAINERS[@]}"; do
        reset_streak "container:${svc}"
    done
    reset_streak "api_down"

    log "info" "Container restart sequence complete"
}

_in_failed_containers() {
    local needle="$1" x
    for x in "${FAILED_CONTAINERS[@]:-}"; do
        [[ "${x}" == "${needle}" ]] && return 0
    done
    return 1
}

# ---------------------------------------------------------------------------
# Restart: full stack (thermal, throttle, system service failure)
# ---------------------------------------------------------------------------

restart_full_stack() {
    log "warn" "RESTART full stack"

    # 1. Containers down first (reverse order)
    local i
    for (( i=${#CONTAINERS[@]}-1; i>=0; i-- )); do
        local svc="${CONTAINERS[$i]}"
        container_exists "${svc}" || continue
        log "info" "  Stopping container: ${svc}"
        user_ctl stop "${svc}.service" || true
    done
    sleep 3

    # 2. Reload user daemon (picks up any Quadlet changes on disk)
    log "info" "  Reloading user systemd daemon"
    sudo -u "${CTDC_USER}" \
        XDG_RUNTIME_DIR="${XDG_USER_DIR}" \
        DBUS_SESSION_BUS_ADDRESS="${DBUS_ADDR}" \
        systemctl --user daemon-reload 2>/dev/null || true

    # 3. System service restart order: DNS first, then tunnel, then VPN
    log "info" "  Restarting pihole-FTL"
    systemctl restart pihole-FTL.service 2>/dev/null \
        || log "err" "  [FAIL] pihole-FTL restart"
    sleep 4

    # Unbound if present
    if systemctl list-units --type=service --all --no-pager 2>/dev/null \
            | grep -q "^.*unbound\.service"; then
        log "info" "  Restarting unbound"
        systemctl restart unbound.service 2>/dev/null || true
        sleep 3
    fi

    log "info" "  Restarting cloudflared"
    # --user unit, same reasoning as the check_system_services() fix above.
    user_ctl restart cloudflared.service \
        || log "err" "  [FAIL] cloudflared restart"
    sleep 3

    log "info" "  Restarting tailscaled"
    systemctl restart tailscaled.service 2>/dev/null \
        || log "err" "  [FAIL] tailscaled restart"
    sleep 3

    # 4. Wait for DNS target
    _wait_dns_target

    # 5. Containers back up in order
    for svc in "${CONTAINERS[@]}"; do
        container_exists "${svc}" || continue
        log "info" "  Starting container: ${svc}"
        if ! user_ctl start "${svc}.service"; then
            log "err" "  [FAIL] ${svc} failed to start -- check: journalctl --user -u ${svc}.service"
        fi
        sleep "${CONTAINER_WAIT}"
    done

    mark_cooldown
    log "info" "Full stack restart complete"
}

# ---------------------------------------------------------------------------
# Internal: wait for dns-stack-ready.target
# ---------------------------------------------------------------------------

_wait_dns_target() {
    log "info" "  Waiting for ${DNS_TARGET} (max ${DNS_WAIT_MAX}s)"
    local waited=0
    while ! systemctl is-active --quiet "${DNS_TARGET}" 2>/dev/null; do
        sleep 2
        (( waited += 2 ))
        if (( waited >= DNS_WAIT_MAX )); then
            log "warn" "  ${DNS_TARGET} not ready after ${DNS_WAIT_MAX}s -- proceeding anyway"
            return
        fi
    done
    log "info" "  [OK] ${DNS_TARGET} active after ${waited}s"
}

# ---------------------------------------------------------------------------
# Report + act
# ---------------------------------------------------------------------------

report() {
    local count=${#ISSUES[@]}

    if (( count == 0 )); then
        log "info" "All checks passed -- stack healthy"
        return
    fi

    local summary
    summary=$(IFS=', '; echo "${ISSUES[*]}")
    log "warn" "Issues: ${count} -- ${summary}"

    if (( DO_RESTART_STACK )); then
        if (( ! ALLOW_SYSTEM_RESTART )); then
            # 2026-08-21: system-level restart (pihole-FTL/cloudflared/
            # tailscaled) is never unattended -- see the file header. Alert
            # loudly with the exact command to run; do NOT touch anything.
            log "warn" "Full stack restart warranted but system-level restarts require a manual run -- alerting only"
            ntfy_send "${NTFY_HOT}" \
                "Watchdog -- System Restart Needed (manual run required)" \
                "Issues: ${summary}. This needs a human: sudo ${0} --allow-system-restart" \
                5
        elif in_cooldown; then
            local rem
            rem=$(cooldown_remaining)
            log "warn" "Full stack restart warranted but in cooldown (${rem}s remaining) -- skipping"
            ntfy_send "${NTFY_OPS}" \
                "Watchdog -- Restart Suppressed" \
                "Stack restart warranted but in ${rem}s cooldown. Issues: ${summary}" \
                3
        else
            ntfy_send "${NTFY_HOT}" \
                "Watchdog -- Full Stack Restart" \
                "Initiating full restart (manual run, --allow-system-restart). Issues: ${summary}" \
                4
            restart_full_stack
            ntfy_send "${NTFY_OPS}" \
                "Watchdog -- Stack Restart Complete" \
                "Full restart finished. Issues were: ${summary}" \
                3
        fi

    elif (( DO_RESTART_CONTAINERS )); then
        if in_cooldown; then
            local rem
            rem=$(cooldown_remaining)
            log "warn" "Container restart warranted but in cooldown (${rem}s remaining)"
            ntfy_send "${NTFY_OPS}" \
                "Watchdog -- Restart Suppressed" \
                "Container restart in ${rem}s cooldown. Issues: ${summary}" \
                2
        else
            ntfy_send "${NTFY_OPS}" \
                "Watchdog -- Container Restart" \
                "Restarting containers. Issues: ${summary}" \
                3
            restart_containers
            mark_cooldown
            ntfy_send "${NTFY_OPS}" \
                "Watchdog -- Container Restart Complete" \
                "Containers restarted. Issues were: ${summary}" \
                2
        fi

    elif (( DO_WARN_ONLY )); then
        ntfy_send "${NTFY_OPS}" \
            "Watchdog -- Warning" \
            "Non-critical issues detected: ${summary}" \
            2
    fi
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

main() {
    mkdir -p "${LOG_DIR}"
    check_lock
    load_streaks

    log "info" "-------- Watchdog run start --------"

    check_thermal
    check_throttle
    check_system_services
    check_containers
    check_api
    report

    save_streaks

    log "info" "-------- Watchdog run end ----------"
}

main "$@"
