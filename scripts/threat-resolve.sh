#!/bin/bash
# /opt/corporatetraveldc/scripts/threat-resolve.sh
# Reverses threat-initiate.sh -- lifts a manual firewall ban (if one was
# made) and restores host-reach opt-ins via restore-network.sh.
#
# Usage: sudo threat-resolve.sh [--ip BANNED_IP] [--reason TEXT] [--dry-run]
# ASCII output only

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="/etc/corporatetraveldc/dispatch.env"
STATE_DIR="/run/corporatetraveldc-threat-response"

IP=""
REASON="manual operator resolution"
DRY_RUN=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --ip)      IP="${2:-}"; shift 2 ;;
        --reason)  REASON="${2:-manual operator resolution}"; shift 2 ;;
        --dry-run) DRY_RUN=1; shift ;;
        *)         shift ;;
    esac
done

# If no --ip given, fall back to whatever threat-initiate.sh recorded
if [[ -z "${IP}" && -f "${STATE_DIR}/banned-ip" ]]; then
    IP=$(cat "${STATE_DIR}/banned-ip" 2>/dev/null || echo "")
fi

[[ -f "${ENV_FILE}" ]] && source "${ENV_FILE}" 2>/dev/null || true
NTFY_BASE="${NTFY_BASE_URL:-http://127.0.0.1:2586}"
NTFY_OPS="${NTFY_OPS_TOPIC:-ops-health}"

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

ntfy_send() {
    local topic="$1" title="$2" msg="$3"
    curl -sf --max-time 5 \
        -H "Title: ${title}" \
        -H "Priority: 3" \
        -d "${msg}" \
        "${NTFY_BASE}/${topic}" >/dev/null 2>&1 || true
}

if [[ $EUID -ne 0 ]]; then
    echo "ERROR: must run as root (sudo $0)"
    exit 1
fi

say "--------------------------------------"
say "  CorporateTravelDC MANUAL Threat Resolve"
say "  Reason: ${REASON}"
[[ -n "${IP}" ]] && say "  Lifting ban: ${IP}"
say "--------------------------------------"

(( DRY_RUN )) && say "[DRY-RUN MODE -- no changes will be made]"

if [[ -n "${IP}" ]]; then
    say "Removing firewalld rich rule for ${IP}..."
    run firewall-cmd --remove-rich-rule="rule family=\"ipv4\" source address=\"${IP}\" reject" 2>&1
    (( DRY_RUN )) || rm -f "${STATE_DIR}/banned-ip"
    say "  [OK] ${IP} unbanned"
else
    say "[SKIP] No IP to unban (none given, none on record from threat-initiate.sh)"
fi

say ""
say "Restoring host-reach opt-ins via restore-network.sh..."
bash "${SCRIPT_DIR}/restore-network.sh" --reason "MANUAL: ${REASON}" $([[ ${DRY_RUN} -eq 1 ]] && echo --dry-run)

say ""
say "Manual threat response resolved."

if (( ! DRY_RUN )); then
    ntfy_send "${NTFY_OPS}" \
        "MANUAL Threat Response Resolved" \
        "Operator-initiated resolution. Reason: ${REASON}.$([[ -n "${IP}" ]] && echo " Unbanned: ${IP}.") Host-reach opt-ins restored."
fi

say "Done."
