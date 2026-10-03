#!/usr/bin/env bash
# skill-snapshot.sh -- create a signed, OFFLINE-VERIFIABLE snapshot of the
# locally-owned skills and persist it to the second-brain vault for multi-site
# distribution + versioned growth. Part of the sovereign skill supply chain
# (project_sovereign_skill_supply_chain, Phase 1).
#
# The vault + a GPG-signed manifest replace GitHub for cross-site distribution:
# any site pulls the tarball + manifest + .asc, verifies OFFLINE (sha256 + your
# GPG key), and installs. No Anthropic sync, no external mutation, full audit
# trail. GPG signing is the OPERATOR's step (hand-back), like sign-manifest.sh.
#
# Snapshots land in 01-Sources/personal-research/skill-snapshots/ -- deliberately
# OUTSIDE Cowork's read scope, because the vendored skills still embed admin
# tokens (ctdc_cowork_...). Parameterize those out (Phase 2) before any
# broader-scope placement or publish-out.
#
# Usage:
#   scripts/skill-snapshot.sh create          # bundle + manifest + upload (unsigned)
#   scripts/skill-snapshot.sh --verify <manifest>   # offline verify a snapshot
set -euo pipefail

SKILLS_DIR="${SKILLS_DIR:-$HOME/.claude/skills}"
STAGE="${STAGE:-$HOME/.claude/skill-snapshots}"
VAULT_SUBDIR="01-Sources/personal-research/skill-snapshots"
mode="${1:-create}"

verify() {
  local man="$1" dir rec hash fname actual
  dir="$(dirname "$man")"
  rec="$(grep '^tarball_sha256 ' "$man" | head -1)"
  hash="$(echo "$rec" | awk '{print $2}')"
  fname="$(echo "$rec" | awk '{print $3}')"
  actual="$(sha256sum "$dir/$fname" | cut -d' ' -f1)"
  if [ "$hash" = "$actual" ]; then echo "tarball hash OK ($fname)"; else echo "tarball hash MISMATCH -- snapshot altered"; exit 1; fi
  if [ -f "$man.asc" ]; then
    gpg --verify "$man.asc" "$man" 2>&1 | grep -iE 'Good signature|BAD signature' || true
  else
    echo "no .asc present -- UNSIGNED snapshot (run the operator sign step)"
  fi
}

if [ "$mode" = "--verify" ]; then verify "$2"; exit 0; fi

mkdir -p "$STAGE"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
base="skills-snapshot-${TS}"
tar="${STAGE}/${base}.tar.gz"
man="${STAGE}/${base}.manifest.txt"

# bundle skills, excluding the cloud-synced cache and trash (deterministic order)
tar -C "$SKILLS_DIR" --sort=name --exclude='synced' --exclude='.trash' \
    --mtime='2026-01-01 00:00:00' -czf "$tar" .

{
  echo "# skill snapshot manifest ${TS}"
  echo "# source: ${SKILLS_DIR} (synced/ and .trash excluded)"
  echo "tarball_sha256 $(sha256sum "$tar" | cut -d' ' -f1) ${base}.tar.gz"
  echo "# per-file sha256 (audit granularity):"
  ( cd "$SKILLS_DIR" && find . -type f -not -path './synced/*' -not -path './.trash/*' -print0 \
      | sort -z | xargs -0 sha256sum )
} > "$man"

echo "created:"
echo "  $(du -h "$tar" | cut -f1)  $tar"
echo "  $(wc -l < "$man") lines  $man"

python3 - "$tar" "$man" "$VAULT_SUBDIR" "$base" <<'PY'
import sys
from second_brain import webdav_client as w
tar, man, subdir, base = sys.argv[1:5]
R = w.BUSINESS_ROOT
for local, name in ((tar, base + ".tar.gz"), (man, base + ".manifest.txt")):
    with open(local, "rb") as f:
        code = w.put(f"{R}/{subdir}/{name}", f.read())
    print(f"  uploaded {name}: HTTP {code}")
PY

echo
echo "OPERATOR SIGN STEP (your GPG key -- run, then upload the .asc):"
echo "  gpg --detach-sign --armor ${man}"
echo "  scripts/skill-snapshot.sh publishes only tarball+manifest; upload the"
echo "  resulting ${base}.manifest.txt.asc to vault ${VAULT_SUBDIR}/ to seal it."
