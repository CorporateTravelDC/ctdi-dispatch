#!/usr/bin/env bash
# scripts/push-and-sync.sh
# Wrapper around `git push` that syncs the public mirror afterward -- but
# only after CONFIRMING the real push actually succeeded, and only as an
# explicit, visible step you chose to run.
#
# 2026-08-13: replaces the old pre-push hook behavior of auto-syncing the
# public mirror silently as a side effect of any push to origin/main.
# git has no native post-push hook (pre-push runs BEFORE the push, so it
# can't know whether the push actually went through -- network failures,
# rejected non-fast-forwards, etc. would all have looked like "success" to
# the old hook). This script is the honest equivalent: it runs the real
# push, checks its actual exit status, and only then offers to sync.
#
# Usage: bash scripts/push-and-sync.sh [git push args...]
#   No args   -> `git push` (current branch, tracked remote)
#   Any args  -> forwarded verbatim to `git push`, e.g.:
#                bash scripts/push-and-sync.sh origin main

set -euo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SELF_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

echo "[push-and-sync] Running: git push $*"
if ! git push "$@"; then
    echo ""
    echo "[push-and-sync] ✗ git push failed -- stopping here, public mirror NOT touched."
    exit 1
fi
echo "[push-and-sync] ✓ git push succeeded."

branch="$(git rev-parse --abbrev-ref HEAD)"
if [[ "$branch" != "main" ]]; then
    echo "[push-and-sync] Current branch is '${branch}', not main -- skipping public sync."
    echo "                Run 'bash scripts/push-public.sh' yourself if you want to publish it."
    exit 0
fi

echo ""
read -r -p "[push-and-sync] Sync public mirror now (bash scripts/push-public.sh main)? [y/N] " reply
if [[ "$reply" =~ ^[Yy]$ ]]; then
    if bash "${REPO_ROOT}/scripts/push-public.sh" main; then
        echo "[push-and-sync] ✓ Public mirror synced."
    else
        echo "[push-and-sync] ✗ Public mirror sync FAILED -- see output above."
        exit 1
    fi
else
    echo "[push-and-sync] Skipped -- public mirror not touched. Run"
    echo "                'bash scripts/push-public.sh' manually whenever ready."
fi
