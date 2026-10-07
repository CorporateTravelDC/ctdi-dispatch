#!/usr/bin/env bash
# scripts/agent-gateway.sh -- operator CLI for the cloud-agent OAuth + MCP
# gateway (common/agent_gateway.py; docs/AGENT_SEGMENTATION.md "Agent gateway").
#
#   agent-gateway.sh add-connector SLUG ACCOUNT VENDOR   e.g. cowork ctdc-agent-anthropic-cowork anthropic
#   agent-gateway.sh connectors                          list connector slugs -> accounts
#   agent-gateway.sh status [SLUG]                       sessions: active | dormant | held | revoked
#   agent-gateway.sh hold SLUG --days N                  keep our side of the link open through a vendor
#                                                         lapse (N <= 90); asks for YOUR signature
#   agent-gateway.sh release SLUG                        end a hold early
#   agent-gateway.sh revoke SLUG|ACCOUNT [reason]        end the chain now (re-link needed)
#   agent-gateway.sh disable SLUG | enable SLUG          switch one connector off (revokes it) / back on
#   agent-gateway.sh kill-all [reason]                   FULL COMPROMISE: freeze the gateway, revoke every
#                                                         connection, disable every connector
#   agent-gateway.sh thaw                                re-open after kill-all; asks for YOUR signature
#
# The MCP address to give the vendor: ${GATEWAY_BASE_URL:-https://agents.example.com}/mcp/SLUG
# Linking a vendor and holding a link both need the operator's SSH-signed
# approval (scripts/approve.sh); revocation needs only this CLI.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
. "${REPO_ROOT}/scripts/lib/gov-exec.sh"
usage() { sed -n '2,19p' "$0" | sed 's/^# \{0,1\}//'; exit 64; }
CMD="${1:-}"; shift || true
case "$CMD" in
  add-connector) [[ $# -eq 3 ]] || usage
    gov_py "$@" <<'PYEOF'
import sys, os
from common import agent_gateway as g
slug, acct, vendor = sys.argv[1:4]
try:
    g.connector_add(slug, acct, vendor)
except g.GatewayError as e:
    sys.exit(f"refused: {e.detail}")
base = os.environ.get("GATEWAY_BASE_URL", "https://agents.example.com")
print(f"connector {slug} -> {acct} ({vendor}); give the vendor: {base}/mcp/{slug}")
PYEOF
    ;;
  connectors)
    gov_py <<'PYEOF'
from common import agent_gateway as g
for c in g.connector_list():
    print(f"{c['slug']:<16} {c['account']:<32} {c['vendor']:<10} {'DISABLED' if c.get('disabled_at') else ''}")
PYEOF
    ;;
  status)
    gov_py "${1:-}" <<'PYEOF'
import sys, time
from common import agent_gateway as g
f = lambda t: time.strftime("%m-%d %H:%M", time.localtime(t)) if t else "-"
for r in g.status(sys.argv[1] or None):
    print(f"{r['slug']:<14} {r['account']:<32} {r['status']:<8} renewed {f(r['last_renewal_at'])}  call {f(r['last_call_at'])}"
          f"  hold {f(r['hold_until'])}  {r['revoke_reason'] or ''}")
PYEOF
    ;;
  hold)
    SLUG="${1:-}"; [[ "${2:-}" == --days && -n "${3:-}" ]] || usage
    AID="$(gov_py "$SLUG" "$3" <<'PYEOF'
import sys
from common import agent_gateway as g
try:
    print(g.request_hold(sys.argv[1], float(sys.argv[2]))["approval_id"])
except g.GatewayError as e:
    sys.exit(f"refused: {e.detail}")
PYEOF
)"
    echo "hold requested (approval ${AID}); signing it now"
    exec "${REPO_ROOT}/scripts/approve.sh" allow "$AID" ;;
  release) [[ -n "${1:-}" ]] || usage
    gov_py "$1" <<'PYEOF'
import sys
from common import agent_gateway as g
print(f"released {g.release_hold(sys.argv[1])} connection(s)")
PYEOF
    ;;
  revoke) [[ -n "${1:-}" ]] || usage
    gov_py "$1" "${2:-revoked by operator}" <<'PYEOF'
import sys
from common import agent_gateway as g
who, why = sys.argv[1:3]
n = g.revoke_account(who, why) if who.startswith("ctdc-") else sum(
    (g.revoke_connection(r["id"], why) or 1) for r in g.status(who) if r["status"] != "revoked")
print(f"revoked {n} connection(s) for {who}")
PYEOF
    ;;
  disable|enable) [[ -n "${1:-}" ]] || usage
    gov_py "$CMD" "$1" <<'PYEOF'
import sys
from common import agent_gateway as g
n = g.connector_set_disabled(sys.argv[2], sys.argv[1] == "disable")
print(f"connector {sys.argv[2]} {sys.argv[1]}d" if n else f"no connector {sys.argv[2]}")
PYEOF
    ;;
  kill-all)
    gov_py "${SUDO_USER:-${USER:-operator}}" "${1:-gateway kill-all}" <<'PYEOF'
import sys
from common import agent_gateway as g
n = g.kill_all(sys.argv[1], sys.argv[2])
print(f"GATEWAY FROZEN: {n} connection(s) revoked, every connector disabled.")
print("Undo: agent-gateway.sh thaw (your signature), then enable connectors and re-link each vendor.")
PYEOF
    ;;
  thaw)
    AID="$(gov_py <<'PYEOF'
from common import agent_gateway as g
print(g.request_thaw()["approval_id"])
PYEOF
)"
    echo "thaw requested (approval ${AID}); signing it now"
    exec "${REPO_ROOT}/scripts/approve.sh" allow "$AID" ;;
  *) usage ;;
esac
