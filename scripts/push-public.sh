#!/usr/bin/env bash
# scripts/push-public.sh
# Push a branch to the public mirror, auto-injecting a commit that gitignores
# dispatch-secrets.env and scrubs sensitive identifiers from all blobs.
#
# Sensitive substitutions live in scripts/scrub-public-tree.py.
#
# Usage:  bash scripts/push-public.sh [branch]   (default: current branch)

set -euo pipefail

branch="${1:-$(git rev-parse --abbrev-ref HEAD)}"
sha=$(git rev-parse "$branch")
remote_url=$(git remote get-url public)
repo_root="$(git rev-parse --show-toplevel)"

echo "[push-public] branch=${branch} tip=${sha:0:8}"

# ── Step 1: ensure dispatch-secrets.env is gitignored ──────────────────────────
if git cat-file blob "${sha}:.gitignore" 2>/dev/null | grep -qF "dispatch-secrets.env"; then
    echo "[push-public] .gitignore already covers dispatch-secrets.env"
    work_tree=$(git rev-parse "${sha}^{tree}")
else
    echo "[push-public] Injecting dispatch-secrets.env into .gitignore for public mirror..."
    new_blob=$(git cat-file blob "${sha}:.gitignore" | \
        sed '/^# Credentials and keys — never commit/a dispatch-secrets.env' | \
        git hash-object -w --stdin)
    work_tree=$(git ls-tree "${sha}" | \
        sed "s|\(100644 blob\) [0-9a-f]*\(\t\.gitignore\)|\1 ${new_blob}\2|" | \
        git mktree)
fi

# ── Step 2: scrub sensitive identifiers from all blobs ─────────────────────────
echo "[push-public] Scrubbing sensitive identifiers..."
scrubbed_tree=$(python3 "${repo_root}/scripts/scrub-public-tree.py" "${work_tree}")

# ── Step 3: create the patched commit and push ─────────────────────────────────
new_commit=$(git commit-tree "${scrubbed_tree}" -p "${sha}" \
    -m "chore(public): sanitize for public mirror [auto by push-public.sh]")

git push --force "$remote_url" "${new_commit}:refs/heads/${branch}"

echo "[push-public] ✓ public/${branch}: ${sha:0:8} → ${new_commit:0:8}"
echo "[push-public]   dispatch-secrets.env gitignored on public mirror; private/${branch} unchanged"
