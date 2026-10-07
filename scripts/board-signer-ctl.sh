#!/usr/bin/env bash
# scripts/board-signer-ctl.sh -- manage the board_signers registry (identity-
# based board signing, docs/BOARD_SIGNING.md) and an account's credentials.
#
#   register   <account> [pubkey-file] [--role admin|member|service] [--kind human|agent|service]
#                                        add or re-key (defaults: the single line
#                                        in /home/<account>/.ssh/authorized_keys;
#                                        for ctdc-agent before stage B, the
#                                        operator's ~/.ssh/cowork_ed25519.pub).
#                                        First registration defaults member/agent;
#                                        a re-key keeps the existing role/kind.
#   set-role   <account> --role R [--kind K]   change role/kind (operator)
#   deactivate <account> [note]          revoke the KEY (active=false) -- the
#                                        team-liveness switch calls this on inert
#   activate   <account> [note]          restore the key
#   revoke-tokens <account>              kill every minted token of the account
#                                        (board tokens labelled with it + API
#                                        auth_tokens with that user_label); the
#                                        liveness switch calls this on inert
#   revoked-since <account> <epoch>      print how many of its tokens were
#                                        REVOKED after <epoch> (kill signal)
#   rename     <from> <to>               carry the signer (and its tokens' labels and
#                                        workspace grants) over to a renamed account
#                                        (plan.sh --rename-account, 2026-10-05)
#   show       <account>                 line 1: "<active|inactive|none> <role> <kind>"
#                                        line 2: the registered pubkey (if any)
#   list
#
# Called from scripts/agent-segmentation/plan.sh at --add-agent/--add-human
# (register) and from scripts/team-liveness.sh (show/deactivate/activate/
# revoke-tokens/revoked-since). Postgres access: PGPASSWORD is read in-script
# from the secrets file and never echoed; never shell-source the env files.
# SQL goes through a quoted heredoc. Falls back to the sqlite backend via
# common.db when DISPATCH_DB_BACKEND != postgres (tests / dev).
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SECRETS=/etc/corporatetraveldc/dispatch-secrets.env
ENVF=/etc/corporatetraveldc/dispatch.env
usage() { sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'; exit 64; }
CMD="${1:-}"; [[ -n "$CMD" ]] || usage; shift
ACCT=""; ARG3=""; ROLE=""; KIND=""
while (( $# )); do
  case "$1" in
    --role) shift; ROLE="${1:-}" ;;
    --kind) shift; KIND="${1:-}" ;;
    *) if [[ -z "$ACCT" ]]; then ACCT="$1"; elif [[ -z "$ARG3" ]]; then ARG3="$1"; else usage; fi ;;
  esac; shift
done
case "$CMD" in
  register|deactivate|activate|revoke-tokens|show) [[ -n "$ACCT" ]] || usage ;;
  set-role) [[ -n "$ACCT" && ( -n "$ROLE" || -n "$KIND" ) ]] || usage ;;
  revoked-since) [[ -n "$ACCT" && "$ARG3" =~ ^[0-9]+(\.[0-9]+)?$ ]] || usage ;;
  rename) [[ -n "$ACCT" && "$ARG3" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]] || usage ;;
  list) ;;
  *) usage ;;
esac
[[ -z "$ACCT" || "$ACCT" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]] || { echo "bad account name" >&2; exit 65; }
[[ -z "$ROLE" || "$ROLE" =~ ^(admin|member|service)$ ]] || { echo "role must be admin|member|service" >&2; exit 65; }
[[ -z "$KIND" || "$KIND" =~ ^(human|agent|service)$ ]] || { echo "kind must be human|agent|service" >&2; exit 65; }

# an explicit DISPATCH_DB_BACKEND in the environment (tests, dev) beats the deployment file
pg_backend() { local b="${DISPATCH_DB_BACKEND:-}"; [[ -n "$b" ]] || b="$(grep -m1 '^DISPATCH_DB_BACKEND=' "$ENVF" 2>/dev/null | cut -d= -f2- || true)"; [[ "$b" == postgres ]]; }
cfg() { grep -m1 "^$1=" "$ENVF" 2>/dev/null | cut -d= -f2- || true; }   # grep's rc 1 must not trip set -e
PG_USER="$(cfg DISPATCH_PG_USER)"; PG_USER="${PG_USER:-dispatch}"
PG_DB="$(cfg DISPATCH_PG_DB)";     PG_DB="${PG_DB:-corporatetraveldc}"
psql_run() {  # SQL on stdin
  local pw; pw="$(grep -m1 '^DISPATCH_PG_PASSWORD=' "$SECRETS" | cut -d= -f2-)"
  PGPASSWORD="$pw" podman exec -i -e PGPASSWORD corporatetraveldc-pgsql psql -U "$PG_USER" -d "$PG_DB" -v ON_ERROR_STOP=1 -q "$@"
}
sql_lit() { printf "%s" "$1" | sed "s/'/''/g"; }

resolve_pubkey() {
  local f="$1"
  if [[ -z "$f" ]]; then
    if [[ -r "/home/${ACCT}/.ssh/authorized_keys" ]]; then f="/home/${ACCT}/.ssh/authorized_keys"
    elif [[ "$ACCT" == ctdc-agent && -r "$HOME/.ssh/cowork_ed25519.pub" ]]; then f="$HOME/.ssh/cowork_ed25519.pub"
    elif [[ "$ACCT" == "$(id -un)" && -r "$HOME/.ssh/cowork_ed25519.pub" ]]; then f="$HOME/.ssh/cowork_ed25519.pub"
    else echo "no pubkey file given and none found for ${ACCT}" >&2; exit 66; fi
  fi
  # read ONCE: the runbook passes <(sudo cat /home/X/.ssh/X_ed25519.pub) -- a
  # pipe that cannot be read twice (the operator cannot read a team .ssh dir)
  local content lines; content=$(grep -vE '^\s*(#|$)' "$f" || true)
  lines=$(printf '%s' "$content" | grep -c . || true)
  [[ "$lines" -eq 1 ]] || { echo "${f}: expected exactly one key line, found ${lines}" >&2; exit 65; }
  PUB=$(printf '%s\n' "$content" | head -1)
  [[ "$PUB" == ssh-ed25519\ * ]] || { echo "only ssh-ed25519 keys are accepted" >&2; exit 65; }
  COMMENT=$(printf '%s' "$PUB" | awk '{print $3}')
}

if pg_backend; then
  case "$CMD" in
    register)
      resolve_pubkey "$ARG3"
      psql_run <<EOF
INSERT INTO board_signers (account, pubkey, key_comment, active, registered_at, note, role, kind)
VALUES ('$(sql_lit "$ACCT")', '$(sql_lit "$PUB")', '$(sql_lit "$COMMENT")', TRUE, EXTRACT(epoch FROM now()),
        'registered by board-signer-ctl', '$(sql_lit "${ROLE:-member}")', '$(sql_lit "${KIND:-agent}")')
ON CONFLICT (account) DO UPDATE SET pubkey = EXCLUDED.pubkey, key_comment = EXCLUDED.key_comment,
  active = TRUE, deactivated_at = NULL, note = 're-keyed by board-signer-ctl',
  role = COALESCE(NULLIF('$(sql_lit "$ROLE")', ''), board_signers.role),
  kind = COALESCE(NULLIF('$(sql_lit "$KIND")', ''), board_signers.kind);
EOF
      echo "registered ${ACCT} (${COMMENT})${ROLE:+ role=$ROLE}${KIND:+ kind=$KIND}" ;;
    set-role)
      psql_run <<EOF
UPDATE board_signers SET role = COALESCE(NULLIF('$(sql_lit "$ROLE")', ''), role), kind = COALESCE(NULLIF('$(sql_lit "$KIND")', ''), kind)
WHERE account = '$(sql_lit "$ACCT")';
EOF
      echo "updated ${ACCT}${ROLE:+ role=$ROLE}${KIND:+ kind=$KIND}" ;;
    deactivate)
      psql_run <<EOF
UPDATE board_signers SET active = FALSE, deactivated_at = EXTRACT(epoch FROM now()), note = '$(sql_lit "${ARG3:-deactivated by board-signer-ctl}")' WHERE account = '$(sql_lit "$ACCT")';
EOF
      # Wave 2 (2026-10-04): the account's APPROVAL key follows its board key
      # (the liveness switch kills both); guarded until migration 0069 exists
      psql_run <<EOF
DO \$\$ BEGIN
  IF to_regclass('approval_signers') IS NOT NULL THEN
    UPDATE approval_signers SET active = FALSE, deactivated_at = EXTRACT(epoch FROM now()) WHERE account = '$(sql_lit "$ACCT")';
  END IF;
END \$\$;
EOF
      echo "deactivated ${ACCT}" ;;
    activate)
      psql_run <<EOF
UPDATE board_signers SET active = TRUE, deactivated_at = NULL, note = '$(sql_lit "${ARG3:-reactivated by board-signer-ctl}")' WHERE account = '$(sql_lit "$ACCT")';
EOF
      # Wave 2 (2026-10-04): the account's APPROVAL key follows its board key
      # (the liveness switch kills both); guarded until migration 0069 exists
      psql_run <<EOF
DO \$\$ BEGIN
  IF to_regclass('approval_signers') IS NOT NULL THEN
    UPDATE approval_signers SET active = TRUE, deactivated_at = NULL WHERE account = '$(sql_lit "$ACCT")';
  END IF;
END \$\$;
EOF
      echo "activated ${ACCT}" ;;
    revoke-tokens)
      psql_run <<EOF
UPDATE board_tokens SET expires_at = EXTRACT(epoch FROM now()), revoked_at = EXTRACT(epoch FROM now())
  WHERE label = '$(sql_lit "$ACCT")' AND expires_at > EXTRACT(epoch FROM now());
UPDATE auth_tokens SET revoked_at = EXTRACT(epoch FROM now()) WHERE user_label = '$(sql_lit "$ACCT")' AND revoked_at IS NULL;
DO \$\$ BEGIN
  IF to_regclass('agent_connections') IS NOT NULL THEN
    UPDATE oauth_tokens SET revoked_at = EXTRACT(epoch FROM now()) WHERE revoked_at IS NULL
      AND connection_id IN (SELECT id FROM agent_connections WHERE account = '$(sql_lit "$ACCT")');
    UPDATE agent_connections SET status = 'revoked', revoked_at = EXTRACT(epoch FROM now()), revoke_reason = 'revoke-tokens'
      WHERE account = '$(sql_lit "$ACCT")' AND status != 'revoked';
  END IF;
END \$\$;
EOF
      echo "revoked all tokens (incl. agent-gateway connections) of ${ACCT}" ;;
    revoked-since)
      psql_run -tA <<EOF
SELECT (SELECT COUNT(*) FROM board_tokens WHERE label = '$(sql_lit "$ACCT")' AND revoked_at > ${ARG3})
     + (SELECT COUNT(*) FROM auth_tokens  WHERE user_label = '$(sql_lit "$ACCT")' AND revoked_at > ${ARG3});
EOF
      ;;
    rename)
      F="$(sql_lit "$ACCT")"; T="$(sql_lit "$ARG3")"
      psql_run <<EOF
BEGIN;
UPDATE board_signers SET account = '$T', key_comment = '$T@corporatetraveldc-dispatch',
  pubkey = regexp_replace(pubkey, ' $F@corporatetraveldc-dispatch\$', ' $T@corporatetraveldc-dispatch'),
  note = 'renamed from $F by board-signer-ctl' WHERE account = '$F';
UPDATE board_tokens SET label = '$T' WHERE label = '$F';
UPDATE auth_tokens SET user_label = '$T' WHERE user_label = '$F';
DO \$\$ BEGIN
  IF to_regclass('workspace_grants') IS NOT NULL THEN UPDATE workspace_grants SET account = '$T' WHERE account = '$F'; END IF;
  IF to_regclass('approval_signers') IS NOT NULL THEN UPDATE approval_signers SET account = '$T' WHERE account = '$F'; END IF;
END \$\$;
COMMIT;
EOF
      echo "renamed signer ${ACCT} -> ${ARG3}" ;;
    show)
      psql_run -tA <<EOF
SELECT CASE WHEN active THEN 'active' ELSE 'inactive' END || ' ' || role || ' ' || kind FROM board_signers WHERE account = '$(sql_lit "$ACCT")';
SELECT pubkey FROM board_signers WHERE account = '$(sql_lit "$ACCT")';
EOF
      ;;
    list)
      psql_run <<'EOF'
SELECT account, role, kind, key_comment, active, to_timestamp(registered_at) AS registered, to_timestamp(deactivated_at) AS deactivated FROM board_signers ORDER BY account;
EOF
      ;;
  esac
else
  # sqlite / dev: go through common.db so tests exercise the same helpers
  [[ "$CMD" == register ]] && resolve_pubkey "$ARG3"
  PYTHONPATH="${REPO_ROOT}/src" python3 - "$CMD" "$ACCT" "${PUB:-}" "${COMMENT:-}" "${ARG3:-}" "${ROLE:-}" "${KIND:-}" <<'PYEOF'
import sys
from common import db
cmd, acct, pub, comment, arg3, role, kind = sys.argv[1:8]
if cmd == "register":
    db.board_signer_upsert(acct, pub, comment or None, "registered by board-signer-ctl", role or None, kind or None)
    print(f"registered {acct} ({comment})" + (f" role={role}" if role else "") + (f" kind={kind}" if kind else ""))
elif cmd == "set-role":
    print(("updated " if db.board_signer_set_role(acct, role or None, kind or None) else "no such signer: ") + acct)
elif cmd == "deactivate":
    print(("deactivated " if db.board_signer_set_active(acct, False, arg3 or None) else "no such signer: ") + acct)
    from common import governance; governance.approval_signer_set_active(acct, False, arg3 or None)
elif cmd == "activate":
    print(("activated " if db.board_signer_set_active(acct, True, arg3 or None) else "no such signer: ") + acct)
    from common import governance; governance.approval_signer_set_active(acct, True, arg3 or None)
elif cmd == "revoke-tokens":
    r = db.board_revoke_all_for_account(acct); print(f"revoked all tokens of {acct}: {r}")
    from common import agent_gateway; agent_gateway.revoke_account(acct, "revoke-tokens")
elif cmd == "revoked-since":
    print(db.board_tokens_revoked_since(acct, float(arg3)))
elif cmd == "rename":
    with db.conn() as c:
        db._ensure_board_auth(c)
        r = c.execute("SELECT pubkey FROM board_signers WHERE account = ?", (acct,)).fetchone()
        if r:
            pub = r["pubkey"].replace(f" {acct}@corporatetraveldc-dispatch", f" {arg3}@corporatetraveldc-dispatch")
            c.execute("UPDATE board_signers SET account = ?, key_comment = ?, pubkey = ? WHERE account = ?",
                      (arg3, f"{arg3}@corporatetraveldc-dispatch", pub, acct))
            c.execute("UPDATE board_tokens SET label = ? WHERE label = ?", (arg3, acct))
    from common import governance
    with db.conn() as c:
        governance.ensure(c)
        c.execute("UPDATE workspace_grants SET account = ? WHERE account = ?", (arg3, acct))
        c.execute("UPDATE approval_signers SET account = ? WHERE account = ?", (arg3, acct))
    print(f"renamed signer {acct} -> {arg3}" if r else f"no such signer: {acct}")
elif cmd == "show":
    r = db.board_signer_get(acct)
    if r:
        print(("active" if r["active"] else "inactive"), r["role"], r["kind"]); print(r["pubkey"])
    else:
        print("none - -")
else:
    for r in db.board_signer_list():
        print(f"{r['account']:<20} {r['role']:<8} {r['kind']:<8} {'active' if r['active'] else 'INACTIVE':<9} {r['key_comment'] or ''}")
PYEOF
fi
