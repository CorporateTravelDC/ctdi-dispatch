#!/usr/bin/env bash
# scripts/claude-policy-check.sh -- prove the Claude Code managed policy is in
# force and unchanged (operator 2026-10-25: "a silent feature adjustment can't
# be made ... and still ensure that it actually takes").
#
# Checks (read-only, no root needed):
#   1. /etc/claude-code/managed-settings.json exists, root:root 0644, and equals
#      the SIGNED config/claude-code/managed-settings.json (manifest hash)
#   2. the hook it wires, /usr/local/libexec/ctdc/ctdc-claude-no-agent-hook.py,
#      is root-owned and equals the signed scripts/ctdc-claude-no-agent-hook.py
#   3. the empty GNUPGHOME it hands Claude is root:root 0555 and empty
#   4. the installed hook still blocks agent/key/sign/commit/push commands and
#      still allows ordinary read-only ones (behavioural self-test)
#   5. the installed Claude Code version equals config/claude-code/claude-code.version
#      (a version change is where defaults such as auto-mode behaviour move;
#      review the release, then bump the pin in a signed commit)
#   6. ~/.claude/settings.json equals config/claude-code/user-settings.baseline.json
#      (any silent change to user settings is reported by top-level key)
#   --live  additionally asks a real `claude -p` session (Haiku, one Bash call)
#           to run `ssh-add -l` and requires the policy's block message back --
#           proof that Claude Code actually loads the managed file. Costs a few
#           Haiku tokens; manual / weekly, never in the hourly sweep.
#   --quiet print findings only
# Prints findings only, never a value. Exit 1 on any FAIL.
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
POLICY=/etc/claude-code/managed-settings.json
HOOK=/usr/local/libexec/ctdc/ctdc-claude-no-agent-hook.py
EMPTY_GNUPG=/usr/local/share/ctdc/claude-empty-gnupg
USER_SETTINGS="${CLAUDE_USER_SETTINGS:-${HOME}/.claude/settings.json}"
# 2026-10-26: the installed binary by its one fixed path -- the integrity-sweep unit's PATH has no
# ~/.local/bin, so a bare `claude` found nothing and every sweep since 2026-10-25 failed here. No
# environment override and no PATH lookup: nothing can point this check at a different program.
CLAUDE_BIN="${HOME}/.local/bin/claude"
LIVE=0; QUIET=0
for a in "$@"; do case "$a" in --live) LIVE=1 ;; --quiet) QUIET=1 ;; esac; done
fails=0
fail() { echo "FAIL: $*"; fails=$((fails + 1)); }
ok() { (( QUIET )) || echo "ok: $*"; }
manifest_hash() { awk -v p="$1" '$2==p || $2=="*"p {print $1; exit}' "${REPO}/MANIFEST.sha256"; }
sha() { sha256sum "$1" 2>/dev/null | cut -d' ' -f1; }

check_root_file() {  # path signed-src mode label
    local f="$1" src="$2" mode="$3" label="$4" want
    if [[ ! -f "$f" ]]; then fail "${label}: ${f} is missing (run sudo scripts/install-root-copies.sh)"; return; fi
    [[ "$(stat -c '%U:%G %a' "$f")" == "root:root ${mode}" ]] || fail "${label}: ${f} is $(stat -c '%U:%G %a' "$f"), expected root:root ${mode}"
    want=$(manifest_hash "$src")
    if [[ -z "$want" ]]; then fail "${label}: ${src} is not in the signed manifest"
    elif [[ "$(sha "$f")" != "$want" ]]; then fail "${label}: ${f} differs from the signed ${src}"
    else ok "${label}: matches the signed ${src}"; fi
}

# 1 + 2
check_root_file "$POLICY" config/claude-code/managed-settings.json 644 "managed policy"
check_root_file "$HOOK" scripts/ctdc-claude-no-agent-hook.py 755 "policy hook"
# 2026-10-26: no aircraft registration in anything Claude writes or returns
REG_HOOK=/usr/local/libexec/ctdc/ctdc-claude-no-registration-hook.py
check_root_file "$REG_HOOK" scripts/ctdc-claude-no-registration-hook.py 755 "registration hook"
if [[ -f "$REG_HOOK" ]]; then
    reg="N""12IO"   # FAA never issues I or O: never a real registration; built at runtime (public scrub rule)
    r1=$(printf '{"tool_input":{"file_path":"/tmp/x.md","content":"tail %s"}}' "$reg" | python3 "$REG_HOOK" >/dev/null 2>&1; echo $?)
    r2=$(printf '{"tool_input":{"content":"11 rows (SELECT count)"}}' | python3 "$REG_HOOK" >/dev/null 2>&1; echo $?)
    [[ "$r1" == 2 && "$r2" == 0 ]] && ok "registration hook: blocks a registration, allows a count" || fail "registration hook self-test failed (registration rc=${r1}, count rc=${r2})"
fi

# 3
if [[ ! -d "$EMPTY_GNUPG" ]]; then fail "empty GNUPGHOME ${EMPTY_GNUPG} is missing"
elif [[ "$(stat -c '%U:%G %a' "$EMPTY_GNUPG")" != "root:root 555" ]]; then fail "empty GNUPGHOME is $(stat -c '%U:%G %a' "$EMPTY_GNUPG"), expected root:root 555"
elif [[ -n "$(ls -A "$EMPTY_GNUPG" 2>/dev/null)" ]]; then fail "empty GNUPGHOME is not empty"
else ok "empty GNUPGHOME: root:root 555, empty"; fi

# 4 -- behavioural self-test of the INSTALLED hook (falls back to the tree copy only to report)
hook_rc() { python3 -c 'import json,sys;print(json.dumps({"tool_input":{"command":sys.argv[1]}}))' "$1" | python3 "$2" >/dev/null 2>&1; echo $?; }
if [[ -f "$HOOK" ]]; then
    bad=0
    for c in 'ssh-add -l' 'eval "$(ssh-agent -s)"' 'gpg-connect-agent /bye' 'git commit -S -m x' 'git -C /tmp push origin main' \
             'gpg --detach-sign f' 'cat ~/.gnupg/pubring.kbx' 'SSH_AUTH_SOCK=/tmp/s git fetch' 'bash scripts/sign-manifest.sh'; do
        [[ "$(hook_rc "$c" "$HOOK")" == 2 ]] || { fail "policy hook let a forbidden command through (case: ${c%% *} ...)"; bad=1; }
    done
    for c in 'git log --oneline -3' 'git diff --cached --stat' 'bash scripts/verify-manifest.sh' 'gh api repos/x/y'; do
        [[ "$(hook_rc "$c" "$HOOK")" == 0 ]] || { fail "policy hook blocks an allowed command (case: ${c%% *} ...)"; bad=1; }
    done
    (( bad )) || ok "policy hook: blocks 9/9 forbidden, allows 4/4 read-only"
fi

# 5
pin=$(tr -d '[:space:]' <"${REPO}/config/claude-code/claude-code.version" 2>/dev/null)
have=$("${CLAUDE_BIN}" --version 2>/dev/null | awk '{print $1; exit}')
if [[ -z "$have" ]]; then fail "claude --version gave nothing (binary: ${CLAUDE_BIN})"
elif [[ "$have" != "$pin" ]]; then fail "Claude Code is ${have}, pinned ${pin}: review the release notes for permission/auto-mode changes, then bump config/claude-code/claude-code.version in a signed commit"
else ok "Claude Code ${have} = pin"; fi

# 6
diffkeys=$(python3 - "$USER_SETTINGS" "${REPO}/config/claude-code/user-settings.baseline.json" <<'PY'
import json, sys
try:
    live, base = (json.load(open(p)) for p in sys.argv[1:3])
except Exception as e:
    print(f"unreadable ({type(e).__name__})"); sys.exit()
keys = sorted(k for k in set(live) | set(base) if live.get(k, "<absent>") != base.get(k, "<absent>"))
print(" ".join(keys))
PY
)
if [[ -n "$diffkeys" ]]; then fail "user settings differ from the signed baseline in: ${diffkeys} (if intended, copy ~/.claude/settings.json to config/claude-code/user-settings.baseline.json in a signed commit)"
else ok "user settings = signed baseline"; fi

# --live
if (( LIVE )); then
    out=$(cd /tmp && timeout 180 "${CLAUDE_BIN}" -p --model haiku --max-turns 2 --allowedTools Bash \
          "Run exactly this shell command once with the Bash tool and then quote any error text you got back, verbatim: ssh-add -l" </dev/null 2>&1 || true)
    if grep -q 'operator policy' <<<"$out"; then ok "live: a real Claude session was blocked by the managed policy hook"
    else fail "live: no policy block seen from a real Claude session (excerpt: $(tr '\n' ' ' <<<"$out" | tr -cd '[:alnum:] .,:()_-' | cut -c1-160))"; fi
fi

(( fails == 0 )) || exit 1
exit 0
