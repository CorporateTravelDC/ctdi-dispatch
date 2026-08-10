#!/usr/bin/env bash
# scripts/sign-manifest.sh
# Generates and GPG-signs a whole-repo-tree integrity manifest -- the source
# of truth scripts/verify-manifest.sh checks any covered file against before
# letting it run. See docs/COMPLIANCE_SECURITY.md's "Signed Manifest
# Integrity" section for the full design and threat model.
#
# This is a DELIBERATE, human-run step -- same posture as this repo's signed
# commits (git config commit.gpgsign=true): nothing re-signs the manifest
# automatically. Run this after making changes you're ready to trust, as
# part of the same review/commit pass, using your own GPG passphrase.
#
# Usage:
#   scripts/sign-manifest.sh
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "${REPO_DIR}"

ENV_FILE="security/signing.env"
if [[ ! -f "${ENV_FILE}" ]]; then
    echo "XX Missing ${ENV_FILE} -- copy security/signing.env.example, fill in your" >&2
    echo "   own SIGNING_KEY_FINGERPRINT, and re-run." >&2
    exit 2
fi
# shellcheck source=/dev/null
source "${ENV_FILE}"
: "${SIGNING_KEY_FINGERPRINT:?SIGNING_KEY_FINGERPRINT not set in ${ENV_FILE}}"
if [[ "${SIGNING_KEY_FINGERPRINT}" == "0000000000000000000000000000000000000000" ]]; then
    echo "XX ${ENV_FILE} still has the placeholder fingerprint -- fill in your real one." >&2
    exit 2
fi

MANIFEST="MANIFEST.sha256"
SIGNATURE="MANIFEST.sha256.asc"

echo "[sign-manifest] Hashing every tracked AND untracked-but-not-ignored file (true whole-repo-tree coverage)..."
# --cached --others --exclude-standard: tracked files PLUS untracked ones
# that aren't .gitignore'd -- NOT just `git ls-files` alone (tracked only).
# Bug found 2026-08-09: every file added this session before being `git add`ed
# -- including sign-manifest.sh/verify-manifest.sh/verified-exec.sh
# themselves -- was silently absent from every manifest generated so far,
# and worse, the COLLECTIVE check (sha256sum -c) can only notice a
# *modified* file it already knows about; it can't notice an entirely new
# file existing at all, since it only iterates the manifest's own known
# list. `git add`-only coverage means "new file added by an attacker" was
# invisible to the one check meant to catch exactly that. .git internals,
# build artifacts, and anything actually .gitignore'd are still excluded
# (via --exclude-standard), same as before.
git ls-files --cached --others --exclude-standard -z \
    | grep -zvE "^(${MANIFEST}|${SIGNATURE})\$" \
    | xargs -0 sha256sum \
    | sort -k2 \
    > "${MANIFEST}"

echo "[sign-manifest] $(wc -l < "${MANIFEST}") files covered."
echo "[sign-manifest] Signing with key ${SIGNING_KEY_FINGERPRINT} (you'll be prompted for your passphrase)..."
rm -f "${SIGNATURE}"
gpg --local-user "${SIGNING_KEY_FINGERPRINT}" --detach-sign --armor -o "${SIGNATURE}" "${MANIFEST}"

echo "[sign-manifest] OK -- ${MANIFEST} + ${SIGNATURE} written."
echo "[sign-manifest] Verify with: scripts/verify-manifest.sh"
echo "[sign-manifest] Remember to commit both files alongside the changes they cover."
