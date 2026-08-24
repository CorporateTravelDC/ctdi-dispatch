#!/bin/bash
# /opt/corporatetraveldc/scripts/restore-network.sh
# Reverses lockdown.sh -- restores the host-reach opt-ins for Ollama,
# corporatetraveldc-pusher, and corporatetraveldc-acarshub.
#
# Usage: sudo restore-network.sh [--dry-run] [--reason TEXT]
# ASCII output only

set -uo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SELF_DIR}/.." && pwd)"
if ! "${REPO_ROOT}/scripts/verify-manifest.sh" "scripts/restore-network.sh"; then
    echo "restore-network: INTEGRITY CHECK FAILED -- refusing to run" >&2
    exit 1
fi

CTDC_USER="corporatetraveldc"
CTDC_UID=$(id -u "${CTDC_USER}" 2>/dev/null || echo "")
XDG_USER_DIR="/run/user/${CTDC_UID}"
DBUS_ADDR="unix:path=${XDG_USER_DIR}/bus"
QUADLET_DIR="/home/${CTDC_USER}/.config/containers/systemd"
CONTAINERS_CONF="/home/${CTDC_USER}/.config/containers/containers.conf"
OLLAMA_BINDING_CONF="/etc/systemd/system/ollama.service.d/10-binding.conf"
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
NTFY_OPS="${NTFY_OPS_TOPIC:-ops-health}"

# ---------------------------------------------------------------------------
# Helpers -- same conventions as restart-stack.sh / watchdog.sh / lockdown.sh
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
        -H "Priority: 3" \
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
say "  CorporateTravelDC Restore Network"
say "  Reason: ${REASON}"
say "--------------------------------------"

(( DRY_RUN )) && say "[DRY-RUN MODE -- no changes will be made]"

if [[ ! -f "${STATE_FILE}" ]]; then
    say "[SKIP] Not currently locked down -- idempotent no-op"
    exit 0
fi

say "Restoring Ollama's bind..."
if [[ -f "${OLLAMA_BINDING_CONF}" ]] && grep -q '^Environment="OLLAMA_HOST=127\.0\.0\.1:11434"' "${OLLAMA_BINDING_CONF}"; then
    say "  ollama: OLLAMA_HOST -> 100.x.x.x:11434"
    run sed -i 's/^Environment="OLLAMA_HOST=127\.0\.0\.1:11434"/Environment="OLLAMA_HOST=100.x.x.x:11434"/' "${OLLAMA_BINDING_CONF}"
    run systemctl daemon-reload
    run systemctl restart ollama.service
else
    say "  [SKIP] ollama -- not currently locked down or binding conf not found"
fi

say "Restoring pasta-mode host-reach opt-ins..."
for svc in "${PASTA_MAPGW_CONTAINERS[@]}"; do
    quadlet="${QUADLET_DIR}/${svc}.container"
    if [[ ! -f "${quadlet}" ]]; then
        say "  [SKIP] ${svc} -- quadlet not found"
        continue
    fi
    if grep -q "^# LOCKDOWN-DISABLED: Network=pasta:--map-gw" "${quadlet}"; then
        say "  ${svc}: restoring Network=pasta:--map-gw"
        run sed -i 's/^# LOCKDOWN-DISABLED: Network=pasta:--map-gw/Network=pasta:--map-gw/' "${quadlet}"
        run user_ctl daemon-reload
        run user_ctl restart "${svc}.service"
    else
        say "  [SKIP] ${svc} -- not currently locked down"
    fi
done

say "Restoring bridge-mode (acars-net) host-reach opt-in..."
if [[ -f "${CONTAINERS_CONF}" ]] && grep -q '^pasta_options = \[\]' "${CONTAINERS_CONF}"; then
    say "  containers.conf: pasta_options -> [\"--map-gw\"]"
    run sed -i 's/^pasta_options = \[\]/pasta_options = ["--map-gw"]/' "${CONTAINERS_CONF}"
    say "  Cycling acars-net containers to release the shared rootless-netns..."
    for svc in "${ACARS_NET_CONTAINERS[@]}"; do
        run user_ctl stop "${svc}.service"
    done
    (( DRY_RUN )) || sleep 2
    for svc in "${ACARS_NET_CONTAINERS[@]}"; do
        run user_ctl start "${svc}.service"
    done
else
    say "  [SKIP] containers.conf -- already restored or file not found"
fi

(( DRY_RUN )) || rm -f "${STATE_FILE}"

say ""
say "Restored. Host-reach opt-ins are back in place."

if (( ! DRY_RUN )); then
    ntfy_send "${NTFY_OPS}" \
        "Stack Lockdown Lifted" \
        "Host-reach opt-ins restored (ollama, pusher, acarshub). Reason: ${REASON}."
fi

say "Done."
