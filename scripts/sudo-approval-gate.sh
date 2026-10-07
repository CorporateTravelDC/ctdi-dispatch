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
# Standing policy, 2026-08-15 (see SUDO_JUSTIFICATION_PROPOSAL.md "DR/
# time-sensitive auto-promotion"): any DR or time-sensitive request gets
# max ntfy priority (5) AUTOMATICALLY -- not left to each caller to
# remember to set, same lesson as tonight's scattered-timeout mess.
# A request auto-qualifies for priority 5 if ANY of:
#   1. The command itself is destructive -- contains "kill" (systemctl
#      kill, SIGKILL, etc.), case-insensitive. Asking to forcibly
#      terminate something is, by definition, past routine maintenance.
#   2. The command touches "ollama-governor" -- the thermal safety
#      mechanism, softened 2026-08-15 from an absolute never-touch rule
#      to "never silently/automatically" -- any stop/start/restart of it
#      requires an explicit human Allow, always at max priority. See
#      SUDO_JUSTIFICATION_PROPOSAL.md "Still fully excluded" for the full
#      reasoning.
#   3. The caller explicitly flags it: APPROVAL_GATE_DR=1 -- for DR/
#      time-sensitive scenarios that don't literally involve "kill" or
#      the governor (e.g. an urgent dnf removal during an active
#      incident).
# Everything else defaults to priority 4 (routine). APPROVAL_GATE_PRIORITY
# remains available as a raw override on top of both rules, for the rare
# case a caller genuinely needs something other than 4 or 5.
#
# Behavior (2026-10-04 Wave 2: allow = a human's signed approval, the push
# only notifies + offers Deny): creates a pending approval request via the
# local admin API, pushes an ntfy alert with a Deny button (resolve URL points at
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

if [[ -n "${APPROVAL_GATE_PRIORITY:-}" ]]; then
    GATE_PRIORITY="$APPROVAL_GATE_PRIORITY"
elif [[ "${APPROVAL_GATE_DR:-0}" -eq 1 || "${CMD_STR,,}" == *kill* || "${CMD_STR,,}" == *ollama-governor* ]]; then
    GATE_PRIORITY=5
else
    GATE_PRIORITY=4
fi

# Bearer tokens go to curl as a header FILE (process substitution, owner-only
# /dev/fd), never as argv: /proc/<pid>/cmdline is world-readable on this box
# (no hidepid), so `-H "Authorization: Bearer $TOKEN"` exposes the token to
# every account, including team accounts (adversarial duel 2026-10-04).
auth_hdr() { printf 'Authorization: Bearer %s\n' "$1"; }

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
CREATE_BODY=$(python3 - "$PATTERN" "$CMD_STR" "$REASON" "$TTL_SECONDS" <<'PYEOF'
import json, sys
print(json.dumps({"command_pattern": sys.argv[1], "command": sys.argv[2],
                  "reasoning": sys.argv[3], "ttl_seconds": float(sys.argv[4])}))
PYEOF
)
CREATE_RESP=$(curl -s -X POST "${BASE_URL}/admin/approval-requests" \
    -H @<(auth_hdr "$ADMIN_TOKEN") \
    -H "Content-Type: application/json" \
    -d "$CREATE_BODY")

# 2026-10-04 (duel X1): the response carries per-action resolve keys exactly
# once. They go ONLY into the push URLs below -- never echoed, never logged.
# JSON goes through the environment (owner-only /proc/<pid>/environ), never
# argv (world-readable /proc/<pid>/cmdline): the response carries the keys.
json_field() { GATE_JSON="$1" python3 - "$2" <<'PYEOF'
import json, os, sys
print(json.loads(os.environ["GATE_JSON"] or "{}").get(sys.argv[1], ""))
PYEOF
}
REQUEST_ID=$(json_field "$CREATE_RESP" id)
DENY_KEY=$(json_field "$CREATE_RESP" deny_key)
if [[ -z "$REQUEST_ID" || -z "$DENY_KEY" ]]; then
    echo "ERROR: approval request creation failed (no id/keys in response) -- not running command" >&2
    exit 1
fi
echo "[approval-gate] request_id=${REQUEST_ID}"

NTFY_TOKEN_RAW=$(grep -m1 '^NTFY_TOKEN=' /etc/corporatetraveldc/dispatch-secrets.env | cut -d= -f2-)
NTFY_BEARER="${NTFY_TOKEN_RAW%%:*}"

# 2026-10-04 (Wave 2): the push carries NO allow link. Allowing takes a
# human's SSH signature over this exact request (scripts/approve.sh, approval
# key registered with scripts/approver-ctl.sh) -- an agent holding the admin
# token, the ntfy token or the push itself has nothing it can tap. The deny
# link stays (denying is always safe).
DENY_URL="${RESOLVE_HOST}/admin/approval-requests/${REQUEST_ID}/resolve?action=deny&k=${DENY_KEY}"
unset DENY_KEY

PAYLOAD=$(GATE_DENY_URL="$DENY_URL" python3 - "$REQUEST_ID" "$PATTERN" "$CMD_STR" "$REASON" "$GATE_PRIORITY" << 'PYEOF'
import json, os, sys
req_id, pattern, cmd, reason, priority = sys.argv[1:6]
deny_url = os.environ["GATE_DENY_URL"]
print(json.dumps({
    "topic": "approval-gate",
    "title": f"Approval needed: {pattern}",
    "message": (f"{cmd}\n\nreason: {reason}\n\n"
                f"approve over SSH: scripts/approve.sh allow {req_id}\n"
                "expires in 10 min. no signature = denied."),
    "priority": int(priority),
    "actions": [
        {"action": "http", "label": "Deny", "url": deny_url, "method": "GET", "clear": True},
    ],
}))
PYEOF
)

printf '%s' "$PAYLOAD" | curl -s -X POST "http://127.0.0.1:2586/" \
    -H @<(auth_hdr "$NTFY_BEARER") \
    -H "Content-Type: application/json" \
    --data-binary @- >/dev/null 2>&1 || true
unset PAYLOAD DENY_URL

echo "[approval-gate] pushed; a human must run: scripts/approve.sh allow ${REQUEST_ID}  (polling, ttl=${TTL_SECONDS}s)..."

ELAPSED=0
while (( ELAPSED < TTL_SECONDS + 10 )); do
    STATUS_RESP=$(curl -s "${BASE_URL}/admin/approval-requests/${REQUEST_ID}" \
        -H @<(auth_hdr "$ADMIN_TOKEN"))
    STATUS=$(json_field "$STATUS_RESP" status)
    if [[ "$STATUS" == "allowed" ]]; then
        echo "[approval-gate] ALLOWED -- running: ${CMD_STR}"
        "${CMD[@]}"
        EXIT_CODE=$?
        COUNT_RESP=$(curl -s "${BASE_URL}/admin/approval-requests?command_pattern=${PATTERN}" \
            -H @<(auth_hdr "$ADMIN_TOKEN"))
        echo "[approval-gate] recent-approval count for '${PATTERN}': $(json_field "$COUNT_RESP" allowed_count) (promotion_candidate=$(json_field "$COUNT_RESP" promotion_candidate))"
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
