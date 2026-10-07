#!/bin/bash
# scripts/install-git-hooks.sh -- install the tracked pre-push hooks (2026-10-07).
#   platform repo   : scripts/pre-push            -> .git/hooks/pre-push
#   website repos   : scripts/git-hooks/site-pre-push -> <repo>/.git/hooks/pre-push
# Refuses to replace a different existing hook in a website repo unless --force
# (it is shown first). Hooks are local: re-run after a fresh clone.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FORCE=0; [[ "${1:-}" == --force ]] && FORCE=1
install -m 0755 "$HERE/scripts/pre-push" "$HERE/.git/hooks/pre-push"
echo "installed: $HERE/.git/hooks/pre-push"
for repo in /opt/corporatetraveldc/private/csexecutiveservices-website /opt/corporatetraveldc/private/executivestandard-website; do
    hook="$repo/.git/hooks/pre-push"
    [[ -d "$repo/.git" ]] || { echo "skip (not a repo): $repo"; continue; }
    if [[ -f "$hook" ]] && ! cmp -s "$hook" "$HERE/scripts/git-hooks/site-pre-push" && (( ! FORCE )); then
        echo "REFUSED: $hook exists and differs -- review it, then re-run with --force"; continue
    fi
    install -m 0755 "$HERE/scripts/git-hooks/site-pre-push" "$hook"
    echo "installed: $hook"
done
