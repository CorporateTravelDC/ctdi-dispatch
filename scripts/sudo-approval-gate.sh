#!/usr/bin/env bash
# scripts/sudo-approval-gate.sh
#
# Request-and-execute wrapper for the two approval-gated sudo grants
# (ollama.service start/stop/restart, dnf remove/autoremove). See
# SUDO_JUSTIFICATION_PROPOSAL.md for the design this implements.
#
# Usage:
#   sudo-approval-gate.sh <command_pattern> <reasoning> -- <actual sudo command...>
#
# Example:
#   sudo-approval-gate.sh "systemctl-restart-ollama" \
#     "inference engine wedged after a governor pause, journalctl shows no \
#      progress in 10 min" \
#     -- sudo systemctl restart ollama.service
#
# Behavior: creates a pending approval request via the local admin API,
# pushes an ntfy alert with Allow/Deny action buttons (resolve URL points at
# the Cloudflare-tunnel hostname so it works whether or not the phone has
# Tailscale active), polls for resolution up to the request's TTL, and only
# runs the actual command on an explicit "allowed". Denied, expired, or
# never-resolved all result in NOT running the command -- silence is never
# consent. Reports the recent-approval count for this pattern at the end so
# a human can eyeball whether it's a frequency-promotion candidate (>2 in
# 7 days -- see /admin/approval-requests?command_pattern=...).

set -euo pipefail

if [[ $# -lt 4 || "${3:-}" != "--" ]]; then
    echo "usage: $0 <command_pattern> <reasoning> -- <command...>" >&2
    exit 2
fi

PATTERN="$1"
REASON="$2"
shift 3
CMD=("$@")
CMD_STR="$(printf '%q ' "${CMD[@]}")"

BASE_URL="http://127.0.0.1:8000"
RESOLVE_HOST="https://dispatch.example.com"
TTL_SECONDS=600
POLL_INTERVAL=5

TOKEN_RAW=$(grep -m1 '^DISPATCH_ADMIN_TOKEN=' /etc/corporatetraveldc/dispatch-secrets.env 2>/dev/null | cut -d= -f2- || true)
ADMIN_TOKEN="${TOKEN_RAW}"
if [[ -z "$ADMIN_TOKEN" ]]; then
    echo "ERROR: could not read admin token from dispatch-secrets.env" >&2
    exit 1
fi

echo "[approval-gate] creating request for pattern=${PATTERN}"
CREATE_RESP=$(curl -s -X POST "${BASE_URL}/admin/approval-requests" \
    -H "Authorization: Bearer ${ADMIN_TOKEN}" \
    -H "Content-Type: application/json" \
    -d "$(python3 -c "import json,sys; print(json.dumps({'command_pattern': sys.argv[1], 'command': sys.argv[2], 'reasoning': sys.argv[3], 'ttl_seconds': float(sys.argv[4])}))" "$PATTERN" "$CMD_STR" "$REASON" "$TTL_SECONDS")")

REQUEST_ID=$(python3 -c "import json,sys; print(json.load(sys.stdin)['id'])" <<< "$CREATE_RESP")
echo "[approval-gate] request_id=${REQUEST_ID}"

NTFY_TOKEN_RAW=$(grep -m1 '^NTFY_TOKEN=' /etc/corporatetraveldc/dispatch-secrets.env | cut -d= -f2-)
NTFY_BEARER="${NTFY_TOKEN_RAW%%:*}"

ALLOW_URL="${RESOLVE_HOST}/admin/approval-requests/${REQUEST_ID}/resolve?action=allow"
DENY_URL="${RESOLVE_HOST}/admin/approval-requests/${REQUEST_ID}/resolve?action=deny"

PAYLOAD=$(python3 - "$REQUEST_ID" "$PATTERN" "$CMD_STR" "$REASON" "$ALLOW_URL" "$DENY_URL" << 'PYEOF'
import json, sys
req_id, pattern, cmd, reason, allow_url, deny_url = sys.argv[1:7]
print(json.dumps({
    "topic": "approval-gate",
    "title": f"Approval needed: {pattern}",
    "message": f"{cmd}\n\nreason: {reason}\n\nexpires in 10 min. no tap = denied.",
    "priority": 4,
    "actions": [
        {"action": "http", "label": "Allow", "url": allow_url, "method": "GET", "clear": True},
        {"action": "http", "label": "Deny",  "url": deny_url,  "method": "GET", "clear": True},
    ],
}))
PYEOF
)

curl -s -X POST "http://127.0.0.1:2586/" \
    -H "Authorization: Bearer ${NTFY_BEARER}" \
    -H "Content-Type: application/json" \
    -d "$PAYLOAD" >/dev/null 2>&1 || true

echo "[approval-gate] pushed allow/deny request, polling (ttl=${TTL_SECONDS}s)..."

ELAPSED=0
while (( ELAPSED < TTL_SECONDS + 10 )); do
    STATUS_RESP=$(curl -s "${BASE_URL}/admin/approval-requests/${REQUEST_ID}" \
        -H "Authorization: Bearer ${ADMIN_TOKEN}")
    STATUS=$(python3 -c "import json,sys; print(json.load(sys.stdin)['status'])" <<< "$STATUS_RESP")
    if [[ "$STATUS" == "allowed" ]]; then
        echo "[approval-gate] ALLOWED -- running: ${CMD_STR}"
        "${CMD[@]}"
        EXIT_CODE=$?
        COUNT_RESP=$(curl -s "${BASE_URL}/admin/approval-requests?command_pattern=${PATTERN}" \
            -H "Authorization: Bearer ${ADMIN_TOKEN}")
        echo "[approval-gate] recent-approval count for '${PATTERN}': $(python3 -c "import json,sys; d=json.load(sys.stdin); print(f\"{d['allowed_count']} in last {int(d['since_days'])}d (promotion_candidate={d['promotion_candidate']})\")" <<< "$COUNT_RESP")"
        exit $EXIT_CODE
    elif [[ "$STATUS" == "denied" ]]; then
        echo "[approval-gate] DENIED -- not running command"
        exit 1
    elif [[ "$STATUS" == "expired" ]]; then
        echo "[approval-gate] EXPIRED (no response within TTL) -- treated as denial, not running command"
        exit 1
    fi
    sleep "$POLL_INTERVAL"
    ELAPSED=$((ELAPSED + POLL_INTERVAL))
done

echo "[approval-gate] poll loop exhausted without a terminal status -- treating as denial (fail-closed)"
exit 1
