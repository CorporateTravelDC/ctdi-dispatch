#!/usr/bin/env bash
# scripts/push-public.sh
# Push a branch to the public mirror, auto-injecting a commit that gitignores
# dispatch-secrets.env if the tip commit doesn't already do so.
#
# Usage:  bash scripts/push-public.sh [branch]   (default: current branch)
#
# Uses git plumbing (commit-tree + send-pack) so the injection never touches
# the working tree or private/main, and no hook recursion occurs.

set -euo pipefail

branch="${1:-$(git rev-parse --abbrev-ref HEAD)}"
sha=$(git rev-parse "$branch")
remote_url=$(git remote get-url public)

echo "[push-public] branch=${branch} tip=${sha:0:8}"

# Nothing to do if dispatch-secrets.env is already gitignored at this commit.
if git cat-file blob "${sha}:.gitignore" 2>/dev/null | grep -qF "dispatch-secrets.env"; then
    echo "[push-public] .gitignore already covers dispatch-secrets.env — pushing as-is"
    git send-pack --thin "$remote_url" "${sha}:refs/heads/${branch}"
    echo "[push-public] ✓ public/${branch} updated"
    exit 0
fi

echo "[push-public] Injecting dispatch-secrets.env into .gitignore for public mirror..."

# Patch the .gitignore blob: insert after the "Credentials and keys" header line.
new_blob=$(git cat-file blob "${sha}:.gitignore" | \
    sed '/^# Credentials and keys — never commit/a dispatch-secrets.env' | \
    git hash-object -w --stdin)

# Build a new root tree substituting only the .gitignore blob.
new_tree=$(git ls-tree "${sha}" | \
    sed "s|\(100644 blob\) [0-9a-f]*\(\t\.gitignore\)|\1 ${new_blob}\2|" | \
    git mktree)

# Create the patched commit.
new_commit=$(git commit-tree "${new_tree}" -p "${sha}" \
    -m "chore(public): gitignore dispatch-secrets.env [auto-injected by push-public.sh]")

# Push via send-pack — bypasses pre-push hooks, no recursion.
git send-pack --thin "$remote_url" "${new_commit}:refs/heads/${branch}"

echo "[push-public] ✓ public/${branch}: ${sha:0:8} → ${new_commit:0:8}"
echo "[push-public]   dispatch-secrets.env gitignored on public mirror; private/${branch} unchanged"
