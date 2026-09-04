#!/usr/bin/env bash
# scripts/grant-agent-session.sh
#
# Human-run only. Issues a bounded, explicit exemption from the per-signature
# approval-gate round-trip (scripts/sudo-approval-gate.sh) for a specific
# command_pattern -- so a human doing a long batch of agent-driven work
# doesn't have to tap Allow on their phone for every single signature.
#
# Nothing agent-side can call this -- an agent can only check for/consume an
# existing grant (common.db.get_active_session_grant), never create one.
# This is the operator directive from 2026-08-20: even a session-wide
# exemption must be something a human deliberately switches on, and it must
# leave an audit_log record when it's granted -- see common.db.
# create_session_grant()'s docstring.
#
# Every individual signature made under an active grant STILL gets its own
# db.audit() row (see scripts/agent-sign-manifest.sh) -- a grant changes
# friction, never the record.
#
# Usage:
#   scripts/grant-agent-session.sh <command_pattern> [hours] [reason...]
#   scripts/grant-agent-session.sh --revoke <grant_id>
#   scripts/grant-agent-session.sh --status <command_pattern>
#
# Examples:
#   scripts/grant-agent-session.sh sign-manifest:agent-key 4 "big refactor pass"
#   scripts/grant-agent-session.sh sign-manifest:agent-key 1
#   scripts/grant-agent-session.sh --status sign-manifest:agent-key
#   scripts/grant-agent-session.sh --revoke 00000000-0000-0000-0000-000000000001
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "${REPO_DIR}"

if [[ "${1:-}" == "--revoke" ]]; then
    [[ -n "${2:-}" ]] || { echo "usage: $0 --revoke <grant_id>" >&2; exit 2; }
    GRANT_ID="$2"
    PYTHONPATH=src python3 -c "
from common import db
ok = db.revoke_session_grant('${GRANT_ID}', revoked_by='${USER:-operator}')
print('revoked' if ok else 'not found / already revoked or expired')
"
    exit 0
fi

if [[ "${1:-}" == "--status" ]]; then
    [[ -n "${2:-}" ]] || { echo "usage: $0 --status <command_pattern>" >&2; exit 2; }
    PATTERN="$2"
    PYTHONPATH=src python3 -c "
import time
from common import db
g = db.get_active_session_grant('${PATTERN}')
if g:
    mins_left = (g['expires_at'] - time.time()) / 60
    print(f\"ACTIVE: id={g['id']} scope={g['scope']} expires_in={mins_left:.1f}min\")
else:
    print('no active grant for this pattern')
"
    exit 0
fi

if [[ $# -lt 1 ]]; then
    echo "usage: $0 <command_pattern> [hours=4] [reason...]" >&2
    echo "       $0 --revoke <grant_id>" >&2
    echo "       $0 --status <command_pattern>" >&2
    exit 2
fi

PATTERN="$1"
HOURS="${2:-4}"
shift $(( $# >= 2 ? 2 : 1 ))
REASON="$*"
TTL_SECONDS=$(python3 -c "print(float('${HOURS}') * 3600)")

echo "[grant-agent-session] issuing a ${HOURS}h grant for pattern='${PATTERN}'"
echo "[grant-agent-session] this waives the per-signature Allow/Deny prompt for that long --"
echo "[grant-agent-session] every individual use is still audit-logged, this only changes friction."

PYTHONPATH=src python3 -c "
from common import db
g = db.create_session_grant('${PATTERN}', granted_by='${USER:-operator}', reasoning='''${REASON}''', ttl_seconds=${TTL_SECONDS})
print(f\"[grant-agent-session] OK -- grant id={g['id']} expires_at={g['expires_at']}\")
print(f\"[grant-agent-session] revoke early with: $0 --revoke {g['id']}\")
"
