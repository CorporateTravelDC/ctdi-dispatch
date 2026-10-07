#!/usr/bin/env bash
# scripts/approver-ctl.sh -- register the HUMAN approval keys that sign
# approvals (docs/AGENT_SEGMENTATION.md "Approvals, council/arena and the
# shared workspace"). Operator-only.
#
#   approver-ctl.sh register <account> <pubkey-file> [--private-key FILE | --off-box]
#   approver-ctl.sh deactivate|activate <account> [note]
#   approver-ctl.sh list
#
# An approval key is NOT the account's board key: the operator's board key
# (~/.ssh/corporatetraveldc_ed25519) has no passphrase, so every process
# running as the operator -- Claude Code included -- can sign with it. The
# approval key must therefore be either
#   * passphrase-protected on this box: register checks the private key with
#     --private-key and refuses one that opens with an empty passphrase, or
#   * off-box (phone / laptop / hardware): --off-box, you sign there with
#     scripts/approve.sh message + submit.
# The account must already be an ACTIVE kind=human board signer; a key equal
# to any board signer key is refused server-side too (common.governance).
# Create one (passphrase prompt, on the device that will hold it):
#   ssh-keygen -t ed25519 -C "$(id -un)-approver@corporatetraveldc-dispatch" -f ~/.ssh/$(id -un)_approver_ed25519
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
. "${REPO_ROOT}/scripts/lib/gov-exec.sh"
usage() { sed -n '2,24p' "$0" | sed 's/^# \{0,1\}//'; exit 64; }
CMD="${1:-}"; shift || true
case "$CMD" in
  register)
    ACCT="${1:-}"; PUBF="${2:-}"; shift 2 || usage
    PRIV=""; OFFBOX=0
    while (( $# )); do case "$1" in --private-key) shift; PRIV="${1:-}" ;; --off-box) OFFBOX=1 ;; *) usage ;; esac; shift; done
    [[ "$ACCT" =~ ^[a-z_][a-z0-9_-]{0,31}$ && -r "$PUBF" ]] || usage
    lines=$(grep -cvE '^\s*(#|$)' "$PUBF" || true); [[ "$lines" == 1 ]] || { echo "${PUBF}: expected exactly one key line" >&2; exit 65; }
    PUB=$(grep -vE '^\s*(#|$)' "$PUBF" | head -1)
    [[ "$PUB" == ssh-ed25519\ * ]] || { echo "only ssh-ed25519 keys are accepted" >&2; exit 65; }
    if (( ! OFFBOX )); then
      [[ -n "$PRIV" && -r "$PRIV" ]] || { echo "give --private-key FILE (checked for a passphrase) or --off-box" >&2; exit 65; }
      if ssh-keygen -y -P '' -f "$PRIV" >/dev/null 2>&1; then
        echo "refusing: ${PRIV} opens WITHOUT a passphrase -- any process running as $(id -un) could approve with it. ssh-keygen -p -f ${PRIV} to add one." >&2; exit 66
      fi
    fi
    gov_py "$ACCT" "$PUB" <<'PYEOF'
import sys
from common import governance as g
acct, pub = sys.argv[1:3]
try:
    g.approval_signer_upsert(acct, pub, pub.split()[2] if len(pub.split()) > 2 else None, "registered by approver-ctl")
except g.GovernanceError as e:
    print(f"refused: {e.detail}", file=sys.stderr); sys.exit(65)
print(f"approval key registered for {acct}")
PYEOF
    ;;
  deactivate|activate)
    ACCT="${1:-}"; NOTE="${2:-${CMD}d by approver-ctl}"; [[ -n "$ACCT" ]] || usage
    gov_py "$CMD" "$ACCT" "$NOTE" <<'PYEOF'
import sys
from common import governance as g
cmd, acct, note = sys.argv[1:4]
changed = g.approval_signer_set_active(acct, cmd == "activate", note)
print(("%sd approval key of %s" % (cmd, acct)) if changed else f"no approval key for {acct}")
PYEOF
    ;;
  list)
    gov_py <<'PYEOF'
from common import governance as g
for r in g.approval_signer_list():
    print(f"{r['account']:<20} {'active' if r['active'] else 'INACTIVE':<9} {r['key_comment'] or ''}")
PYEOF
    ;;
  *) usage ;;
esac
