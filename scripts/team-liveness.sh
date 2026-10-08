#!/usr/bin/env bash
# scripts/team-liveness.sh -- dead-man switch + kill-order executor for team
# accounts. Operator directive 2026-10-04: "a stale login for an agent after
# the split, or a human reverting their login to /dev/null or no login, shuts
# them down and makes them inert" -- and, same day, the authorisation model:
# "an AND, not an OR from a liveness stance"; "three separate kills"; "a
# two-man or three-man human override of an admin".
#
# Evaluates every account in ctdc-agents (agent runtimes) and ctdc-ops (human
# teammates) -- never the operator (corporatetraveldc) and never root.
#
# LIVENESS IS AN AND. An account is alive only while ALL of these hold:
#   (a) LOGIN -- by the account's login_mode in /etc/ctdc-accounts.conf
#       ("name kind login_mode preloaded", written by plan.sh; an agent not
#       listed defaults to claude, a human to ssh):
#       AGENT/claude  ~/.claude/.credentials.json exists AND refreshTokenExpiresAt is in
#              the future AND expiresAt (the ~24h access token) is not older
#              than LIVENESS_AGENT_MAX_STALE (7d). Only the two expiry fields
#              are read -- never a token value.
#       AGENT/codex   ~/.codex/auth.json holds a refresh token AND its last_refresh is
#              not older than LIVENESS_CODEX_MAX_STALE (14d; Codex refreshes about
#              every 8 days) AND the Codex daemon runs as the account (its
#              remote-control unit active, or a codex process). Only the
#              timestamp and the presence of a refresh token are read -- never a
#              token value. (2026-10-08)
#       AGENT/ssh     no Claude creds (a client such as the Cowork desktop app
#              SSHes in): same shell/key/lock checks as a human, last SSH login
#              within LIVENESS_SSH_MAX_IDLE (7d).
#       SERVICE       no login factor at all (nologin shell, never a session):
#              alive = key AND token AND order only.
#       PRELOADED     (preloaded=1) skipped entirely, reported as "preloaded".
#       HUMAN  real login shell (not nologin, /dev/null, /bin/false), exactly
#              ONE authorized key whose comment starts "<name>@", not locked or
#              expired, last SSH login within LIVENESS_HUMAN_MAX_IDLE (14d);
#              LIVENESS_NEW_ACCOUNT_GRACE (3d) from creation for new accounts.
#   (b) KEY    the account's board_signers row exists and active=true
#              (board-signer-ctl.sh show). A missing row is tolerated only
#              inside the new-account grace (register happens at creation).
#   (c) TOKEN  none of the account's minted tokens was REVOKED in the last
#              LIVENESS_REVOCATION_LOOKBACK (24h) -- a deliberate revocation is
#              a kill signal, not a hint.
#   (d) ORDER  no executable kill order against it (quorum met, see below).
#   Any single failure -> INERT for everything.
#
# INERT (only with --execute --i-am-the-operator, root): terminate the user's
# sessions, stop its user-manager units, disable linger, usermod -L, chage -E
# 0 (all reversible), deactivate its board signer, REVOKE ALL its minted
# tokens, marker under /var/lib/corporatetraveldc/team-liveness/inert/, ntfy
# p4 (p5 for a kill order). Already-inert accounts are skipped silently.
# REACTIVATE re-enables login/linger/signer explicitly; tokens are NOT
# restored -- the operator mints new ones.
#
# KILL ORDERS (scripts/kill-order.sh): signed JSON files under
# <state>/orders/<target>/. Each is valid when: signature verifies against the
# issuer's registered key (namespace corporatetraveldc-kill), issuer alive and
# active, ts within 7d and not in the future, issuer != target, target is not
# the operator. Quorum: target role member/service or kind agent -> ONE valid
# admin issuer OR QUORUM_NON_ADMIN (2) distinct non-admin issuers; target role
# admin -> QUORUM_FOR_ADMIN (2; operator may set 3) distinct issuers of any
# role. Executed -> orders/<target>/executed/, invalid -> rejected/ (+.why),
# valid-but-short stay pending. ntfy p5 names the issuers.
#
# Usage:
#   team-liveness.sh                       dry-run: verdicts only, no action
#   team-liveness.sh --status              table of every account
#   team-liveness.sh --user NAME           evaluate one account
#   team-liveness.sh --execute --i-am-the-operator      act on verdicts (root)
#   team-liveness.sh --reactivate NAME --i-am-the-operator   reverse inert
#
# LIVENESS_FAKE_ROOT=<dir> (tests only) redirects every file read, the signer
# registry and every privileged command to that directory.
set -uo pipefail
# 2026-10-05 (argv-token sweep): a bearer token never goes on a command line --
# /proc/<pid>/cmdline is world-readable here (no hidepid), i.e. readable by
# every team account. authhdr NAME TOKEN puts the header on a private fd and
# sets NAME=(-H @/dev/fd/N) for ONE curl call (re-run it before each call).
authhdr() { local -n _ah="$1"; [[ -n "${_AUTHHDR_FD:-}" ]] && exec {_AUTHHDR_FD}<&-; exec {_AUTHHDR_FD}<<<"Authorization: Bearer $2"; _ah=(-H "@/dev/fd/${_AUTHHDR_FD}"); }

# 2026-10-04 (duel C1): root runs an INSTALLED copy from /usr/local/libexec/ctdc
# (scripts/install-root-copies.sh, root:root 0755), never the operator-writable
# checkout. The installed tree carries its own python lib (lib/common/board_sign.py);
# the repo path is used only for the signer-registry helper, which runs AS THE
# OPERATOR via runuser (operator privilege, not root).
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "${SELF_DIR}/.ctdc-installed" ]]; then
  REPO_ROOT="${CTDC_REPO_ROOT:-/opt/corporatetraveldc/private/ctdi-dispatch-internal}"; PY_SRC="${SELF_DIR}/lib"
else
  REPO_ROOT="$(cd "${SELF_DIR}/.." && pwd)"; PY_SRC="${REPO_ROOT}/src"
fi
AGENT_MAX_STALE="${LIVENESS_AGENT_MAX_STALE:-$((7*86400))}"
HUMAN_MAX_IDLE="${LIVENESS_HUMAN_MAX_IDLE:-$((14*86400))}"
SSH_MAX_IDLE="${LIVENESS_SSH_MAX_IDLE:-$((7*86400))}"
CODEX_MAX_STALE="${LIVENESS_CODEX_MAX_STALE:-$((14*86400))}"
NEW_GRACE="${LIVENESS_NEW_ACCOUNT_GRACE:-$((3*86400))}"
REVOKE_LOOKBACK="${LIVENESS_REVOCATION_LOOKBACK:-86400}"
ORDER_MAX_AGE="${LIVENESS_ORDER_MAX_AGE:-$((7*86400))}"
QUORUM_NON_ADMIN="${QUORUM_NON_ADMIN:-2}"
QUORUM_FOR_ADMIN="${QUORUM_FOR_ADMIN:-2}"
FAKE="${LIVENESS_FAKE_ROOT:-}"
STATE_DIR="/var/lib/corporatetraveldc/team-liveness"; [[ -n "$FAKE" ]] && STATE_DIR="${FAKE}/var/lib/corporatetraveldc/team-liveness"
INERT_DIR="${STATE_DIR}/inert"; ORDERS_DIR="${STATE_DIR}/orders"
REGISTRY="/etc/ctdc-accounts.conf"; [[ -n "$FAKE" ]] && REGISTRY="${FAKE}/accounts.conf"
HOOK_SCRIPT="${REPO_ROOT}/scripts/board-signer-ctl.sh"
ENV_FILE=/etc/corporatetraveldc/dispatch.env; SECRETS_FILE=/etc/corporatetraveldc/dispatch-secrets.env
OPERATOR="corporatetraveldc"
# The signer registry lives in the OPERATOR's rootless podman (postgres). When
# this runs as root (the system timer / .path unit) root cannot see those
# containers ("no container with name corporatetraveldc-pgsql", 2026-10-04
# 15:26), so every registry call drops to the operator with their runtime dir.
hook() {
  if [[ -n "${FAKE:-}" ]]; then "$HOOK_SCRIPT" "$@"; return; fi
  if (( EUID == 0 )); then
    runuser -u "$OPERATOR" -- env XDG_RUNTIME_DIR="/run/user/$(id -u "$OPERATOR")" DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$(id -u "$OPERATOR")/bus" "$HOOK_SCRIPT" "$@"
  else "$HOOK_SCRIPT" "$@"; fi
}
HOOK="$HOOK_SCRIPT"   # existence checks below use the path; calls go through hook()

EXECUTE=0; OPERATOR_OK=0; STATUS=0; ONE=""; REACT=""
while (( $# )); do
  case "$1" in
    --execute) EXECUTE=1 ;; --i-am-the-operator) OPERATOR_OK=1 ;;
    --status) STATUS=1 ;; --user) shift; ONE="${1:-}" ;; --reactivate) shift; REACT="${1:-}" ;;
    -h|--help) sed -n '2,52p' "$0"; exit 0 ;;
    *) echo "team-liveness: unknown argument '$1'" >&2; exit 64 ;;
  esac; shift
done
if (( EXECUTE )) && (( ! OPERATOR_OK )); then echo "team-liveness: --execute needs --i-am-the-operator" >&2; exit 3; fi
if (( EXECUTE )) && [[ -z "$FAKE" && $(id -u) -ne 0 ]]; then echo "team-liveness: --execute needs root (system timer or sudo)" >&2; exit 4; fi
if [[ -n "$REACT" ]] && (( ! OPERATOR_OK )); then echo "team-liveness: --reactivate needs --i-am-the-operator" >&2; exit 3; fi

log() { printf '[%s] [%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$1" "$2"; }
now=$(date +%s)
read_env_var() { local key="$1" file="$2"; [[ -f "$file" ]] || return 0; grep -m1 "^${key}=" "$file" 2>/dev/null | cut -d'=' -f2-; }
NTFY_BASE="$(read_env_var NTFY_BASE_URL "$ENV_FILE")"; NTFY_BASE="${NTFY_BASE:-http://127.0.0.1:2586}"
NTFY_OPS="$(read_env_var NTFY_OPS_TOPIC "$ENV_FILE")";  NTFY_OPS="${NTFY_OPS:-ops-health}"
NTFY_TOKEN="$(read_env_var NTFY_TOKEN "$SECRETS_FILE")"
ntfy_send() { local title="$1" msg="$2" priority="${3:-2}"; local auth=()
  [[ -n "$FAKE" ]] && { echo "ntfy ${priority} ${title} -- ${msg}" >>"${FAKE}/actions.log"; return 0; }
  [[ -n "$NTFY_TOKEN" ]] && authhdr auth "${NTFY_TOKEN}"
  curl -sf --max-time 5 "${auth[@]}" -H "Title: ${title}" -H "Priority: ${priority}" -H "Tags: skull" -d "${msg}" "${NTFY_BASE}/${NTFY_OPS}" >/dev/null 2>&1 || true; }

# ---------------------------------------------------------------- probes ----
# Every probe has a FAKE path so the verdict logic is unit-testable without
# root or real accounts.
# registry refinement: "kind login_mode preloaded" for a name, with defaults
# derived from the group (agents: agent claude 0; humans: human ssh 0).
# In FAKE mode accounts.txt may carry the same three columns after the shell.
registry_of() {   # <name> <group> -> "kind mode preloaded"
  local r=""
  if [[ -n "$FAKE" ]]; then r=$(awk -v u="$1" '$1==u && NF>=6 {print $4, $5, $6}' "${FAKE}/accounts.txt" 2>/dev/null | tail -1)
  elif [[ -r "$REGISTRY" ]]; then r=$(awk -v u="$1" '$1==u {print $2, $3, $4}' "$REGISTRY" 2>/dev/null | tail -1); fi
  if [[ -z "$r" ]]; then [[ "$2" == ctdc-agents ]] && r="agent claude 0" || r="human ssh 0"; fi
  echo "$r"
}
accounts() {   # prints "name group kind mode preloaded"
  local g m u
  if [[ -n "$FAKE" ]]; then
    while read -r u g _; do [[ -n "$u" && "$u" != \#* ]] && echo "$u $g $(registry_of "$u" "$g")"; done < <(awk 'NF>=2 {print $1, $2, $3}' "${FAKE}/accounts.txt" 2>/dev/null); return
  fi
  for g in ctdc-agents ctdc-ops; do
    m=$(getent group "$g" 2>/dev/null | cut -d: -f4 | tr ',' ' ')
    for u in $m; do [[ "$u" == "$OPERATOR" || "$u" == root ]] && continue; echo "$u $g $(registry_of "$u" "$g")"; done
  done
}
home_of()   { if [[ -n "$FAKE" ]]; then echo "${FAKE}/home/$1"; else getent passwd "$1" | cut -d: -f6; fi; }
shell_of()  { if [[ -n "$FAKE" ]]; then awk -v u="$1" '$1==u {print $3}' "${FAKE}/accounts.txt"; else getent passwd "$1" | cut -d: -f7; fi; }
# 2026-10-05: "locked" means a DELIBERATE lock (usermod -L: "!" in front of a
# real hash). `passwd -S` also reports L/LK for an account that never had a
# password ("!!" / "!" -- the useradd default), which is every key-only team
# account: ctdc-agent-cowork went inert 06:12 today, 30 min after creation,
# for "account locked" while nothing had locked it. Read the shadow field
# instead (root); the real kill switch is chage -E 0, caught by expired().
locked()    { if [[ -n "$FAKE" ]]; then [[ "$(cat "${FAKE}/passwd-S/$1" 2>/dev/null)" == L ]]
              else local f; f=$(getent shadow "$1" 2>/dev/null | cut -d: -f2); [[ "$f" == '!'* ]] || return 1
                   f="${f#\!}"; f="${f#\!}"; [[ -n "$f" && "$f" != '*' ]]; fi; }
expired()   { local e
  if [[ -n "$FAKE" ]]; then e=$(cat "${FAKE}/expire/$1" 2>/dev/null || echo never)
  else e=$(chage -l "$1" 2>/dev/null | awk -F': ' '/Account expires/ {print $2}'); fi
  [[ -z "$e" || "$e" == never ]] && return 1
  local es; es=$(date -d "$e" +%s 2>/dev/null || echo 0); (( es > 0 && es <= now )); }
created_at() { if [[ -n "$FAKE" ]]; then cat "${FAKE}/created/$1" 2>/dev/null || echo 0
  else local h; h=$(home_of "$1"); stat -c %W "$h" 2>/dev/null | grep -vE '^(0|-)' || stat -c %Y "$h" 2>/dev/null || echo 0; fi; }
last_login() {   # epoch of the account's last login, 0 if unknown
  # 2026-10-04 (duel U5/H5): Fedora 44 ships lastlog2, not lastlog, and the
  # journal fallback ages out. Preference: a root-written PAM state file
  # (runbook: pam_exec session hook), then lastlog2, then the sshd journal.
  local f="${STATE_DIR}/lastlogin/$1"
  if [[ -n "$FAKE" ]]; then
    [[ -f "$f" ]] && { cat "$f"; return; }
    [[ -f "${FAKE}/lastlog2/$1" ]] && { parse_lastlog2 <"${FAKE}/lastlog2/$1"; return; }
    cat "${FAKE}/lastlogin/$1" 2>/dev/null || echo 0; return
  fi
  if [[ -f "$f" && ! -L "$f" && "$(stat -c %u "$f")" == 0 ]]; then grep -m1 -oE '^[0-9]+' "$f" && return; fi
  local l; l=$(lastlog2 -u "$1" 2>/dev/null | parse_lastlog2)
  [[ -n "$l" && "$l" != 0 ]] && { echo "$l"; return; }
  journalctl -u sshd --since "30 days ago" --no-pager -o short-unix 2>/dev/null | grep -E "Accepted publickey for $1 " | tail -1 | cut -d. -f1 | grep -E '^[0-9]+$' || echo 0; }
parse_lastlog2() {   # stdin: `lastlog2 -u NAME` output -> epoch or 0
  # format: "Username  Port  From  Latest" header, then e.g.
  # "ctdc-agent-cowork  pts/1  192.0.2.7  Sun Oct  4 16:10:29 -0400 2026"
  # or "... **Never logged in**"
  awk 'NR==2 { if ($0 ~ /Never logged in/) { print 0; exit } n=split($0,a," "); \
       if (n>=8) { print a[n-5]" "a[n-4]" "a[n-3]" "a[n-2]" "a[n-1]" "a[n]; exit } } END{}' | \
    { read -r d || { echo 0; return; }; [[ "$d" == 0 ]] && { echo 0; return; }; date -d "$d" +%s 2>/dev/null || echo 0; }
}
cred_expiry() {  # prints "expiresAt refreshTokenExpiresAt" in epoch seconds, or nothing
  local f="$(home_of "$1")/.claude/.credentials.json"; [[ -r "$f" ]] || return 1
  python3 - "$f" <<'PYEOF'
import json, sys
try:
    d = json.load(open(sys.argv[1])); o = d.get("claudeAiOauth", d)
    e = int(o.get("expiresAt") or 0); r = int(o.get("refreshTokenExpiresAt") or 0)
    print(e // 1000 if e > 10**11 else e, r // 1000 if r > 10**11 else r)   # ms -> s
except Exception:
    sys.exit(1)
PYEOF
}
codex_last_refresh() {  # prints last_refresh in epoch seconds; fails without a usable login
  local f="$(home_of "$1")/.codex/auth.json"; [[ -r "$f" ]] || return 1
  python3 - "$f" <<'PYEOF'
import datetime, json, re, sys
try:
    d = json.load(open(sys.argv[1]))
    if not (d.get("tokens") or {}).get("refresh_token"):
        sys.exit(1)                                   # API-key mode or logged out: no refreshing login
    s = re.sub(r"(\.\d{6})\d+", r"\1", str(d["last_refresh"])).replace("Z", "+00:00")
    print(int(datetime.datetime.fromisoformat(s).timestamp()))
except Exception:
    sys.exit(1)
PYEOF
}
codex_corroboration() {  # active | inactive | unknown  (same evidence rules as claude_corroboration)
  local name="$1"
  if [[ -n "$FAKE" ]]; then
    [[ -f "${FAKE}/codex-unit/${name}" ]] && cat "${FAKE}/codex-unit/${name}" || echo inactive; return
  fi
  if (( EUID == 0 )); then
    local st; st=$(runuser -u "$name" -- env XDG_RUNTIME_DIR="/run/user/$(id -u "$name")" systemctl --user is-active "$CODEX_RC_UNIT" 2>/dev/null)
    [[ "$st" == active ]] && { echo active; return; }
  fi
  pgrep -u "$name" -x codex >/dev/null 2>&1 && { echo active; return; }
  (( EUID == 0 )) && echo inactive || echo unknown
}
# signer registry: "active|inactive|none role kind" or "unavailable" (infra
# failure -- never a reason to kill)
signer_state() {
  if [[ -n "$FAKE" ]]; then
    [[ -d "${FAKE}/signers" ]] || { echo unavailable; return; }
    [[ -f "${FAKE}/signers/$1" ]] && head -1 "${FAKE}/signers/$1" || echo "none - -"; return
  fi
  [[ -x "$HOOK" ]] || { echo unavailable; return; }
  local out; out=$(hook show "$1" 2>/dev/null) || { echo unavailable; return; }
  printf '%s\n' "$out" | head -1 | grep -E '^(active|inactive|none)' || echo "none - -"
}
signer_pubkey() {
  if [[ -n "$FAKE" ]]; then cat "${FAKE}/signers/$1.pub" 2>/dev/null; return; fi
  hook show "$1" 2>/dev/null | sed -n 2p
}
revoked_since() {   # count of the account's tokens revoked after $2
  if [[ -n "$FAKE" ]]; then local r; r=$(cat "${FAKE}/revoked/$1" 2>/dev/null || echo 0); (( r > $2 )) && echo 1 || echo 0; return; fi
  [[ -x "$HOOK" ]] || { echo 0; return; }
  hook revoked-since "$1" "$2" 2>/dev/null | tr -d ' ' | grep -E '^[0-9]+$' || echo 0
}
days() { awk -v s="$1" 'BEGIN{printf "%.1fd", s/86400}'; }
RC_UNIT="corporatetraveldc-claude-remote-control.service"
CODEX_RC_UNIT="corporatetraveldc-codex-remote-control.service"
# 2026-10-04 (duel M9/U5): the credentials file is owned and writable by the
# agent itself, so it cannot be the only proof of login. Once the account HAS
# a remote-control unit, that unit must be active (or a claude process must be
# running as the account). Prints active|inactive|none-unit|unknown.
claude_corroboration() {
  local name="$1"
  if [[ -n "$FAKE" ]]; then
    [[ -f "${FAKE}/rc-unit/${name}" ]] || { echo none-unit; return; }
    cat "${FAKE}/rc-unit/${name}"; return
  fi
  local h; h=$(home_of "$name")
  [[ -f "${h}/.config/systemd/user/${RC_UNIT}" ]] 2>/dev/null || { (( EUID == 0 )) && { echo none-unit; return; }; echo unknown; return; }
  if (( EUID == 0 )); then
    local st; st=$(runuser -u "$name" -- env XDG_RUNTIME_DIR="/run/user/$(id -u "$name")" systemctl --user is-active "$RC_UNIT" 2>/dev/null)
    [[ "$st" == active ]] && { echo active; return; }
  fi
  pgrep -u "$name" -x claude >/dev/null 2>&1 && { echo active; return; }
  (( EUID == 0 )) && echo inactive || echo unknown
}

# ---------------------------------------------------------------- verdict ----
# sets VERDICT (live|stale) REASON DETAIL. $3 = "nokill" skips factor (d)
# (used while validating the ISSUER of a kill order, to avoid recursion).
evaluate() {
  local name="$1" group="$2" mode="${3:-}"; VERDICT=live; REASON=""; DETAIL=""
  local h; h=$(home_of "$name")
  local cr; cr=$(created_at "$name")
  local reg; reg=$(registry_of "$name" "$group"); local kind=${reg%% *} lmode pre
  lmode=$(echo "$reg" | awk '{print $2}'); pre=$(echo "$reg" | awk '{print $3}')
  if [[ "$pre" == 1 ]]; then VERDICT=preloaded; REASON="preloaded -- not yet activated (plan.sh --activate ${name})"; DETAIL="kind=${kind} mode=${lmode}"; return; fi
  DETAIL="kind=${kind}/${lmode}"
  # (a) LOGIN -- by login mode
  if [[ "$kind" == service || "$lmode" == none ]]; then
    DETAIL="${DETAIL} login=n/a(service)"
  elif [[ "$group" == ctdc-agents && "$lmode" == codex ]]; then
    local lr
    if ! lr=$(codex_last_refresh "$name"); then
      local why="never"; (( EUID != 0 )) && [[ -z "${FAKE:-}" ]] && why="unreadable-unprivileged (run with sudo for the real verdict)"
      if (( cr > 0 && now - cr <= NEW_GRACE )); then DETAIL="${DETAIL} login=${why} (new agent, grace $(days $((NEW_GRACE-(now-cr)))) left -- run: sudo -u ${name} -i codex login --device-auth)"
      else VERDICT=stale; REASON="no usable Codex login (~/.codex/auth.json missing, unreadable, or without a refresh token)"; DETAIL="refresh=n/a"; return; fi
    else
      if (( now - lr > CODEX_MAX_STALE )); then VERDICT=stale; REASON="Codex login last refreshed $(days $((now-lr))) ago (> $(days $CODEX_MAX_STALE))"; DETAIL="refresh=$(days $((now-lr)))"; return; fi
      DETAIL="${DETAIL} refresh=$(days $(( now-lr < 0 ? 0 : now-lr )))"
      case "$(codex_corroboration "$name")" in
        active)   DETAIL="${DETAIL} session=active" ;;
        unknown)  DETAIL="${DETAIL} session=?(unprivileged)" ;;
        inactive) VERDICT=stale; REASON="Codex login looks fresh but neither its remote-control unit nor a codex process runs as ${name} (the login file is self-attested)"; DETAIL="${DETAIL} session=INACTIVE"; return ;;
      esac
    fi
  elif [[ "$group" == ctdc-agents && "$lmode" == claude ]]; then
    local ce
    if ! ce=$(cred_expiry "$name"); then
      # 2026-10-04 15:23: a freshly created agent has no credentials until its
      # first interactive `claude` login; the .path trigger killed ctdc-agent
      # seconds after stage A. New-account grace applies to agents too.
      local why="never"; (( EUID != 0 )) && [[ -z "${FAKE:-}" ]] && why="unreadable-unprivileged (run with sudo for the real verdict)"
      if (( cr > 0 && now - cr <= NEW_GRACE )); then DETAIL="${DETAIL} login=${why} (new agent, grace $(days $((NEW_GRACE-(now-cr)))) left -- run: sudo -u ${name} -i claude)"
      else VERDICT=stale; REASON="no readable credentials file"; DETAIL="refresh=n/a"; return; fi
    else
    local exp=${ce% *} ref=${ce#* }
    if (( ref <= now )); then VERDICT=stale; REASON="refresh token expired $(days $((now-ref))) ago"; DETAIL="refresh=expired"; return; fi
    if (( now - exp > AGENT_MAX_STALE )); then VERDICT=stale; REASON="access token last refreshed $(days $((now-exp))) ago (> $(days $AGENT_MAX_STALE))"; DETAIL="refresh=$(days $((now-exp)))"; return; fi
      DETAIL="${DETAIL} refresh=$(days $(( now-exp < 0 ? 0 : now-exp )))"
      case "$(claude_corroboration "$name")" in
        active)    DETAIL="${DETAIL} session=active" ;;
        none-unit) DETAIL="${DETAIL} session=no-unit(creds only)" ;;
        unknown)   DETAIL="${DETAIL} session=?(unprivileged)" ;;
        inactive)  VERDICT=stale; REASON="credentials look fresh but the remote-control unit is not active and no claude process runs as ${name} (credentials file is self-attested)"; DETAIL="${DETAIL} session=INACTIVE"; return ;;
      esac
    fi
  else
    # humans, and agents in login-mode ssh (the client SSHes in): shell, one
    # key named after the account, not locked/expired, recent login
    local max_idle=$HUMAN_MAX_IDLE; [[ "$group" == ctdc-agents ]] && max_idle=$SSH_MAX_IDLE
    local sh; sh=$(shell_of "$name")
    case "$sh" in *nologin|/dev/null|/bin/false|"") VERDICT=stale; REASON="login shell is '${sh:-empty}'"; DETAIL="shell"; return ;; esac
    local ak="${h}/.ssh/authorized_keys" nkeys=0
    [[ -r "$ak" ]] && nkeys=$(grep -cE '^(ssh|ecdsa)-' "$ak")
    if (( nkeys != 1 )); then VERDICT=stale; REASON="authorized_keys has ${nkeys} key(s), need exactly 1"; DETAIL="keys=${nkeys}"; return; fi
    if ! grep -E '^(ssh|ecdsa)-' "$ak" | awk '{print $NF}' | grep -qE "^${name}@"; then VERDICT=stale; REASON="key comment does not start with ${name}@"; DETAIL="keys=1"; return; fi
    if locked "$name"; then VERDICT=stale; REASON="account locked"; DETAIL="locked"; return; fi
    if expired "$name"; then VERDICT=stale; REASON="account expired"; DETAIL="expired"; return; fi
    local ll; ll=$(last_login "$name")
    if (( ll > 0 )); then
      DETAIL="${DETAIL} login=$(days $((now-ll)))"
      (( now - ll > max_idle )) && { VERDICT=stale; REASON="no login for $(days $((now-ll))) (> $(days $max_idle))"; return; }
    else
      if (( cr > 0 && now - cr <= NEW_GRACE )); then DETAIL="${DETAIL} login=never (new, grace $(days $((NEW_GRACE-(now-cr)))) left)"
      else VERDICT=stale; REASON="never logged in and past the new-account grace"; DETAIL="login=never"; return; fi
    fi
  fi
  # (b) KEY -- registered signer must exist and be active
  local st; st=$(signer_state "$name"); local sflag=${st%% *}
  case "$sflag" in
    active) DETAIL="${DETAIL} key=active" ;;
    inactive) VERDICT=stale; REASON="board signer deactivated (key revoked)"; DETAIL="${DETAIL} key=INACTIVE"; return ;;
    none) if (( cr > 0 && now - cr <= NEW_GRACE )); then DETAIL="${DETAIL} key=unregistered(grace)"
          else VERDICT=stale; REASON="no board signer registered (past the new-account grace)"; DETAIL="${DETAIL} key=none"; return; fi ;;
    *) # 2026-10-04 (duel M7): never skip the key factor. An unreachable registry
       # means we cannot prove the key is live -- HOLD: no inert, no pass.
       VERDICT=hold; REASON="signer registry unavailable -- verdict held (no inert, no pass)"; DETAIL="${DETAIL} key=?"; return ;;
  esac
  # (c) TOKEN -- a revocation inside the lookback is a kill signal
  local rv; rv=$(revoked_since "$name" $((now - REVOKE_LOOKBACK)))
  if (( rv > 0 )); then VERDICT=stale; REASON="${rv} token(s) revoked within the last $(days $REVOKE_LOOKBACK)"; DETAIL="${DETAIL} token=REVOKED"; return; fi
  # (d) ORDER -- an executable kill order
  if [[ "$mode" != nokill ]] && [[ -n "${KILL_READY[$name]:-}" ]]; then
    VERDICT=stale; REASON="kill order executed: ${KILL_READY[$name]}"; DETAIL="${DETAIL} order=EXECUTE"; return
  fi
}

is_inert() { [[ -f "${INERT_DIR}/$1" ]]; }

act() {  # privileged commands; in FAKE mode they are recorded, not run
  if [[ -n "$FAKE" ]]; then echo "$*" >>"${FAKE}/actions.log"; return 0; fi
  "$@"
}
make_inert() {
  local name="$1" reason="$2" prio="${3:-4}"
  log warn "INERT ${name}: ${reason}"
  local k; k=$(accounts | awk -v u="$name" '$1==u {print $3}')
  if [[ "$k" != service ]]; then   # a service has no sessions, no user manager, no linger
    act loginctl terminate-user "$name"
    act runuser -u "${name}" -- env XDG_RUNTIME_DIR="/run/user/$(id -u "${name}")" systemctl --user stop '*.service' '*.timer'
    act loginctl disable-linger "$name"
  fi
  act usermod -L "$name" 2>/dev/null || true   # may already be '!' (passwordless); chage -E 0 below is the real lock
  act chage -E 0 "$name"
  if [[ -x "$HOOK" || -n "$FAKE" ]]; then act hook deactivate "$name" "inert: ${reason:0:80}"; act hook revoke-tokens "$name"
  else log info "board-signer-ctl.sh not present -- signer deactivation / token revocation skipped for ${name}"; fi
  mkdir -p "$INERT_DIR" && printf '%s inert: %s\n' "$(date -Is)" "$reason" >"${INERT_DIR}/${name}"
  ntfy_send "team-liveness: ${name} made INERT" "${reason}. Reactivate: team-liveness.sh --reactivate ${name} --i-am-the-operator" "$prio"
}
reactivate() {
  local name="$1"
  log info "REACTIVATE ${name}"
  act chage -E -1 "$name"
  act usermod -U "$name" 2>/dev/null || act usermod -p '*' "$name"   # passwordless account: '*' = no password, not "locked"
  local row; row=$(accounts | awk -v u="$name" '$1==u {print $2, $3, $4}'); local grp=${row%% *}; local lm; lm=$(echo "$row" | awk '{print $3}')
  [[ "$(echo "$row" | awk '{print $2}')" == service ]] || act loginctl enable-linger "$name"
  if [[ -x "$HOOK" || -n "$FAKE" ]]; then act hook activate "$name" "reactivated by operator"; fi
  rm -f "${INERT_DIR}/${name}"
  # 2026-10-04 (duel M7): pending orders against the account would re-kill it
  # on the next run -- move them to the archive as superseded by this decision.
  archive_orders "$name" superseded
  echo "note: tokens revoked at inert are NOT restored -- mint new ones for ${name} if it needs any"
  case "${lm:-}" in
    none)   echo "next: service identity -- live again as soon as its signer is active (done above)" ;;
    claude) echo "next: the agent must log in again:  sudo -u ${name} -i claude   (then /exit)" ;;
    codex)  echo "next: the agent must log in again:  sudo -u ${name} -i codex login --device-auth   then: runuser -u ${name} -- env XDG_RUNTIME_DIR=/run/user/\$(id -u ${name}) systemctl --user restart ${CODEX_RC_UNIT}" ;;
    *)      echo "next: ${name} must SSH in with their single registered key within $(days $NEW_GRACE)" ;;
  esac
}

# ------------------------------------------------------------ kill orders ----
# Fills KILL_READY[target]="issuer1,issuer2" for targets whose quorum is met.
declare -A KILL_READY
verify_order() {   # $1 json $2 sig $3 issuer -> rc 0/1; uses board_sign.verify in the kill namespace
  local pub; pub=$(signer_pubkey "$3"); [[ -n "$pub" ]] || return 1
  PYTHONPATH="${PY_SRC}" python3 - "$1" "$2" "$pub" "$3" <<'PYEOF'
import base64, sys
from common import board_sign as bs
json_path, sig_path, pub, issuer = sys.argv[1:5]
try:
    msg = open(json_path, "rb").read()
    armored = open(sig_path, "r").read()
    ok = bs.verify(pub, base64.b64encode(armored.encode()).decode(), msg, issuer, namespace=bs.KILL_NAMESPACE)
except Exception:
    ok = False
sys.exit(0 if ok else 1)
PYEOF
}
order_fields() {   # prints "issuer target ts" from the canonical json, or nothing
  python3 - "$1" <<'PYEOF'
import json, sys
try:
    d = json.load(open(sys.argv[1]))
    print(d["issuer"], d["target"], int(d["ts"]))
except Exception:
    sys.exit(1)
PYEOF
}
# 2026-10-04 (duel H5/X2/M8): orders/ is group-writable drop space. Root never
# acts inside it: each order is checked and MOVED into a root-only staging
# tree first, and executed/rejected archives live under a root-only tree.
# Target subdirs are created by root; a target dir not owned by root (a squat)
# has its contents rejected. Only `--execute` as root mutates anything;
# --status / --user / dry-run only report what WOULD happen.
# 2026-10-04 20:05 incident: the root-only staging/archive trees must live
# OUTSIDE /var/lib/corporatetraveldc -- 53 rootless quadlets bind-mount that
# tree with :z, podman relabels it recursively, and a root 0700 dir inside
# it made every container start fail (exit 126, "open ...: permission denied").
ROOT_STATE_DIR="/var/lib/ctdc-liveness"; [[ -n "$FAKE" ]] && ROOT_STATE_DIR="${FAKE}/var/lib/ctdc-liveness"
STAGING="${ROOT_STATE_DIR}/staging"; ARCHIVE="${ROOT_STATE_DIR}/orders-archive"
ORDER_MAX_FILES="${LIVENESS_ORDER_MAX_FILES:-50}"; ORDER_MAX_BYTES="${LIVENESS_ORDER_MAX_BYTES:-65536}"
ROOT_UID=0; [[ -n "$FAKE" ]] && ROOT_UID="${LIVENESS_FAKE_ROOT_UID:-$(id -u)}"   # tests may pretend dirs are not root-owned
MUTATE=0; (( EXECUTE )) && { [[ -n "$FAKE" ]] || (( EUID == 0 )); } && MUTATE=1
owner_ok() {   # file owner is the operator or a ctdc-dev member (FAKE: any)
  [[ -n "$FAKE" ]] && return 0
  local u; u=$(stat -c %U "$1" 2>/dev/null) || return 1
  [[ "$u" == "$OPERATOR" ]] && return 0
  id -nG "$u" 2>/dev/null | tr ' ' '\n' | grep -qx ctdc-dev
}
archive_move() {   # $1 kind(executed|rejected|superseded) $2 target $3.. files
  local kind="$1" target="$2"; shift 2
  local d="${ARCHIVE}/${kind}/${target}"; install -d -m 0700 "$d" 2>/dev/null || mkdir -p "$d"
  local f; for f in "$@"; do [[ -e "$f" || -L "$f" ]] || continue
    if [[ -L "$f" ]]; then rm -f -- "$f"; else mv -f -- "$f" "$d/"; fi; done
}
reject_order() {  # $1 base path (no ext) $2 why
  local base="$1" why="$2" target; target=$(basename "$(dirname "$base")")
  if (( ! MUTATE )); then log warn "kill order $(basename "$base") would be REJECTED: ${why}"; return; fi
  log warn "kill order $(basename "$base") REJECTED: ${why}"
  archive_move rejected "$target" "${base}.json" "${base}.sig"
  printf '%s %s\n' "$(date -Is)" "$why" >"${ARCHIVE}/rejected/${target}/$(basename "$base").why"
}
prepare_order_dirs() {   # root creates a target dir for every team account
  [[ -d "$ORDERS_DIR" && ! -L "$ORDERS_DIR" ]] || return 0
  install -d -m 0700 "$ROOT_STATE_DIR" "$STAGING" "$ARCHIVE" 2>/dev/null || mkdir -p "$STAGING" "$ARCHIVE"
  local row name; for row in "${ROWS[@]}"; do name=${row%% *}
    [[ -e "${ORDERS_DIR}/${name}" || -L "${ORDERS_DIR}/${name}" ]] && continue
    if [[ -n "$FAKE" ]]; then mkdir -p "${ORDERS_DIR}/${name}"
    else install -d -m 3775 -o root -g ctdc-dev "${ORDERS_DIR}/${name}"; fi
  done
}
stage_orders() {   # move valid drops into ${STAGING}/<target>/ (MUTATE only)
  [[ -d "$ORDERS_DIR" && ! -L "$ORDERS_DIR" ]] || return 0
  local tdir target f base n=0 over=0
  for tdir in "$ORDERS_DIR"/*; do
    target=$(basename "$tdir"); [[ "$target" == .new ]] && continue
    if [[ -L "$tdir" || ! -d "$tdir" ]]; then log warn "orders/${target} is not a real directory -- removed"; rm -f -- "$tdir"; continue; fi
    if [[ "$(stat -c %u "$tdir")" != "$ROOT_UID" ]]; then
      for f in "$tdir"/*; do [[ -e "$f" || -L "$f" ]] || continue
        base="${f%.*}"; reject_order "$base" "target dir orders/${target} was not created by root (squat)"; done
      continue
    fi
    for f in "$tdir"/*.json; do
      [[ -e "$f" || -L "$f" ]] || continue; base="${f%.json}"
      n=$((n+1)); if (( n > ORDER_MAX_FILES )); then over=$((over+1)); reject_order "$base" "per-run cap of ${ORDER_MAX_FILES} orders exceeded"; continue; fi
      if [[ -L "$f" || ! -f "$f" || -L "${base}.sig" ]]; then reject_order "$base" "symlink or non-regular file"; continue; fi
      [[ -f "${base}.sig" ]] || { reject_order "$base" "no signature file"; continue; }
      if (( $(stat -c %s "$f") > ORDER_MAX_BYTES || $(stat -c %s "${base}.sig") > ORDER_MAX_BYTES )); then reject_order "$base" "file larger than ${ORDER_MAX_BYTES} bytes"; continue; fi
      owner_ok "$f" && owner_ok "${base}.sig" || { reject_order "$base" "file not owned by a ctdc-dev member"; continue; }
      local sd="${STAGING}/${target}"; install -d -m 0700 "$sd" 2>/dev/null || mkdir -p "$sd"
      mv -f -- "$f" "${base}.sig" "$sd/"
      # re-check after the move: staging is root-only, so nothing can swap it now
      if [[ -L "$sd/$(basename "$f")" || ! -f "$sd/$(basename "$f")" ]]; then reject_order "$sd/$(basename "$base")" "changed type during staging"; fi
    done
    # stray non-order files in a root-owned target dir (other than .sig pairs) are left alone
  done
  (( over )) && ntfy_send "team-liveness: kill-order flood" "${over} order(s) over the per-run cap of ${ORDER_MAX_FILES} were rejected" 4
}
process_orders() {   # evaluates staged orders (execute) or drop-box orders read-only (dry-run)
  local src; if (( MUTATE )); then src="$STAGING"; else src="$ORDERS_DIR"; fi
  [[ -d "$src" ]] || return 0
  local tdir target f base fields issuer tgt ts st role kind
  for tdir in "$src"/*/; do
    [[ -d "$tdir" && ! -L "${tdir%/}" ]] || continue; target=$(basename "$tdir")
    if (( ! MUTATE )) && [[ "$(stat -c %u "$tdir")" != "$ROOT_UID" ]]; then log warn "orders/${target}: not root-created -- its orders would be rejected (squat)"; continue; fi
    local -A seen=(); local admins=0 others=0 names=""
    for f in "$tdir"/*.json; do
      [[ -f "$f" && ! -L "$f" ]] || continue; base="${f%.json}"
      [[ -f "${base}.sig" ]] || { reject_order "$base" "no signature file"; continue; }
      if ! fields=$(order_fields "$f"); then reject_order "$base" "unparseable"; continue; fi
      read -r issuer tgt ts <<<"$fields"
      [[ "$tgt" == "$target" ]] || { reject_order "$base" "target mismatch (${tgt} vs dir ${target})"; continue; }
      [[ "$issuer" != "$target" ]] || { reject_order "$base" "issuer is the target"; continue; }
      [[ "$target" != "$OPERATOR" && "$target" != root ]] || { reject_order "$base" "the operator is never a kill-order target"; continue; }
      (( ts <= now + 300 && now - ts <= ORDER_MAX_AGE )) || { reject_order "$base" "timestamp stale or in the future"; continue; }
      [[ "$(basename "$base")" == "${issuer}."* ]] || { reject_order "$base" "file name does not match issuer"; continue; }
      st=$(signer_state "$issuer"); read -r _ role kind <<<"$st"
      [[ "$st" != unavailable ]] || { log warn "kill order $(basename "$base"): registry unavailable -- held"; continue; }
      [[ "${st%% *}" == active ]] || { reject_order "$base" "issuer ${issuer} has no active signer"; continue; }
      local igrp; igrp=$(accounts | awk -v u="$issuer" '$1==u {print $2}')
      if [[ -n "$igrp" ]]; then evaluate "$issuer" "$igrp" nokill
        [[ "$VERDICT" == hold ]] && { log warn "kill order $(basename "$base"): issuer verdict held -- order kept"; continue; }
        [[ "$VERDICT" == live ]] || { reject_order "$base" "issuer ${issuer} is not alive: ${REASON}"; continue; }
      elif [[ "$issuer" != "$OPERATOR" ]]; then reject_order "$base" "issuer ${issuer} is not a team account"; continue; fi
      verify_order "$f" "${base}.sig" "$issuer" || { reject_order "$base" "signature does not verify"; continue; }
      [[ -n "${seen[$issuer]:-}" ]] && continue   # one vote per issuer
      seen[$issuer]=1; names="${names:+$names,}${issuer}"
      if [[ "$role" == admin ]]; then admins=$((admins+1)); else others=$((others+1)); fi
    done
    (( ${#seen[@]} )) || continue
    local tst trole tkind need=""; tst=$(signer_state "$target"); read -r _ trole tkind <<<"$tst"
    if [[ "$trole" == admin ]]; then
      (( admins + others >= QUORUM_FOR_ADMIN )) && KILL_READY[$target]="$names" || need="admin target: ${admins}+${others} of ${QUORUM_FOR_ADMIN} distinct issuers"
    else
      if (( admins >= 1 )) || (( others >= QUORUM_NON_ADMIN )); then KILL_READY[$target]="$names"
      else need="${others} of ${QUORUM_NON_ADMIN} non-admin issuers (or one admin)"; fi
    fi
    [[ -n "$need" ]] && log info "kill order(s) against ${target} pending: ${need}"
  done
}
archive_orders() {  # $1 target [$2 kind] -> move its staged (and dropped) orders to the archive
  local t="$1" kind="${2:-executed}"; (( MUTATE )) || [[ "$kind" == superseded ]] || return 0
  shopt -s nullglob
  archive_move "$kind" "$t" "${STAGING}/${t}"/*.json "${STAGING}/${t}"/*.sig
  [[ -d "${ORDERS_DIR}/${t}" && ! -L "${ORDERS_DIR}/${t}" && "$(stat -c %u "${ORDERS_DIR}/${t}")" == "$ROOT_UID" ]] && \
    archive_move "$kind" "$t" "${ORDERS_DIR}/${t}"/*.json "${ORDERS_DIR}/${t}"/*.sig
  shopt -u nullglob
}

# ------------------------------------------------------------------ main ----
if [[ -n "$REACT" ]]; then reactivate "$REACT"; exit 0; fi

# 2026-10-05: the operator dead-man for cloud-agent sessions
# (common/agent_gateway.operator_seen_ok): record the operator's last login
# where the web container can read it. 0644, operator-readable (nothing under
# /var/lib/corporatetraveldc may be unreadable to the operator).
if (( EXECUTE )) && [[ -z "$FAKE" ]]; then
  _op_seen=$(last_login corporatetraveldc 2>/dev/null || echo 0)
  if [[ "$_op_seen" =~ ^[0-9]+$ ]] && (( _op_seen > 0 )) && [[ ! -L "${STATE_DIR}/operator-last-login" ]]; then
    printf '%s\n' "$_op_seen" > "${STATE_DIR}/operator-last-login.tmp" && chmod 0644 "${STATE_DIR}/operator-last-login.tmp" \
      && mv -f "${STATE_DIR}/operator-last-login.tmp" "${STATE_DIR}/operator-last-login"
  fi
fi

mapfile -t ROWS < <(accounts)
if (( MUTATE )); then prepare_order_dirs; stage_orders; fi
process_orders
if (( ${#ROWS[@]} == 0 )); then log info "no team accounts yet (groups ctdc-agents / ctdc-ops empty) -- nothing to evaluate"; exit 0; fi
if (( STATUS )); then printf '%-20s %-12s %-8s %-7s %-9s %-6s %-40s %s\n' ACCOUNT GROUP KIND MODE STATE INERT DETAIL REASON; fi
rc=0; HELD=0
for row in "${ROWS[@]}"; do
  read -r name group rkind rmode rpre <<<"$row"
  [[ -n "$ONE" && "$name" != "$ONE" ]] && continue
  evaluate "$name" "$group"
  inert=no; is_inert "$name" && inert=YES
  if (( STATUS )); then printf '%-20s %-12s %-8s %-7s %-9s %-6s %-40s %s\n' "$name" "$group" "$rkind" "$rmode" "$VERDICT" "$inert" "$DETAIL" "$REASON"; continue; fi
  if [[ "$VERDICT" == preloaded ]]; then log info "${name} (${group}): preloaded -- skipped (${REASON})"; continue; fi
  if [[ "$VERDICT" == hold ]]; then log warn "${name} (${group}): HOLD -- ${REASON}"; HELD=$((HELD+1)); continue; fi
  if [[ "$VERDICT" == live ]]; then log info "${name} (${group}): live ${DETAIL}"; continue; fi
  if [[ "$inert" == YES ]]; then continue; fi
  if (( EXECUTE )); then
    if [[ -n "${KILL_READY[$name]:-}" ]]; then make_inert "$name" "$REASON" 5; archive_orders "$name"; else make_inert "$name" "$REASON"; fi
  else log warn "${name} (${group}): STALE -- ${REASON} (dry-run: would make inert)"; fi
  rc=1
done
if (( HELD )) && (( ! STATUS )); then
  # the registry could not be reached: nothing was killed and nothing passed on
  # the key factor; alert and exit 2 (not in the unit's SuccessExitStatus)
  (( EXECUTE )) && ntfy_send "team-liveness: registry unavailable" "${HELD} account(s) held -- signer registry unreachable; no inert, no pass" 4
  exit 2
fi
exit $rc
