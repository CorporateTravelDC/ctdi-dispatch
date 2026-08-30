#!/bin/bash
# /opt/corporatetraveldc/scripts/lockdown.sh
# Revert the host-reach opt-ins for corporatetraveldc-pusher and
# corporatetraveldc-acarshub, without stopping any container or the
# Cloudflare tunnel. Narrow, fast, reversible -- see restore-network.sh.
#
# Not a full stack panic button: containers keep running, they just lose the
# ability to reach host-bound services (ntfy, ultrafeeder) until
# restore-network.sh runs. Meant to be triggered automatically by fail2ban
# (see jail.d/nginx-limit-req-corporatetraveldc.conf's actionban) as well as
# manually.
#
# 2026-08-30: the Ollama bind-revert step this script used to carry was
# removed -- ollama.service is retired (2026-08-27 llama.cpp cutover) and
# the public ollama.* vhost it existed to cut is gone. See
# restore-network.sh for the matching removal.
#
# Usage: sudo lockdown.sh [--dry-run] [--reason TEXT]
# ASCII output only

set -uo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SELF_DIR}/.." && pwd)"
if ! "${REPO_ROOT}/scripts/verify-manifest.sh" "scripts/lockdown.sh"; then
    echo "lockdown: INTEGRITY CHECK FAILED -- refusing to run" >&2
    exit 1
fi

CTDC_USER="corporatetraveldc"
CTDC_UID=$(id -u "${CTDC_USER}" 2>/dev/null || echo "")
XDG_USER_DIR="/run/user/${CTDC_UID}"
DBUS_ADDR="unix:path=${XDG_USER_DIR}/bus"
QUADLET_DIR="/home/${CTDC_USER}/.config/containers/systemd"
CONTAINERS_CONF="/home/${CTDC_USER}/.config/containers/containers.conf"
ENV_FILE="/etc/corporatetraveldc/dispatch.env"
STATE_FILE="/run/corporatetraveldc-lockdown-active"

PASTA_MAPGW_CONTAINERS=(
    "corporatetraveldc-pusher"
)

ACARS_NET_CONTAINERS=(
    "corporatetraveldc-acarsrouter"
    "corporatetraveldc-dumpvdl2"
    "corporatetraveldc-acars-watcher"
    "corporatetraveldc-acarshub"
)

DRY_RUN=0
REASON="manual"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run) DRY_RUN=1; shift ;;
        --reason)  REASON="${2:-manual}"; shift 2 ;;
        *)         shift ;;
    esac
done

[[ -f "${ENV_FILE}" ]] && source "${ENV_FILE}" 2>/dev/null || true
NTFY_BASE="${NTFY_BASE_URL:-http://127.0.0.1:2586}"
NTFY_HOT="${NTFY_HOT_TOPIC:-hot-alerts}"

# ---------------------------------------------------------------------------
# Helpers -- same conventions as restart-stack.sh / watchdog.sh
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

ntfy_send() {
    local topic="$1" title="$2" msg="$3"
    curl -sf --max-time 5 \
        -H "Title: ${title}" \
        -H "Priority: 4" \
        -d "${msg}" \
        "${NTFY_BASE}/${topic}" >/dev/null 2>&1 || true
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if [[ $EUID -ne 0 ]]; then
    echo "ERROR: must run as root (sudo $0)"
    exit 1
fi

say "--------------------------------------"
say "  CorporateTravelDC Lockdown"
say "  Reason: ${REASON}"
say "--------------------------------------"

(( DRY_RUN )) && say "[DRY-RUN MODE -- no changes will be made]"

if [[ -f "${STATE_FILE}" ]]; then
    say "[SKIP] Already locked down since $(cat "${STATE_FILE}" 2>/dev/null) -- idempotent no-op"
    exit 0
fi

# Step 2 -- comment out Network=pasta:--map-gw on the pasta-mode containers
# that reach a 0.0.0.0-bound host service via host.containers.internal.
# Marker prefix lets restore-network.sh find and reverse exactly this line.
say "Reverting pasta-mode host-reach opt-ins..."
for svc in "${PASTA_MAPGW_CONTAINERS[@]}"; do
    quadlet="${QUADLET_DIR}/${svc}.container"
    if [[ ! -f "${quadlet}" ]]; then
        say "  [SKIP] ${svc} -- quadlet not found"
        continue
    fi
    if grep -q "^Network=pasta:--map-gw" "${quadlet}"; then
        say "  ${svc}: commenting out Network=pasta:--map-gw"
        run sed -i 's/^Network=pasta:--map-gw/# LOCKDOWN-DISABLED: Network=pasta:--map-gw/' "${quadlet}"
        run user_ctl daemon-reload
        run user_ctl restart "${svc}.service"
    else
        say "  [SKIP] ${svc} -- opt-in already absent"
    fi
done

# Step 3 -- revert the host-wide bridge-mode map-gw setting and cycle the
# acars-net containers so the shared rootless-netns pasta process picks up
# the reverted containers.conf. Anchored to line start so this can't touch
# the descriptive comment above the same key in that file.
say "Reverting bridge-mode (acars-net) host-reach opt-in..."
if [[ -f "${CONTAINERS_CONF}" ]] && grep -q '^pasta_options = \["--map-gw"\]' "${CONTAINERS_CONF}"; then
    say "  containers.conf: pasta_options -> []"
    run sed -i 's/^pasta_options = \["--map-gw"\]/pasta_options = []/' "${CONTAINERS_CONF}"
    say "  Cycling acars-net containers to release the shared rootless-netns..."
    for svc in "${ACARS_NET_CONTAINERS[@]}"; do
        run user_ctl stop "${svc}.service"
    done
    (( DRY_RUN )) || sleep 2
    for svc in "${ACARS_NET_CONTAINERS[@]}"; do
        run user_ctl start "${svc}.service"
    done
else
    say "  [SKIP] containers.conf -- opt-in already absent or file not found"
fi

(( DRY_RUN )) || date '+%Y-%m-%d %H:%M:%S' > "${STATE_FILE}"

say ""
say "Locked down. Containers are still running; host-reach is cut."
say "Restore with: sudo restore-network.sh"

if (( ! DRY_RUN )); then
    ntfy_send "${NTFY_HOT}" \
        "Stack Lockdown Engaged" \
        "Host-reach opt-ins reverted (pusher, acarshub). Reason: ${REASON}. Containers still running. Restore with restore-network.sh."
fi

say "Done."
