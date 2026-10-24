#!/bin/bash
# scripts/install-git-hooks.sh -- install the tracked git hooks (2026-10-07).
#   platform repo   : scripts/pre-push                  -> <hooks>/pre-push
#                     scripts/pre-commit                -> <hooks>/pre-commit
#                     scripts/post-commit-doc-verify.sh -> <hooks>/post-commit
#   website repos   : scripts/git-hooks/site-pre-push   -> <repo>/.git/hooks/pre-push
# Refuses to replace a different existing hook in a website repo unless --force
# (it is shown first). Hooks are local: re-run after a fresh clone. Idempotent:
# an identical installed hook is left alone and reported "unchanged".
# 2026-10-22 (operator): was pre-push only, so a fresh clone ran without the
# pre-commit credential scanner. Every hook the platform repo uses is now in
# HOOKS below. <hooks> is resolved with `git rev-parse --git-common-dir`, so this
# also works from a worktree (where .git is a file, not a directory).
# NOT installed on purpose: scripts/post-rewrite (auto-push after a rebase) --
# commit and push are the operator's, never automatic.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FORCE=0; [[ "${1:-}" == --force ]] && FORCE=1
# hook name | tracked source (relative to the repo root)
HOOKS=(
    "pre-push|scripts/pre-push"
    "pre-commit|scripts/pre-commit"
    "post-commit|scripts/post-commit-doc-verify.sh"
)
# 2026-10-22: overridable so the test never touches the real website repos.
read -r -a SITE_REPOS <<<"${INSTALL_HOOKS_SITE_REPOS-/opt/corporatetraveldc/private/csexecutiveservices-website /opt/corporatetraveldc/private/executivestandard-website}"

put() {  # put <src> <dest>
    if [[ -f "$2" ]] && cmp -s "$1" "$2" && [[ -x "$2" ]]; then echo "unchanged: $2"; return 0; fi
    install -m 0755 "$1" "$2"
    echo "installed: $2"
}

common=$(git -C "$HERE" rev-parse --git-common-dir)
[[ "$common" = /* ]] || common="$HERE/$common"
HOOK_DIR="$common/hooks"
mkdir -p "$HOOK_DIR"
for entry in "${HOOKS[@]}"; do
    name=${entry%%|*}; src="$HERE/${entry#*|}"
    [[ -f "$src" ]] || { echo "MISSING tracked hook source: $src" >&2; exit 1; }
    put "$src" "$HOOK_DIR/$name"
done
for repo in "${SITE_REPOS[@]}"; do
    hook="$repo/.git/hooks/pre-push"
    [[ -d "$repo/.git" ]] || { echo "skip (not a repo): $repo"; continue; }
    if [[ -f "$hook" ]] && ! cmp -s "$hook" "$HERE/scripts/git-hooks/site-pre-push" && (( ! FORCE )); then
        echo "REFUSED: $hook exists and differs -- review it, then re-run with --force"; continue
    fi
    put "$HERE/scripts/git-hooks/site-pre-push" "$hook"
done
