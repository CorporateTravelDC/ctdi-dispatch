#!/bin/bash
# /opt/corporatetraveldc/scripts/watchdog.sh
# Dispatch stack health watchdog
# Runs as root via systemd timer every 90s
# Monitors: thermals, throttle, system services, containers, API liveness, feed freshness
# ASCII output only -- no Unicode symbols

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

# Ordered container list -- start in this order, stop in reverse
CONTAINERS=(
    "corporatetraveldc-web"
    "corporatetraveldc-poller"
    "corporatetraveldc-pusher"
    "corporatetraveldc-ingest"
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
        if systemctl is-active --quiet "${svc}.service" 2>/dev/null; then
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
    local any_failed=0

    for svc in "${CONTAINERS[@]}"; do
        if ! container_exists "${svc}"; then
            log "info" "  [SKIP] ${svc} (no .container file)"
            continue
        fi

        if container_active "${svc}"; then
            log "info" "  [OK] ${svc}"
        else
            log "err" "  [FAIL] ${svc} not active"
            ISSUES+=("container_down:${svc}")
            any_failed=1
        fi
    done

    (( any_failed )) && DO_RESTART_CONTAINERS=1 || true
}

# ---------------------------------------------------------------------------
# Check 5: API liveness + feed freshness
# ---------------------------------------------------------------------------

check_api() {
    log "info" "CHECK API liveness"

    local healthz
    healthz=$(api_get "/healthz" || true)

    if [[ -z "${healthz}" ]]; then
        log "err" "  [FAIL] /healthz unreachable -- API down or container not responding"
        ISSUES+=("api_down")
        DO_RESTART_CONTAINERS=1
        return  # No point checking feeds
    fi
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
            DO_RESTART_CONTAINERS=1
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
    log "info" "RESTART containers (ordered)"

    # Stop in reverse order
    local i
    for (( i=${#CONTAINERS[@]}-1; i>=0; i-- )); do
        local svc="${CONTAINERS[$i]}"
        container_exists "${svc}" || continue
        log "info" "  Stopping ${svc}"
        user_ctl stop "${svc}.service" || true
        sleep 2
    done

    # Brief pause for Podman to release resources
    sleep 3

    # Confirm DNS target before bringing containers up
    _wait_dns_target

    # Start in order
    for svc in "${CONTAINERS[@]}"; do
        container_exists "${svc}" || continue
        log "info" "  Starting ${svc}"
        if ! user_ctl start "${svc}.service"; then
            log "err" "  [FAIL] ${svc} failed to start"
        fi
        sleep "${CONTAINER_WAIT}"
    done

    log "info" "Container restart sequence complete"
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
    systemctl restart cloudflared.service 2>/dev/null \
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
        if in_cooldown; then
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
                "Initiating full restart. Issues: ${summary}" \
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

    log "info" "-------- Watchdog run start --------"

    check_thermal
    check_throttle
    check_system_services
    check_containers
    check_api
    report

    log "info" "-------- Watchdog run end ----------"
}

main "$@"
