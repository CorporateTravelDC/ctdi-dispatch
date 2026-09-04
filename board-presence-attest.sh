#!/usr/bin/env bash
# scripts/board-presence-attest.sh
# Weekly human proof-of-presence for the Cowork board-write A2A channel.
# GPG-clearsigns an attestation (your passphrase, same discipline as
# sign-manifest.sh), self-verifies it against security/trusted-signing-key.
# pub.asc before trusting it, records it via board-presence-ingest.py, and
# mints a fresh enrollment nonce to hand Cowork to start the week's
# autonomous daily-refresh chain (db.board_refresh_token,
# GET /api/v1/board/refresh -- see docs/COMPLIANCE_SECURITY.md).
#
# This is a DELIBERATE, human-run step -- run it yourself on a ~7-day
# cadence, whenever a reminder fires (or proactively). Nothing re-signs this
# automatically, same posture as sign-manifest.sh.
#
# Usage:
#   scripts/board-presence-attest.sh
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

PUBKEY="security/trusted-signing-key.pub.asc"
if [[ ! -f "${PUBKEY}" ]]; then
    echo "XX Missing ${PUBKEY} -- cannot self-verify, refusing to proceed." >&2
    exit 2
fi

STATE_DIR="/var/lib/corporatetraveldc"
ATTEST_FILE="${STATE_DIR}/board-presence.asc"
WINDOW_S=$((7 * 86400))

ISSUED_AT="$(date -u +%s)"
VALID_UNTIL="$((ISSUED_AT + WINDOW_S))"
ISSUED_ISO="$(date -u -d "@${ISSUED_AT}" +%Y-%m-%dT%H:%M:%SZ)"
VALID_UNTIL_ISO="$(date -u -d "@${VALID_UNTIL}" +%Y-%m-%dT%H:%M:%SZ)"

# Single trap for both temp paths -- a second `trap ... EXIT` later would
# silently replace this one instead of adding to it (traps don't stack;
# same footgun documented in verify-manifest.sh).
PLAIN_TMP=""
GNUPGHOME_TMP=""
trap 'rm -f "${PLAIN_TMP}"; [[ -n "${GNUPGHOME_TMP}" ]] && rm -rf "${GNUPGHOME_TMP}"' EXIT

PLAIN_TMP="$(mktemp)"
cat > "${PLAIN_TMP}" <<EOF
CTDI-BOARD-PRESENCE
scope: cowork-board-write
issued_at: ${ISSUED_ISO}
valid_until: ${VALID_UNTIL_ISO}
EOF

echo "[board-presence-attest] Clearsigning with key ${SIGNING_KEY_FINGERPRINT} (you'll be prompted for your passphrase)..."
rm -f "${ATTEST_FILE}.tmp"
gpg --local-user "${SIGNING_KEY_FINGERPRINT}" --clearsign --output "${ATTEST_FILE}.tmp" "${PLAIN_TMP}"

echo "[board-presence-attest] Self-verifying against ${PUBKEY} before trusting it..."
GNUPGHOME_TMP="$(mktemp -d)"
chmod 700 "${GNUPGHOME_TMP}"
export GNUPGHOME="${GNUPGHOME_TMP}"
gpg --quiet --import "${PUBKEY}" >/dev/null 2>&1
if ! gpg --quiet --verify "${ATTEST_FILE}.tmp" 2>&1; then
    echo "XX Self-verification FAILED -- refusing to record. Something is wrong" >&2
    echo "   (wrong signing key, or it doesn't match trusted-signing-key.pub.asc)." >&2
    rm -f "${ATTEST_FILE}.tmp"
    exit 1
fi

mkdir -p "${STATE_DIR}"
mv "${ATTEST_FILE}.tmp" "${ATTEST_FILE}"
echo "[board-presence-attest] Verified OK -- written to ${ATTEST_FILE}"

echo "[board-presence-attest] Recording in DB and minting this cycle's enrollment nonce..."
PYTHONPATH=src python3 scripts/board-presence-ingest.py \
    --attestation-file "${ATTEST_FILE}" \
    --issued-at "${ISSUED_AT}" \
    --valid-until "${VALID_UNTIL}" \
    --key-fingerprint "${SIGNING_KEY_FINGERPRINT}"

echo
echo "[board-presence-attest] Done. Next attestation due by ${VALID_UNTIL_ISO}."
