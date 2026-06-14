#!/bin/bash
# /opt/corporatetraveldc/scripts/restart-stack.sh
# Manual full stack restart -- run as root
# Bypasses watchdog cooldown; use when you know a restart is needed NOW
# Usage: sudo restart-stack.sh [--containers-only] [--dry-run]
# ASCII output only

set -uo pipefail

CTDC_USER="corporatetraveldc"
CTDC_UID=$(id -u "${CTDC_USER}" 2>/dev/null || echo "")
XDG_USER_DIR="/run/user/${CTDC_UID}"
DBUS_ADDR="unix:path=${XDG_USER_DIR}/bus"
QUADLET_DIR="/home/${CTDC_USER}/.config/containers/systemd"
ENV_FILE="/etc/corporatetraveldc/dispatch.env"
COOLDOWN_FILE="/run/corporatetraveldc-watchdog-cooldown"
DNS_TARGET="dns-stack-ready.target"
DNS_WAIT_MAX=60
CONTAINER_WAIT=4

CONTAINERS=(
    "corporatetraveldc-web"
    "corporatetraveldc-poller"
    "corporatetraveldc-pusher"
    "corporatetraveldc-ingest"
)

SYSTEM_SERVICES=(
    "pihole-FTL"
    "cloudflared"
    "tailscaled"
)

CONTAINERS_ONLY=0
DRY_RUN=0

for arg in "$@"; do
    case "${arg}" in
        --containers-only) CONTAINERS_ONLY=1 ;;
        --dry-run)         DRY_RUN=1 ;;
    esac
done

[[ -f "${ENV_FILE}" ]] && source "${ENV_FILE}" 2>/dev/null || true
NTFY_BASE="${NTFY_BASE_URL:-http://127.0.0.1:2586}"
NTFY_OPS="${NTFY_OPS_TOPIC:-ops-health}"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

say() {
    echo "[$(date '+%H:%M:%S')] $*"
}

run() {
    if (( DRY_RUN )); then
        say "  [DRY-RUN] $*"
        return 0
    fi
    "$@"
}

user_ctl() {
    if [[ -z "${CTDC_UID}" ]]; then
        say "ERROR: cannot resolve UID for ${CTDC_USER}"
        return 1
    fi
    run sudo -u "${CTDC_USER}" \
        XDG_RUNTIME_DIR="${XDG_USER_DIR}" \
        DBUS_SESSION_BUS_ADDRESS="${DBUS_ADDR}" \
        systemctl --user "$@" 2>/dev/null
}

container_exists() {
    [[ -f "${QUADLET_DIR}/${1}.container" ]]
}

ntfy_send() {
    local topic="$1" title="$2" msg="$3"
    curl -sf --max-time 5 \
        -H "Title: ${title}" \
        -H "Priority: 3" \
        -d "${msg}" \
        "${NTFY_BASE}/${topic}" >/dev/null 2>&1 || true
}

wait_dns() {
    say "Waiting for ${DNS_TARGET} (max ${DNS_WAIT_MAX}s)"
    local waited=0
    while ! systemctl is-active --quiet "${DNS_TARGET}" 2>/dev/null; do
        sleep 2
        (( waited += 2 ))
        if (( waited >= DNS_WAIT_MAX )); then
            say "  [WARN] ${DNS_TARGET} not ready after ${DNS_WAIT_MAX}s -- proceeding"
            return
        fi
    done
    say "  [OK] ${DNS_TARGET} active (${waited}s)"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if [[ $EUID -ne 0 ]]; then
    echo "ERROR: must run as root (sudo $0)"
    exit 1
fi

say "--------------------------------------"
say "  CorporateTravelDC Stack Restart"
say "  Mode: $(( CONTAINERS_ONLY )) containers-only | $(( DRY_RUN )) dry-run"
say "--------------------------------------"

(( DRY_RUN )) && say "[DRY-RUN MODE -- no changes will be made]"

# Stop containers (reverse)
say "Stopping containers..."
for (( i=${#CONTAINERS[@]}-1; i>=0; i-- )); do
    svc="${CONTAINERS[$i]}"
    container_exists "${svc}" || { say "  [SKIP] ${svc}"; continue; }
    say "  Stopping ${svc}"
    user_ctl stop "${svc}.service" || true
    (( DRY_RUN )) || sleep 2
done
say "  All containers stopped"
(( DRY_RUN )) || sleep 2

if (( ! CONTAINERS_ONLY )); then
    # Reload user daemon
    say "Reloading user systemd daemon..."
    if [[ -n "${CTDC_UID}" ]]; then
        run sudo -u "${CTDC_USER}" \
            XDG_RUNTIME_DIR="${XDG_USER_DIR}" \
            DBUS_SESSION_BUS_ADDRESS="${DBUS_ADDR}" \
            systemctl --user daemon-reload 2>/dev/null || true
    fi

    say "Restarting system services..."

    say "  pihole-FTL"
    run systemctl restart pihole-FTL.service 2>/dev/null \
        || say "  [FAIL] pihole-FTL"
    (( DRY_RUN )) || sleep 4

    # Unbound if installed
    if systemctl list-units --type=service --all --no-pager 2>/dev/null \
            | grep -q "^.*unbound\.service"; then
        say "  unbound"
        run systemctl restart unbound.service 2>/dev/null || true
        (( DRY_RUN )) || sleep 3
    fi

    say "  cloudflared"
    run systemctl restart cloudflared.service 2>/dev/null \
        || say "  [FAIL] cloudflared"
    (( DRY_RUN )) || sleep 3

    say "  tailscaled"
    run systemctl restart tailscaled.service 2>/dev/null \
        || say "  [FAIL] tailscaled"
    (( DRY_RUN )) || sleep 3

    (( DRY_RUN )) || wait_dns
fi

say "Starting containers..."
for svc in "${CONTAINERS[@]}"; do
    container_exists "${svc}" || { say "  [SKIP] ${svc}"; continue; }
    say "  Starting ${svc}"
    if ! user_ctl start "${svc}.service"; then
        say "  [FAIL] ${svc} -- check: journalctl --user -u ${svc}.service -n 30"
    fi
    (( DRY_RUN )) || sleep "${CONTAINER_WAIT}"
done

# Reset watchdog cooldown so it picks up fresh state on next cycle
(( DRY_RUN )) || rm -f "${COOLDOWN_FILE}"
say "  Watchdog cooldown cleared"

say ""
say "Verifying..."
(( DRY_RUN )) || sleep 6

# Quick status summary
for svc in "${CONTAINERS[@]}"; do
    container_exists "${svc}" || continue
    if (( DRY_RUN )); then
        say "  [DRY-RUN] ${svc} status skipped"
    elif sudo -u "${CTDC_USER}" \
            XDG_RUNTIME_DIR="${XDG_USER_DIR}" \
            DBUS_SESSION_BUS_ADDRESS="${DBUS_ADDR}" \
            systemctl --user is-active --quiet "${svc}.service" 2>/dev/null; then
        say "  [OK] ${svc}"
    else
        say "  [FAIL] ${svc} -- check journalctl"
    fi
done

# Quick API ping
if (( ! DRY_RUN )); then
    if curl -sf --max-time 5 "http://127.0.0.1:8000/healthz" >/dev/null 2>&1; then
        say "  [OK] API /healthz responding"
        ntfy_send "${NTFY_OPS}" \
            "Stack Restart Complete" \
            "Manual restart finished -- API responding."
    else
        say "  [WARN] API /healthz not responding yet -- may still be starting"
        ntfy_send "${NTFY_OPS}" \
            "Stack Restart -- API Pending" \
            "Manual restart finished -- API not yet responding."
    fi
fi

say ""
say "Done."
