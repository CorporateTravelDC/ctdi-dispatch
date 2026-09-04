#!/bin/bash
# /opt/corporatetraveldc/scripts/threat-initiate.sh
# Manual equivalent of the automated fail2ban->lockdown trigger
# (fail2ban/action.d/corporatetraveldc-lockdown.conf), for an operator who
# judges there's a threat without waiting for (or instead of relying on)
# rate-based detection. The automated path currently only reverts
# host-reach opt-ins (see scripts/lockdown.sh) -- it does not ban an IP
# itself, fail2ban's own actionban does that separately. This script
# replicates BOTH halves manually: optionally ban a specific IP at the
# firewall, and always revert host-reach opt-ins via lockdown.sh.
#
# Usage: sudo threat-initiate.sh [--ip SUSPECTED_IP] [--reason TEXT] [--dry-run]
# ASCII output only

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="/etc/corporatetraveldc/dispatch.env"
STATE_DIR="/run/corporatetraveldc-threat-response"

IP=""
REASON="manual operator judgment"
DRY_RUN=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --ip)      IP="${2:-}"; shift 2 ;;
        --reason)  REASON="${2:-manual operator judgment}"; shift 2 ;;
        --dry-run) DRY_RUN=1; shift ;;
        *)         shift ;;
    esac
done

[[ -f "${ENV_FILE}" ]] && source "${ENV_FILE}" 2>/dev/null || true
NTFY_BASE="${NTFY_BASE_URL:-http://127.0.0.1:2586}"
NTFY_HOT="${NTFY_HOT_TOPIC:-hot-alerts}"

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
        -H "Priority: 5" \
        -d "${msg}" \
        "${NTFY_BASE}/${topic}" >/dev/null 2>&1 || true
}

if [[ $EUID -ne 0 ]]; then
    echo "ERROR: must run as root (sudo $0)"
    exit 1
fi

say "--------------------------------------"
say "  CorporateTravelDC MANUAL Threat Response"
say "  Reason: ${REASON}"
[[ -n "${IP}" ]] && say "  Suspected IP: ${IP}"
say "--------------------------------------"

(( DRY_RUN )) && say "[DRY-RUN MODE -- no changes will be made]"

if [[ -n "${IP}" ]]; then
    say "Banning ${IP} via firewalld rich rule..."
    run mkdir -p "${STATE_DIR}"
    run firewall-cmd --add-rich-rule="rule family=\"ipv4\" source address=\"${IP}\" reject" 2>&1
    (( DRY_RUN )) || echo "${IP}" > "${STATE_DIR}/banned-ip"
    say "  [OK] ${IP} banned (runtime only -- add --permanent manually if this should survive a firewalld reload)"
else
    say "[SKIP] No --ip given -- not banning any address, host-reach revert only"
fi

say ""
say "Reverting host-reach opt-ins via lockdown.sh..."
bash "${SCRIPT_DIR}/lockdown.sh" --reason "MANUAL: ${REASON}" $([[ ${DRY_RUN} -eq 1 ]] && echo --dry-run)

say ""
say "Manual threat response engaged."
say "Resolve with: sudo threat-resolve.sh$([[ -n "${IP}" ]] && echo " --ip ${IP}")"

if (( ! DRY_RUN )); then
    ntfy_send "${NTFY_HOT}" \
        "MANUAL Threat Response Engaged" \
        "Operator-initiated (not automated). Reason: ${REASON}.$([[ -n "${IP}" ]] && echo " Banned: ${IP}.") Host-reach opt-ins reverted. Resolve with threat-resolve.sh."
fi

say "Done."
