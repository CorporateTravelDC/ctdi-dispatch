#!/usr/bin/env bash
# scripts/mint-agent-api-token.sh -- give a team account its OWN dispatch-API
# token, scoped to named actions, delivered into the account's home and never
# shown (2026-10-05; replaces shared/over-broad tokens such as Cowork's
# admin-tier remote-admin/cowork).
#
#   scripts/mint-agent-api-token.sh ACCOUNT --actions "a,b,c" [--tier admin|shares|cert] [--execute]
#   scripts/mint-agent-api-token.sh --revoke-label USER_LABEL DEVICE_LABEL [--execute]
#
# The token is minted inside the web container (common/auth.generate_token, the
# signed image's code), its row gets allowed_actions (pg_schema/0070), and the
# value goes straight into /home/ACCOUNT/.config/ctdc/api-token (0600, owned by
# the account) through a pipe -- not argv, not stdout. user_label = ACCOUNT, so
# the liveness switch's revoke-tokens kills it with the account. Action names
# are the ones require_admin() audits (e.g. admin.healthz, watchlist.flight.add).
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
. "${REPO_ROOT}/scripts/lib/gov-exec.sh"
usage() { sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'; exit 64; }
EXEC=0; ACCT=""; ACTIONS=""; TIER=admin; RL=""; RD=""
while (( $# )); do case "$1" in
  --execute) EXEC=1 ;; --actions) ACTIONS="${2:-}"; shift ;; --tier) TIER="${2:-}"; shift ;;
  --revoke-label) RL="${2:-}"; RD="${3:-}"; shift 2 ;;
  -*) usage ;; *) ACCT="$1" ;; esac; shift; done

if [[ -n "$RL" ]]; then
  [[ -n "$RD" ]] || usage
  if (( ! EXEC )); then echo "plan: revoke active tokens with user_label=${RL} device_label=${RD}"; exit 0; fi
  gov_py "$RL" "$RD" <<'PYEOF'
import sys, time
from common import db
ul, dl = sys.argv[1:3]
with db.conn() as c:
    cur = c.execute("UPDATE auth_tokens SET revoked_at = ? WHERE user_label = ? AND device_label = ? AND revoked_at IS NULL",
                    (time.time(), ul, dl))
print(f"revoked {cur.rowcount} token(s) {ul}/{dl}")
PYEOF
  exit 0
fi

[[ "$ACCT" =~ ^ctdc-[a-z0-9-]+$ && -n "$ACTIONS" && "$TIER" =~ ^(admin|shares|cert)$ ]] || usage
getent passwd "$ACCT" >/dev/null || { echo "no such account $ACCT" >&2; exit 65; }
DEST="/home/${ACCT}/.config/ctdc/api-token"
echo "plan: mint ${TIER} token for ${ACCT} scoped to: ${ACTIONS}"
echo "      deliver to ${DEST} (0600 ${ACCT}); never printed"
(( EXEC )) || { echo "plan only -- add --execute"; exit 0; }
sudo -v
TOK="$(gov_py "$ACCT" "$TIER" "$ACTIONS" <<'PYEOF'
import sys
from auth.auth import generate_token, _hash_token
from common import db
acct, tier, actions = sys.argv[1:4]
tok = generate_token(acct, tier, f"{acct}-api-scoped")
with db.conn() as c:
    c.execute("UPDATE auth_tokens SET allowed_actions = ? WHERE token_hash = ?", (actions, _hash_token(tok)))
sys.stdout.write(tok)
PYEOF
)"
[[ "$TOK" == ctdc_* ]] || { echo "mint failed" >&2; exit 4; }
sudo install -d -m 0700 -o "$ACCT" -g "$ACCT" "/home/${ACCT}/.config" "/home/${ACCT}/.config/ctdc"
printf '%s' "$TOK" | sudo install -m 0600 -o "$ACCT" -g "$ACCT" /dev/stdin "$DEST"
code=$(curl -s -o /dev/null -w '%{http_code}' -H @<(printf 'Authorization: Bearer %s\n' "$TOK") http://127.0.0.1:8000/admin/healthz)
unset TOK
echo "minted and delivered to ${DEST}; probe /admin/healthz -> ${code} ($( [[ ",${ACTIONS}," == *",admin.healthz,"* ]] && echo 'want 200' || echo 'want 403 -- not in scope'))"
