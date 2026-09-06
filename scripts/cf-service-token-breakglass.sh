#!/usr/bin/env bash
# scripts/cf-service-token-breakglass.sh
# Incident-response companion to cf-service-token-mint.sh: revokes a
# compromised Cloudflare Access Service Token immediately, then mints a
# brand-new one under a name derived from a signature ONLY the operator
# can produce -- proving a human authorized this specific recovery, not
# just that whoever/whatever held CF_MANAGEMENT_API_TOKEN did it.
#
# Two-step, by design -- this cannot be a single autonomous command:
#   1. `prepare <compromised-token-name>` -- run by the agent. Writes an
#      artifact (compromised name + timestamp + random nonce) to
#      PENDING_FILE and prints the exact command for the operator to sign
#      it with the CTDI Break-Glass Authorization Key
#      (security/breakglass-authorization-key.pub.asc, fingerprint
#      5DA4A5A13949643EB7BF93A40B0744999425A548) -- a passphrase-protected
#      key confirmed live 2026-08-14 to genuinely require the operator's
#      passphrase, never usable by this script or the agent unattended.
#   2. `complete` -- run once the operator has produced the clear-signed
#      file. Verifies the signature was made by THAT SPECIFIC fingerprint
#      (not just "any valid signature" -- an agent-key or unknown-key
#      signature is explicitly rejected here), then revokes the
#      compromised token and mints a replacement named
#      ctdi-mcp-breakglass-<sha256 of the signed artifact>. The hash-based
#      name is itself the audit trail: reproducing that hash later means
#      reproducing the exact signed artifact, i.e. proving which
#      break-glass event a given live token corresponds to.
#
# Why this needs zero Access policy changes either way: both the identity
# policy ("corporatetraveldc") and the service-auth policy
# ("corporatetraveldc-service-auth") include `any_valid_service_token` --
# see the 2026-08-14 fix in docs/INFRA_MAP.md section 6a. Any non-expired
# token this account holds satisfies them, regardless of name.
#
# Never prints CF_MANAGEMENT_API_TOKEN or any client_secret to stdout/logs.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OPERATOR_KEY_FINGERPRINT="5DA4A5A13949643EB7BF93A40B0744999425A548"
PENDING_FILE="/var/lib/corporatetraveldc/breakglass-pending.txt"
SIGNED_FILE="${PENDING_FILE}.asc"
AUDIT_LOG="/var/lib/corporatetraveldc/cf-token-audit.log"

if ! "${REPO_DIR}/scripts/verify-manifest.sh"; then
    echo "XX INTEGRITY CHECK FAILED -- refusing to run cf-service-token-breakglass.sh" >&2
    exit 5
fi

MODE="${1:-}"

if [[ "${MODE}" == "prepare" ]]; then
    if [[ $# -ne 2 ]]; then
        echo "Usage: $0 prepare <compromised-token-name>" >&2
        exit 2
    fi
    COMPROMISED_NAME="$2"
    NONCE="$(python3 -c 'import uuid; print(uuid.uuid4())')"
    mkdir -p "$(dirname "${PENDING_FILE}")"
    {
        echo "breakglass_request_for=${COMPROMISED_NAME}"
        echo "requested_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
        echo "nonce=${NONCE}"
    } > "${PENDING_FILE}"
    rm -f "${SIGNED_FILE}"
    echo "[breakglass] Wrote ${PENDING_FILE}. On the Pi, as the operator, run:"
    echo ""
    echo "    gpg --clearsign --local-user breakglass@corporatetraveldc.local -o ${SIGNED_FILE} ${PENDING_FILE}"
    echo ""
    echo "You'll be prompted for the break-glass key's passphrase. Once done, re-run:"
    echo "    $0 complete"
    exit 0
fi

if [[ "${MODE}" != "complete" ]]; then
    echo "Usage: $0 prepare <compromised-token-name>" >&2
    echo "       $0 complete" >&2
    exit 2
fi

# ── complete mode ────────────────────────────────────────────────────────
if [[ ! -f "${SIGNED_FILE}" ]]; then
    echo "XX ${SIGNED_FILE} not found -- run '$0 prepare <name>' first, then have the" >&2
    echo "   operator clear-sign ${PENDING_FILE} before running 'complete'." >&2
    exit 2
fi

echo "[breakglass] Verifying signature on ${SIGNED_FILE}..."
VERIFY_OUT="$(gpg --status-fd 1 --verify "${SIGNED_FILE}" 2>/dev/null)" || {
    echo "XX Signature verification FAILED -- ${SIGNED_FILE} is not validly signed by anyone." >&2
    echo "   Refusing to proceed. Access is NOT restored." >&2
    exit 3
}

SIGNING_FPR="$(echo "${VERIFY_OUT}" | awk '/^\[GNUPG:\] VALIDSIG/ {print $3}')"
if [[ -z "${SIGNING_FPR}" ]]; then
    echo "XX Could not determine signing key fingerprint from gpg output -- refusing to proceed." >&2
    exit 3
fi

if [[ "${SIGNING_FPR}" != "${OPERATOR_KEY_FINGERPRINT}" ]]; then
    echo "XX SIGNATURE IS VALID BUT WRONG KEY." >&2
    echo "   Expected operator fingerprint: ${OPERATOR_KEY_FINGERPRINT}" >&2
    echo "   Actual signing fingerprint:    ${SIGNING_FPR}" >&2
    echo "   This is exactly the case this script exists to catch -- a signature from" >&2
    echo "   the agent key (or any other key) does NOT authorize break-glass recovery." >&2
    echo "   Refusing to proceed. Access is NOT restored." >&2
    exit 3
fi
echo "[breakglass] ✅ Signature confirmed from the operator break-glass key (${OPERATOR_KEY_FINGERPRINT})"

COMPROMISED_NAME="$(grep -E '^breakglass_request_for=' "${PENDING_FILE}" | cut -d'=' -f2-)"
if [[ -z "${COMPROMISED_NAME}" ]]; then
    echo "XX Could not read compromised token name from ${PENDING_FILE}" >&2
    exit 2
fi

ARTIFACT_HASH="$(sha256sum "${SIGNED_FILE}" | cut -d' ' -f1)"
NEW_NAME="ctdi-mcp-breakglass-${ARTIFACT_HASH:0:32}"

SECRETS_ENV="/etc/corporatetraveldc/dispatch-secrets.env"
OUT_ENV="/var/lib/corporatetraveldc/cf-service-token.env"

CF_MANAGEMENT_API_TOKEN="$(grep -E '^CF_MANAGEMENT_API_TOKEN=' "${SECRETS_ENV}" | tail -1 | cut -d'=' -f2-)"
CF_ACCOUNT_ID="$(grep -E '^CF_ACCOUNT_ID=' "${SECRETS_ENV}" | tail -1 | cut -d'=' -f2-)"
if [[ -z "${CF_MANAGEMENT_API_TOKEN}" || -z "${CF_ACCOUNT_ID}" ]]; then
    echo "XX CF_MANAGEMENT_API_TOKEN or CF_ACCOUNT_ID not set in ${SECRETS_ENV}" >&2
    exit 2
fi
API_BASE="https://api.cloudflare.com/client/v4/accounts/${CF_ACCOUNT_ID}/access/service_tokens"

# ── 1. Revoke the compromised token ─────────────────────────────────────────
echo "[breakglass] Looking up '${COMPROMISED_NAME}' to revoke..."
LIST_RESP="$(curl -s -m 20 -X GET "${API_BASE}" \
    -H "Authorization: Bearer ${CF_MANAGEMENT_API_TOKEN}" \
    -H "Content-Type: application/json")"
COMPROMISED_ID="$(echo "${LIST_RESP}" | python3 -c "
import json, sys
data = json.load(sys.stdin)
for t in data.get('result', []):
    if t.get('name') == '${COMPROMISED_NAME}':
        print(t.get('id'))
        break
" 2>/dev/null || true)"

if [[ -n "${COMPROMISED_ID}" ]]; then
    echo "[breakglass] Found '${COMPROMISED_NAME}' (id=${COMPROMISED_ID}) -- revoking now..."
    DEL_RESP="$(curl -s -m 20 -X DELETE "${API_BASE}/${COMPROMISED_ID}" \
        -H "Authorization: Bearer ${CF_MANAGEMENT_API_TOKEN}" \
        -H "Content-Type: application/json")"
    DEL_SUCCESS="$(echo "${DEL_RESP}" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("success"))' 2>/dev/null || echo "False")"
    if [[ "${DEL_SUCCESS}" == "True" ]]; then
        echo "[breakglass] ✅ REVOKED '${COMPROMISED_NAME}' (id=${COMPROMISED_ID})"
    else
        echo "XX Revoke call failed -- STOPPING before minting a replacement. Investigate manually:" >&2
        echo "${DEL_RESP}" | python3 -m json.tool >&2 || true
        exit 3
    fi
else
    echo "!! '${COMPROMISED_NAME}' not found (already revoked, or name typo?) -- proceeding to mint a replacement anyway, since restoring access is the priority. Verify the old credential is actually dead by other means."
fi

# ── 2. Mint the operator-authorized replacement ─────────────────────────────
echo "[breakglass] Minting replacement token '${NEW_NAME}'..."
CREATE_RESP="$(curl -s -m 20 -X POST "${API_BASE}" \
    -H "Authorization: Bearer ${CF_MANAGEMENT_API_TOKEN}" \
    -H "Content-Type: application/json" \
    -d "{\"name\": \"${NEW_NAME}\"}")"

CREATE_SUCCESS="$(echo "${CREATE_RESP}" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("success"))' 2>/dev/null || echo "False")"
if [[ "${CREATE_SUCCESS}" != "True" ]]; then
    echo "XX Failed to mint replacement token -- access is NOT restored:" >&2
    echo "${CREATE_RESP}" | python3 -m json.tool >&2 || true
    exit 3
fi

CLIENT_ID="$(echo "${CREATE_RESP}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["result"]["client_id"])')"
CLIENT_SECRET="$(echo "${CREATE_RESP}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["result"]["client_secret"])')"
if [[ -z "${CLIENT_ID}" || -z "${CLIENT_SECRET}" ]]; then
    echo "XX Response missing client_id/client_secret -- not writing anything, access NOT restored" >&2
    exit 3
fi

TMP_ENV="$(mktemp "$(dirname "${OUT_ENV}")/.cf-service-token.env.XXXXXX")"
{
    echo "# Auto-generated by scripts/cf-service-token-breakglass.sh -- DO NOT EDIT BY HAND."
    echo "# Break-glass replacement minted $(date -u +%Y-%m-%dT%H:%M:%SZ) after revoking '${COMPROMISED_NAME}'."
    echo "# New token name: ${NEW_NAME}"
    echo "CF_ACCESS_CLIENT_ID=${CLIENT_ID}"
    echo "CF_ACCESS_CLIENT_SECRET=${CLIENT_SECRET}"
} > "${TMP_ENV}"
chmod 600 "${TMP_ENV}"
mv -f "${TMP_ENV}" "${OUT_ENV}"

# Audit log gets the FULL clear-signed artifact appended verbatim (not
# re-signed by the agent key) -- this IS the operator's own signature,
# already the strongest evidence available.
mkdir -p "$(dirname "${AUDIT_LOG}")"
{
    echo "--- BREAK-GLASS EVENT: revoked '${COMPROMISED_NAME}', minted '${NEW_NAME}' ---"
    cat "${SIGNED_FILE}"
    echo ""
} >> "${AUDIT_LOG}"

echo "[breakglass] ✅ RESTORED -- new token '${NEW_NAME}' (client_id=${CLIENT_ID}) live at ${OUT_ENV}"
echo "[breakglass] Operator-signed artifact appended verbatim to ${AUDIT_LOG}"
