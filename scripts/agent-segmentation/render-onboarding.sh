#!/usr/bin/env bash
# scripts/agent-segmentation/render-onboarding.sh <account> [--install]
#
# Renders onboarding-template.md for one team account (human or agent) and,
# with --install (root), places it at /home/<account>/CLAUDE.md -- the first
# file Claude Code reads when a session starts in that home, and a plain
# README for a human. Operator design (2026-10-04): the home directory is the
# account's desk; the pamphlet on it says which repos it may enter and what
# its controls are, as a dated STATIC mirror of the vault/docs, never live
# state. Re-render after any segmentation change (idempotent).
set -euo pipefail
NAME="${1:-}"; MODE="${2:-}"
[[ -n "$NAME" ]] || { echo "usage: $0 <account> [--install]" >&2; exit 64; }
getent passwd "$NAME" >/dev/null || { echo "no such account: $NAME" >&2; exit 67; }
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; REPO_ROOT="$(cd "$HERE/../.." && pwd)"
TPL="$HERE/onboarding-template.md"
UIDN=$(id -u "$NAME"); HOME_DIR=$(getent passwd "$NAME" | cut -d: -f6)
GROUPS_CSV=$(id -Gn "$NAME" | tr ' ' ',')
if id -nG "$NAME" | tr ' ' '\n' | grep -qx ctdc-agents; then KIND=agent; SLICE=agents.slice; SUBSET=/etc/ctdc-agent/agent-secrets.env; LOGIN_MODE=claude
elif id -nG "$NAME" | tr ' ' '\n' | grep -qx ctdc-ops; then KIND=human; SLICE=humans.slice; SUBSET=/etc/ctdc-ops/ops-secrets.env; LOGIN_MODE=ssh
else KIND=unknown; SLICE="(none)"; SUBSET="(none)"; LOGIN_MODE=none; fi
# the registry (/etc/ctdc-accounts.conf: name kind login_mode preloaded)
# refines the group: service accounts, ssh-mode agents, preloaded accounts
PRELOADED=0; REGISTRY=/etc/ctdc-accounts.conf
if [[ -r "$REGISTRY" ]]; then
  read -r rk rm rp < <(awk -v n="$NAME" '$1==n {print $2, $3, $4}' "$REGISTRY" | tail -1) || true
  [[ -n "${rk:-}" ]] && { KIND="$rk"; LOGIN_MODE="$rm"; PRELOADED="${rp:-0}"; }
fi
[[ "$KIND" == service ]] && SLICE="(none -- a service identity runs no user manager)"
case "$LOGIN_MODE" in
  claude) LOGIN_TEXT="you log in with the Claude CLI once (\`sudo -u ${NAME} -i claude\`); your liveness is that login's refresh (must refresh within 7 days)" ;;
  ssh)    LOGIN_TEXT="you arrive over SSH as \`${NAME}\` with the single inbound key below; your liveness is your last SSH login (within 7 days for agents, 14 for humans)" ;;
  *)      LOGIN_TEXT="you do NOT log in -- a service identity has a nologin shell and never a session; you sign. Your liveness is your signer key, your tokens, and the absence of a kill order" ;;
esac
if [[ "$PRELOADED" == 1 ]]; then PRELOAD_BANNER="> **PRELOADED -- not yet activated.** This account exists but is expired, has no linger and an inactive signer; nothing runs as it until the operator runs \`plan.sh --activate ${NAME}\`. Everything below describes the account AFTER activation."$'\n'
else PRELOAD_BANNER=""; fi
# signer registry = operator's rootless podman; as root, drop to the operator (and
# never let this lookup abort the render -- it is informational)
OPERATOR=corporatetraveldc
if (( EUID == 0 )); then
  ROLE=$(runuser -u "$OPERATOR" -- env XDG_RUNTIME_DIR="/run/user/$(id -u "$OPERATOR")" "$REPO_ROOT/scripts/board-signer-ctl.sh" show "$NAME" 2>/dev/null | awk 'NR==1 && $2!="-" {print $2}' || true)
else
  ROLE=$("$REPO_ROOT/scripts/board-signer-ctl.sh" show "$NAME" 2>/dev/null | awk 'NR==1 && $2!="-" {print $2}' || true)
fi
ROLE=${ROLE:-member}
# repos = every git checkout under /opt/corporatetraveldc/private the account's groups can write
REPOS=""
for d in /opt/corporatetraveldc/private/*/; do
  [[ -d "$d/.git" ]] || continue
  g=$(stat -c %G "$d"); perm=$(stat -c %A "$d")
  if id -nG "$NAME" | tr ' ' '\n' | grep -qx "$g" && [[ "${perm:5:1}" == w ]]; then REPOS+="  - \`${d%/}\` (group \`$g\`, write)"$'\n'
  elif [[ "${perm:7:1}" == r ]] || { id -nG "$NAME" | tr ' ' '\n' | grep -qx "$g" && [[ "${perm:4:1}" == r ]]; }; then REPOS+="  - \`${d%/}\` (read only)"$'\n'; fi   # the ACCOUNT's access, from mode bits, not the renderer's
done
[[ -n "$REPOS" ]] || REPOS="  - (none yet -- ask the operator which repo this work belongs to)"$'\n'
# skills table from the grants file (scripts/lib/skill_grants.py render); agents only
if [[ "$KIND" == agent ]]; then SKILLS=$(python3 "$REPO_ROOT/scripts/lib/skill_grants.py" --repo "$REPO_ROOT" render "$NAME" 2>&1 || echo "_(skills table unavailable -- run scripts/skill-grants.sh list ${NAME})_")
else SKILLS="_(skills are granted to agent accounts; humans use their own Claude setup)_"; fi
render() {
python3 - "$TPL" "$NAME" "$UIDN" "$KIND" "$ROLE" "$GROUPS_CSV" "$HOME_DIR" "$SLICE" "$SUBSET" "$REPOS" "$LOGIN_MODE" "$LOGIN_TEXT" "$PRELOAD_BANNER" "$SKILLS" <<'PYEOF'
import sys, datetime
tpl, name, uid, kind, role, groups, home, slice_, subset, repos, login_mode, login_text, preload_banner, skills = sys.argv[1:15]
t = open(tpl).read()
for k, v in {"NAME": name, "UID": uid, "KIND": kind, "ROLE": role, "GROUPS": groups, "HOME": home,
             "SLICE": slice_, "SUBSET": subset, "REPOS": repos.rstrip("\n"),
             "LOGIN_MODE": login_mode, "LOGIN_TEXT": login_text, "PRELOAD_BANNER": preload_banner, "SKILLS": skills.rstrip("\n"),
             "RENDERED": datetime.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")}.items():
    t = t.replace("{{" + k + "}}", v)
sys.stdout.write(t)
PYEOF
}
if [[ "$MODE" == --install ]]; then
  (( EUID == 0 )) || { echo "--install needs root (sudo)" >&2; exit 77; }
  tmp=$(mktemp); render >"$tmp"
  install -m 0444 -o root -g "$NAME" "$tmp" "$HOME_DIR/CLAUDE.md"   # root-owned, read-only: the account cannot rewrite its own pamphlet
  # 2026-10-08: Codex reads AGENTS.md (its working directory, and ~/.codex/AGENTS.md
  # globally), not CLAUDE.md -- the same pamphlet goes under both names so a
  # codex-mode agent reads the same rules.
  install -m 0444 -o root -g "$NAME" "$tmp" "$HOME_DIR/AGENTS.md"
  [[ -d "$HOME_DIR/.codex" ]] && install -m 0444 -o root -g "$NAME" "$tmp" "$HOME_DIR/.codex/AGENTS.md"
  rm -f "$tmp"; echo "installed $HOME_DIR/CLAUDE.md + AGENTS.md$([[ -d "$HOME_DIR/.codex" ]] && echo ' + .codex/AGENTS.md') ($(wc -l <"$HOME_DIR/CLAUDE.md") lines, root:$NAME 0444)"
else
  render
fi
