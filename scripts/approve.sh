#!/usr/bin/env bash
# scripts/approve.sh -- a HUMAN approves or denies one pending request by
# signing its exact content (Wave 2, 2026-10-04). Nothing else allows: the
# phone push only notifies and offers Deny.
#
#   approve.sh list                       pending requests (sudo, council, council-close)
#   approve.sh show  <id>                 the full request, exactly what you would sign
#   approve.sh allow <id>                 show, confirm, sign with YOUR approval key, submit
#   approve.sh deny  <id>                 same, deny
#   approve.sh message <id> allow|deny    print the canonical text only (off-box signing:
#                                         ssh-keygen -Y sign -f KEY -n corporatetraveldc-approval < msg > msg.sig)
#   approve.sh submit  <id> allow|deny <msg.sig>   submit an off-box signature
#
# Approval key: $APPROVE_KEY, default ~/.ssh/<you>_approver_ed25519 --
# passphrase-protected (scripts/approver-ctl.sh refuses to register one that
# isn't). ssh-keygen asks for the passphrase on your terminal; that typed
# passphrase is the human step an agent cannot perform.
# The canonical text is rebuilt HERE from the command this script displays
# (sha256 computed locally), so what you read is what you sign:
#   corporatetraveldc-approval v1 / id / action / kind / requester /
#   expires_at / command-sha256         (common/governance.py approval_canonical)
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BASE="${BOARD_API_BASE:-http://127.0.0.1:8000}"
NS=corporatetraveldc-approval
ME="$(id -un)"
KEY="${APPROVE_KEY:-$HOME/.ssh/${ME}_approver_ed25519}"
usage() { sed -n '2,24p' "$0" | sed 's/^# \{0,1\}//'; exit 64; }

signed_get() {   # PATH -> body on stdout; non-200 is fatal
  local out code
  out="$("${REPO_ROOT}/scripts/board-sign.sh" GET "$1" --send)"
  code="$(printf '%s\n' "$out" | tail -1 | awk '{print $2}')"
  [[ "$code" == 200 ]] || { printf '%s\n' "$out" >&2; echo "approve: GET $1 -> HTTP ${code}" >&2; exit 1; }
  printf '%s\n' "$out" | sed '$d'
}

# REQ_JSON in the environment (never argv) -> fields / canonical text
req_py() { REQ_JSON="$1" python3 - "${@:2}" <<'PYEOF'
import hashlib, json, os, sys, time
r = json.loads(os.environ["REQ_JSON"])
mode = sys.argv[1]
if mode == "show":
    print(f"id:         {r['id']}\nkind:       {r.get('kind') or 'sudo'}\nstatus:     {r['status']}\n"
          f"requester:  {r.get('requester') or '-'}\npattern:    {r.get('command_pattern')}\n"
          f"expires:    {time.strftime('%Y-%m-%d %H:%M:%S %Z', time.localtime(r['expires_at']))}\n"
          f"reasoning:  {r.get('reasoning') or ''}\n--- exact command / request (what you sign) ---")
    cmd = r.get("command") or ""
    try:
        print(json.dumps(json.loads(cmd), indent=2, sort_keys=True))
    except ValueError:
        print(cmd)
    print("--- sha256: " + hashlib.sha256(cmd.encode()).hexdigest())
elif mode == "status":
    print(r["status"])
elif mode == "canonical":
    action = sys.argv[2]
    sys.stdout.write("\n".join([
        "corporatetraveldc-approval v1", f"id: {r['id']}", f"action: {action}",
        f"kind: {r.get('kind') or 'sudo'}", f"requester: {r.get('requester') or '-'}",
        f"expires_at: {int(r['expires_at'])}",
        "command-sha256: " + hashlib.sha256((r.get("command") or "").encode()).hexdigest()]))
elif mode == "list":
    for p in r.get("pending", []):
        left = int(p["expires_at"] - time.time())
        print(f"{p['id']}  {p.get('kind') or 'sudo':<13} {p.get('requester') or '-':<22} {left//60:>4}m left  {p.get('command_pattern')}")
    if not r.get("pending"):
        print("(nothing pending)")
PYEOF
}

submit() {   # id action armored-sig-file
  local sig body
  sig="$(base64 -w0 < "$3")"
  body="$(SIG="$sig" python3 - "$2" "$ME" <<'PYEOF'
import json, os, sys
print(json.dumps({"action": sys.argv[1], "signer": sys.argv[2], "signature": os.environ["SIG"]}))
PYEOF
)"
  printf '%s' "$body" | curl -sS -X POST "${BASE}/api/v1/approvals/$1/resolve" -H 'Content-Type: application/json' \
      --data-binary @- -w '\nHTTP %{http_code}\n'
}

CMD="${1:-}"; ID="${2:-}"
[[ -z "$ID" || "$ID" =~ ^[0-9a-f-]{36}$ ]] || { echo "approve: '${ID}' is not a request id" >&2; exit 64; }
case "$CMD" in
  list) req_py "$(signed_get /api/v1/approvals)" list ;;
  show) [[ -n "$ID" ]] || usage; req_py "$(signed_get "/api/v1/approvals/${ID}")" show ;;
  message)
    ACT="${3:-}"; [[ -n "$ID" && "$ACT" =~ ^(allow|deny)$ ]] || usage
    req_py "$(signed_get "/api/v1/approvals/${ID}")" canonical "$ACT"; echo ;;
  submit)
    ACT="${3:-}"; SIGF="${4:-}"; [[ -n "$ID" && "$ACT" =~ ^(allow|deny)$ && -r "$SIGF" ]] || usage
    submit "$ID" "$ACT" "$SIGF" ;;
  allow|deny)
    [[ -n "$ID" ]] || usage
    [[ -f "$KEY" ]] || { echo "approve: no approval key at ${KEY} (APPROVE_KEY=...; register one with scripts/approver-ctl.sh)" >&2; exit 66; }
    if ssh-keygen -y -P '' -f "$KEY" >/dev/null 2>&1; then
      echo "approve: ${KEY} has NO passphrase -- refusing (an agent running as ${ME} could use it). ssh-keygen -p -f ${KEY}" >&2; exit 66
    fi
    REQ="$(signed_get "/api/v1/approvals/${ID}")"
    req_py "$REQ" show
    [[ "$(req_py "$REQ" status)" == pending ]] || { echo "approve: request is not pending" >&2; exit 1; }
    [[ -t 0 ]] || { echo "approve: needs an interactive terminal (a human)" >&2; exit 1; }
    read -r -p "Type ${CMD^^} to sign this exact request as ${ME}: " ans
    [[ "$ans" == "${CMD^^}" ]] || { echo "approve: not confirmed -- nothing signed"; exit 1; }
    tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
    req_py "$REQ" canonical "$CMD" > "$tmp/msg"
    ssh-keygen -Y sign -f "$KEY" -n "$NS" < "$tmp/msg" > "$tmp/msg.sig"
    submit "$ID" "$CMD" "$tmp/msg.sig" ;;
  *) usage ;;
esac
