#!/usr/bin/env bash
# scripts/workspace-grants.sh -- who may WRITE to the shared ghostwriting
# workspace (Series/contributions/), per agent, per task, or all at once.
# Operator-only. Reads are open to every team account (research routes);
# writes are create-only and attributed (POST /api/v1/workspace/contribute).
#
#   workspace-grants.sh list
#   workspace-grants.sh grant  <account|*> [--task ID] [--until ISO8601+offset] [--note TEXT]
#   workspace-grants.sh deny   <account|*> [--task ID] [--until ISO8601+offset] [--note TEXT]
#   workspace-grants.sh revoke <grant-id>
#   workspace-grants.sh lock               deny * * -- freezes every write
#   workspace-grants.sh unlock
#
# Deny wins; an expired row is ignored either way; no matching grant = no
# write. Default after migration 0069: grant * * (everyone). Council/arena
# approvals add per-participant task grants that expire at the deadline.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
. "${REPO_ROOT}/scripts/lib/gov-exec.sh"
usage() { sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'; exit 64; }
CMD="${1:-}"; shift || true
ACCT=""; TASK="*"; UNTIL=""; NOTE=""; GID=""
case "$CMD" in
  grant|deny) ACCT="${1:-}"; shift || true
    while (( $# )); do case "$1" in --task) TASK="$2"; shift ;; --until) UNTIL="$2"; shift ;; --note) NOTE="$2"; shift ;; *) usage ;; esac; shift; done
    [[ -n "$ACCT" ]] || usage ;;
  revoke) GID="${1:-}"; [[ -n "$GID" ]] || usage ;;
  lock|unlock|list) ;;
  *) usage ;;
esac
gov_py "$CMD" "$ACCT" "$TASK" "$UNTIL" "$NOTE" "$GID" "$(id -un)" <<'PYEOF'
import sys, time
from datetime import datetime
from common import governance as g
cmd, acct, task, until, note, gid, me = sys.argv[1:8]
try:
    if cmd in ("grant", "deny"):
        u = None
        if until:
            d = datetime.fromisoformat(until)
            if d.tzinfo is None:
                print("refused: --until needs an explicit offset (e.g. 2026-10-11T20:00-04:00)", file=sys.stderr); sys.exit(65)
            u = d.timestamp()
        print(g.grant_add(cmd, acct, task, u, me, note or None))
    elif cmd == "revoke":
        print("revoked" if g.grant_remove(gid) else f"no grant {gid}")
    elif cmd == "lock":
        g.grant_add("deny", "*", "*", None, me, "lock", gid="wg-lock"); print("workspace LOCKED (deny * *)")
    elif cmd == "unlock":
        print("unlocked" if g.grant_remove("wg-lock") else "was not locked")
    else:
        now = time.time()
        for r in g.grant_list():
            exp = "" if r["until"] is None else time.strftime(" until %Y-%m-%d %H:%M", time.localtime(r["until"])) + (" (EXPIRED)" if r["until"] <= now else "")
            print(f"{r['id']:<28} {r['effect']:<5} {r['account']:<26} task={r['task']}{exp}  {r['note'] or ''}")
except g.GovernanceError as e:
    print(f"refused: {e.detail}", file=sys.stderr); sys.exit(65)
PYEOF
