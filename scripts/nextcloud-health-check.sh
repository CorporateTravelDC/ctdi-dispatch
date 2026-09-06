#!/bin/bash
# scripts/nextcloud-health-check.sh
# Health check for the Nextcloud "second brain" instance -- wired into the
# same ntfy ops-health topic used by container-mem-watch.sh, following the
# same script conventions established during the 2026-07-19/20 ingest
# mitigation work.
#
# container-mem-watch.sh already covers nextcloud-app/nextcloud-db for
# sustained-memory-pressure and OOM alerts (it watches every live container
# dynamically via `podman stats`, no hardcoded list). What that does NOT
# cover: whether Nextcloud is actually responding to requests, whether it's
# stuck in maintenance mode, or whether its background cron job (which runs
# ntfy-app-disable state, calendar reminders, etc.) has silently stopped
# firing. This script closes that gap.
#
# Checks, in order:
#   1. occ status -- installed=true, maintenance=false, no pending DB upgrade
#   2. LOCAL status.php -- http://127.0.0.1:8090/status.php, the container's
#      published port directly, no DNS/WAN/Cloudflare/nginx in the path at all.
#   3. OUTBOUND status.php -- https://cloud.example.com/status.php,
#      the same path a real external client uses (out via this Pi's own WAN
#      egress, through Cloudflare's edge, back through the tunnel).
#   4. Cron freshness -- Nextcloud stamps its own last-cron-run timestamp;
#      cron.timer runs every 5min, so anything older than 15min means cron
#      itself has stopped, even though the container looks "up".
#
# UPDATED 2026-07-26 per operator: checks 2 and 3 used to be a single
# outbound-only check, which made every failure look like "Nextcloud might be
# down" even when the real cause was this Pi's own WiFi/gateway congestion
# (see docs -- same root cause diagnosed for Tailscale DERP latency the same
# day) or a genuine tunnel-side hiccup. Splitting local vs outbound lets the
# alert say definitively which one it is:
#   local FAIL + outbound FAIL  -> Nextcloud itself is actually down
#   local OK   + outbound FAIL  -> network/tunnel path problem, Nextcloud is fine
#   local FAIL + outbound OK    -> inconsistent/transient, flagged as such
#   both OK                     -> clean, no alert
#
# Usage:
#   nextcloud-health-check.sh          # normal run (called by the timer)
#   nextcloud-health-check.sh --status # print current state, no ntfy
#
# ASCII output only -- no Unicode symbols (repo convention).

set -uo pipefail

STATE_DIR="/var/lib/corporatetraveldc/nextcloud-health-check"
LOG_FILE="${STATE_DIR}/health.log"
ENV_FILE="/etc/corporatetraveldc/dispatch.env"
SECRETS_FILE="/etc/corporatetraveldc/dispatch-secrets.env"
NEXTCLOUD_LOCAL_URL="http://127.0.0.1:8090"
NEXTCLOUD_PUBLIC_URL="https://cloud.example.com"
LOCAL_TIMEOUT=5      # loopback -- should be near-instant if the container is alive
OUTBOUND_TIMEOUT=8   # real round trip out to Cloudflare and back
CRON_STALE_SECS=900  # 15min -- 3x the 5min cron cadence before we call it stale

mkdir -p "${STATE_DIR}"

# Same reasoning as scheduled-ingest-restart.sh: don't `source` dispatch.env,
# it's a podman --env-file (plain KEY=VALUE), not bash-safe to source.
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

MODE="run"
[[ "${1:-}" == "--status" ]] && MODE="status"

log() {
    local level="$1"; shift
    local ts
    ts=$(date '+%Y-%m-%d %H:%M:%S')
    echo "[${ts}] [${level^^}] $*" >> "${LOG_FILE}" 2>/dev/null
    echo "[${ts}] [${level^^}] $*"
}

ntfy_send() {
    local title="$1" msg="$2" priority="${3:-2}"
    local auth_args=()
    [[ -n "${NTFY_TOKEN}" ]] && auth_args=(-H "Authorization: Bearer ${NTFY_TOKEN}")
    curl -sf --max-time 5 \
        "${auth_args[@]}" \
        -H "Title: ${title}" \
        -H "Priority: ${priority}" \
        -H "Tags: cloud" \
        -d "${msg}" \
        "${NTFY_BASE}/${NTFY_OPS}" >/dev/null 2>&1 || log "warn" "ntfy_send failed (token_set=$([[ -n \"${NTFY_TOKEN}\" ]] && echo yes || echo no))"
}

problems=()

# --- 1. occ status ---------------------------------------------------------
occ_out=$(podman exec --user www-data nextcloud-app php /var/www/html/occ status 2>&1)
occ_rc=$?
if [[ ${occ_rc} -ne 0 ]]; then
    problems+=("occ status failed to run (rc=${occ_rc}) -- container may be down")
else
    if ! grep -q "installed: true" <<< "${occ_out}"; then
        problems+=("occ reports not installed")
    fi
    if grep -q "maintenance: true" <<< "${occ_out}"; then
        problems+=("stuck in maintenance mode")
    fi
    if grep -q "needsDbUpgrade: true" <<< "${occ_out}"; then
        problems+=("pending DB upgrade not applied")
    fi
fi

# --- 2. LOCAL reachability (container's published port, no WAN/tunnel) -----
local_http_code=$(curl -s -o /dev/null -w "%{http_code}" --max-time "${LOCAL_TIMEOUT}" "${NEXTCLOUD_LOCAL_URL}/status.php" 2>/dev/null)
local_ok=1
if [[ "${local_http_code}" != "200" ]]; then
    local_ok=0
fi

# --- 3. OUTBOUND reachability (same path a real external client takes) ----
outbound_http_code=$(curl -s -o /dev/null -w "%{http_code}" --max-time "${OUTBOUND_TIMEOUT}" "${NEXTCLOUD_PUBLIC_URL}/status.php" 2>/dev/null)
outbound_ok=1
if [[ "${outbound_http_code}" != "200" ]]; then
    outbound_ok=0
fi

# Diagnose local vs outbound so the alert says WHICH kind of failure this is,
# instead of a single ambiguous "status.php didn't return 200".
if (( local_ok == 0 && outbound_ok == 0 )); then
    problems+=("Nextcloud appears DOWN -- local ${local_http_code:-no response} AND outbound ${outbound_http_code:-no response} both failed")
elif (( local_ok == 1 && outbound_ok == 0 )); then
    problems+=("TUNNEL/NETWORK issue, not Nextcloud -- local check OK (200) but outbound failed (${outbound_http_code:-no response}). Likely this Pi's own WAN egress/WiFi congestion or a Cloudflare tunnel hiccup, not the app.")
elif (( local_ok == 0 && outbound_ok == 1 )); then
    problems+=("INCONSISTENT -- local check failed (${local_http_code:-no response}) but outbound succeeded (200). Possibly a transient local blip during the check; recheck if this repeats.")
fi
# both ok -> no entry, nothing to report for reachability

# --- 4. Cron freshness -------------------------------------------------------
lastcron=$(podman exec --user www-data nextcloud-app php /var/www/html/occ config:app:get core lastcron 2>/dev/null | tr -d '[:space:]')
now_epoch=$(date +%s)
if [[ -z "${lastcron}" || ! "${lastcron}" =~ ^[0-9]+$ ]]; then
    problems+=("could not read core.lastcron (got '${lastcron}')")
else
    age=$(( now_epoch - lastcron ))
    if (( age > CRON_STALE_SECS )); then
        problems+=("cron stale: last run ${age}s ago (threshold ${CRON_STALE_SECS}s)")
    fi
fi

if [[ "${MODE}" == "status" ]]; then
    echo "nextcloud local url:  ${NEXTCLOUD_LOCAL_URL}"
    echo "nextcloud public url: ${NEXTCLOUD_PUBLIC_URL}"
    echo "occ status rc:        ${occ_rc}"
    echo "local http code:      ${local_http_code:-none}"
    echo "outbound http code:   ${outbound_http_code:-none}"
    echo "cron last run:        ${lastcron:-unknown} ($(( now_epoch - ${lastcron:-now_epoch} ))s ago)"
    if (( ${#problems[@]} == 0 )); then
        echo "result:               clean"
    else
        echo "result:               PROBLEMS FOUND"
        printf '  - %s\n' "${problems[@]}"
    fi
    exit 0
fi

if (( ${#problems[@]} == 0 )); then
    log "info" "clean (occ ok, local 200, outbound 200, cron fresh)"
    exit 0
fi

log "warn" "problems found: ${problems[*]}"
ntfy_send "Nextcloud health check FAILED" \
    "$(printf '%s; ' "${problems[@]}")" \
    4
