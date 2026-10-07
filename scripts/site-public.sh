#!/usr/bin/env bash
# scripts/site-public.sh -- build / publish a WEBSITE's public mirror (2026-10-06).
#
#   site-public.sh build   <site-repo>            stage + scrub + gate + unsigned manifest; prints the
#                                                  staged tree's path; signs nothing, publishes nothing
#   site-public.sh publish <site-repo> [--push]   same, then signs (your GPG key), verifies the tree with
#                                                  the site's own scripts/verify-manifest.sh, commits it
#                                                  (signed) into PUBLIC_DIR; --push sends it to GitHub
#
# Policy lives in the SITE repo: PUBLIC.conf (repo name, emails, substitutions,
# ledger) + PUBLIC-ALLOWLIST (the only files that may go public). Staging and the
# leak gate: scripts/site_public.py. Manifest + signing: scripts/lib/public-manifest.sh
# (shared with the platform's push-public.sh). The public tree is built from the
# CURRENT files only -- never git history.
# Sign the site repo first (its own scripts/sign-manifest.sh): the public tree
# carries a path-blinded copy of the private manifest as an attestation.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODE="${1:-}"; SITE="${2:-}"; PUSH="${3:-}"
[[ "$MODE" =~ ^(build|publish)$ && -d "$SITE/.git" ]] || { sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 64; }
SITE="$(cd "$SITE" && pwd)"
conf() { grep -m1 "^$1=" "$SITE/PUBLIC.conf" | cut -d= -f2-; }
PUBLIC_REPO="$(conf PUBLIC_REPO)"; PUBLIC_DIR="$(conf PUBLIC_DIR)"
[[ -n "$PUBLIC_REPO" && -n "$PUBLIC_DIR" ]] || { echo "PUBLIC.conf needs PUBLIC_REPO and PUBLIC_DIR" >&2; exit 64; }
. "$HERE/lib/public-manifest.sh"

WORK="$(mktemp -d)"; STAGE="$WORK/stage"; VERIFY="$WORK/verify"; mkdir -p "$STAGE" "$VERIFY"
[[ "$MODE" == publish ]] && trap 'rm -rf "$WORK"' EXIT
python3 "$HERE/site_public.py" stage "$SITE" "$STAGE"

cd "$STAGE"
git init -q
pm_blind_private_manifest "$SITE/MANIFEST.sha256" "$STAGE/PRIVATE-TREE-MANIFEST.sha256"
if [[ "$MODE" == publish ]]; then
  [[ -f ARTICLE-LEDGER.md ]] && pm_sign "$SITE" ARTICLE-LEDGER.md ARTICLE-LEDGER.md.asc
  pm_sign "$SITE" PRIVATE-TREE-MANIFEST.sha256 PRIVATE-TREE-MANIFEST.sha256.asc
fi
git add -A
TREE="$(git write-tree)"
pm_generate_manifest "$TREE" "$WORK/MANIFEST.sha256"
SPECS=("MANIFEST.sha256=$WORK/MANIFEST.sha256")
if [[ "$MODE" == publish ]]; then
  pm_sign "$SITE" "$WORK/MANIFEST.sha256" "$WORK/MANIFEST.sha256.asc"
  SPECS+=("MANIFEST.sha256.asc=$WORK/MANIFEST.sha256.asc")
fi
TREE="$(pm_add_to_tree "$TREE" "${SPECS[@]}")"
pm_extract_tree "$TREE" "$VERIFY"

if [[ "$MODE" == build ]]; then
  echo "[site-public] BUILD ONLY -- unsigned tree for $PUBLIC_REPO: $VERIFY ($(cd "$VERIFY" && git ls-files | wc -l) files)"
  exit 0
fi

( cd "$VERIFY" && bash scripts/verify-manifest.sh )          # gate: the tree must verify as a stranger would
mkdir -p "$PUBLIC_DIR"
rsync -a --delete --exclude .git "$VERIFY/" "$PUBLIC_DIR/"
cd "$PUBLIC_DIR"
[[ -d .git ]] || { git init -q -b main; git remote add origin "git@github.com:CorporateTravelDC/${PUBLIC_REPO}.git"; }
git add -A
if git diff --cached --quiet; then
  echo "[site-public] $PUBLIC_REPO: no changes since the last publish"
else
  git commit -S -q -m "Public mirror of $(basename "$SITE") @ $(git -C "$SITE" rev-parse --short HEAD) (scrubbed, self-verifying)"
  echo "[site-public] committed $(git rev-parse --short HEAD) in $PUBLIC_DIR"
fi
if [[ "$PUSH" == --push ]]; then
  git push -u origin main
fi
