#!/usr/bin/env bash
# scripts/push-public.example.sh
#
# Reference template for scripts/push-public.sh -- ships with zero real
# values on purpose (see scrub-public-tree.example.py, the companion file
# this script depends on: it does the actual per-blob sanitization, this
# script just orchestrates the git plumbing and the push).
#
# Copy both this file and scrub-public-tree.example.py into your own repo,
# drop the ".example" suffix from both, then edit scrub-public-tree.py's
# DROP_FILES / SUBSTITUTIONS for your own real secrets and identifiers.
# This script itself needs no per-repo edits beyond the remote name below.
#
# What it actually does to your repo: nothing, to your working directory.
# Every step operates on git tree/blob/commit objects via plumbing commands
# (rev-parse, cat-file, hash-object, mktree, commit-tree) -- there is no
# `git clone`, no checkout, no working-tree copy. Your real branch's commits
# are read, never rewritten; a brand-new sanitized commit object is built
# from a brand-new sanitized tree and pushed under a different history,
# force-pushed only to the public remote's branch ref.
#
# Push a branch to the public mirror, auto-injecting a commit that gitignores
# your real secrets file and scrubs sensitive identifiers from all blobs.
#
# Usage:  bash scripts/push-public.sh [branch]   (default: current branch)

set -euo pipefail

branch="${1:-$(git rev-parse --abbrev-ref HEAD)}"
sha=$(git rev-parse "$branch")
remote_url=$(git remote get-url public)
repo_root="$(git rev-parse --show-toplevel)"

echo "[push-public] branch=${branch} tip=${sha:0:8}"

# ── Step 1: ensure your real secrets file is gitignored on the public side ──
# Adjust "secrets.env" to whatever your own repo's real credentials file is
# actually called.
if git cat-file blob "${sha}:.gitignore" 2>/dev/null | grep -qF "secrets.env"; then
    echo "[push-public] .gitignore already covers secrets.env"
    work_tree=$(git rev-parse "${sha}^{tree}")
else
    echo "[push-public] Injecting secrets.env into .gitignore for public mirror..."
    new_blob=$(git cat-file blob "${sha}:.gitignore" | \
        sed '/^# Credentials and keys — never commit/a secrets.env' | \
        git hash-object -w --stdin)
    work_tree=$(git ls-tree "${sha}" | \
        sed "s|\(100644 blob\) [0-9a-f]*\(\t\.gitignore\)|\1 ${new_blob}\2|" | \
        git mktree)
fi

# ── Step 2: scrub sensitive identifiers from all blobs ─────────────────────
# This is the dependency this whole script leans on -- without it running
# cleanly, nothing here actually sanitizes anything, it just pushes a raw
# force-pushed copy of your private history.
echo "[push-public] Scrubbing sensitive identifiers..."
scrubbed_tree=$(python3 "${repo_root}/scripts/scrub-public-tree.py" "${work_tree}")

# ── Step 3: create the patched commit and push ──────────────────────────────
# The new commit must NEVER parent on ${sha} (the raw private commit).
# scrub-public-tree.py only sanitizes the TREE of the tip -- every ancestor
# commit is still the real, unscrubbed object. Parenting on ${sha} would
# push that entire raw ancestor chain to the public remote via `git push`
# (it transfers whatever the tip doesn't already have there), silently
# defeating the scrub for the repo's full history. Instead, parent on the
# public mirror's OWN current tip -- if there is no public history yet
# (first push, or after a reset), create a fresh orphan commit with no
# parent at all. The public branch is then its own independent,
# fully-scrubbed lineage that never shares an ancestor with the private repo.
public_parent=$(git ls-remote "$remote_url" "refs/heads/${branch}" | cut -f1)

if [ -n "$public_parent" ]; then
    parent_args=(-p "$public_parent")
else
    echo "[push-public] No existing public/${branch} -- creating orphan root commit"
    parent_args=()
fi

new_commit=$(git commit-tree -S "${scrubbed_tree}" "${parent_args[@]}" \
    -m "chore(public): sanitize for public mirror [auto by push-public.sh]")

git push --force "$remote_url" "${new_commit}:refs/heads/${branch}"

echo "[push-public] ✓ public/${branch}: ${sha:0:8} → ${new_commit:0:8}"
echo "[push-public]   secrets.env gitignored on public mirror; private/${branch} unchanged"
