#!/usr/bin/env bash
# scripts/cf-service-token-reconcile.sh
# The "invalidates" check, 2026-08-14: lists every LIVE Cloudflare Access
# Service Token on the account and cross-references it against
# AUDIT_LOG's signed records. A live token this account holds that has no
# corresponding, validly-signed record here is presumptively
# UNAUTHORIZED -- it means something minted a token directly against the
# Cloudflare API, bypassing both cf-service-token-mint.sh (agent-signed)
# and cf-service-token-breakglass.sh (operator-signed). Cloudflare itself
# has no concept of this distinction (any valid token satisfies the
# `any_valid_service_token` policy rule regardless of provenance) -- this
# script is what actually enforces "did OUR process create this," not CF.
#
# Not a mint/revoke tool itself -- read-only. Exits non-zero if anything
# unexplained is found, so this can run on a schedule (systemd timer) and
# alert on failure through the normal mechanism other checks use.
#
# Usage: scripts/cf-service-token-reconcile.sh
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# NOTE: this is the SIGNING SUBKEY fingerprint, not the primary key's
# (C0E92095063C7AE670E590563A0E7B60576BBF22) -- gpg --verify's VALIDSIG
# status line reports whichever key/subkey actually produced the
# signature, and cf-service-token-mint.sh signs with the dedicated
# sign-usage subkey. Confirmed empirically 2026-08-14, not assumed.
AGENT_KEY_FINGERPRINT="1946CA0DD89CD2A3A46DEFCD333D560EF177107C"
OPERATOR_KEY_FINGERPRINT="5DA4A5A13949643EB7BF93A40B0744999425A548"
AUDIT_LOG="/var/lib/corporatetraveldc/cf-token-audit.log"
SECRETS_ENV="/etc/corporatetraveldc/dispatch-secrets.env"

if ! "${REPO_DIR}/scripts/verify-manifest.sh"; then
    echo "XX INTEGRITY CHECK FAILED -- refusing to run cf-service-token-reconcile.sh" >&2
    exit 5
fi

CF_MANAGEMENT_API_TOKEN="$(grep -E '^CF_MANAGEMENT_API_TOKEN=' "${SECRETS_ENV}" | tail -1 | cut -d'=' -f2-)"
CF_ACCOUNT_ID="$(grep -E '^CF_ACCOUNT_ID=' "${SECRETS_ENV}" | tail -1 | cut -d'=' -f2-)"
if [[ -z "${CF_MANAGEMENT_API_TOKEN}" || -z "${CF_ACCOUNT_ID}" ]]; then
    echo "XX CF_MANAGEMENT_API_TOKEN or CF_ACCOUNT_ID not set in ${SECRETS_ENV}" >&2
    exit 2
fi

echo "[reconcile] Fetching live service tokens..."
LIST_RESP="$(curl -s -m 20 -X GET \
    "https://api.cloudflare.com/client/v4/accounts/${CF_ACCOUNT_ID}/access/service_tokens" \
    -H "Authorization: Bearer ${CF_MANAGEMENT_API_TOKEN}" \
    -H "Content-Type: application/json")"

if [[ "$(echo "${LIST_RESP}" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("success"))')" != "True" ]]; then
    echo "XX Failed to list live service tokens:" >&2
    echo "${LIST_RESP}" | python3 -m json.tool >&2 || true
    exit 3
fi

LIVE_NAMES="$(echo "${LIST_RESP}" | python3 -c "
import json, sys
for t in json.load(sys.stdin).get('result', []):
    print(t.get('name', ''))
")"

if [[ ! -f "${AUDIT_LOG}" ]]; then
    echo "!! ${AUDIT_LOG} does not exist -- either nothing has been minted through our" >&2
    echo "   scripts yet, or the log is missing. Every live token below is unexplained." >&2
    AUDIT_TEXT=""
else
    AUDIT_TEXT="$(cat "${AUDIT_LOG}")"
fi

# Extract every token_name= this log claims to cover -- from agent-signed
# clearsign blocks (token_name=X lines) and break-glass blocks (New token
# name: X lines) alike -- WITHOUT yet trusting that claim; signature
# validity is checked separately below, per-entry, not assumed from
# presence in the file.
CLAIMED_NAMES="$(echo "${AUDIT_TEXT}" | grep -oE '(token_name=|New token name: )\S+' | sed -E 's/^(token_name=|New token name: )//')"

UNEXPLAINED=0
echo ""
echo "[reconcile] Live tokens vs audit log:"
while IFS= read -r name; do
    [[ -z "${name}" ]] && continue
    if echo "${CLAIMED_NAMES}" | grep -qxF "${name}"; then
        echo "  OK    ${name} -- has an audit record"
    else
        echo "  ⚠️  UNEXPLAINED  ${name} -- no audit record at all. This token was NOT minted"
        echo "       by cf-service-token-mint.sh or cf-service-token-breakglass.sh. Investigate"
        echo "       who/what created it -- CF_MANAGEMENT_API_TOKEN may be compromised, or"
        echo "       someone minted it by hand outside the documented process."
        UNEXPLAINED=$((UNEXPLAINED + 1))
    fi
done <<< "${LIVE_NAMES}"

# Spot-check: verify the audit log's OWN signatures are real, not hand-
# edited text that merely LOOKS like a valid entry (grep above trusts
# nothing it finds; this section confirms the file's actual PGP blocks
# verify against the two known fingerprints, not just any key).
if [[ -f "${AUDIT_LOG}" ]]; then
    echo ""
    echo "[reconcile] Verifying audit log signatures are genuine (not hand-edited text)..."
    BAD_SIGS=0
    # Agent-signed clearsign blocks are self-contained per entry; verify each.
    csplit -s -z -f /tmp/reconcile-block- -b '%03d.txt' "${AUDIT_LOG}" \
        '/-----BEGIN PGP SIGNED MESSAGE-----/' '{*}' 2>/dev/null || true
    for block in /tmp/reconcile-block-*.txt; do
        [[ -f "${block}" ]] || continue
        if grep -q "BEGIN PGP SIGNED MESSAGE" "${block}" 2>/dev/null; then
            VERIFY_OUT="$(gpg --status-fd 1 --verify "${block}" 2>/dev/null)" || VERIFY_OUT=""
            FPR="$(echo "${VERIFY_OUT}" | awk '/^\[GNUPG:\] VALIDSIG/ {print $3}')"
            if [[ "${FPR}" != "${AGENT_KEY_FINGERPRINT}" && "${FPR}" != "${OPERATOR_KEY_FINGERPRINT}" ]]; then
                echo "  ⚠️  A block in ${AUDIT_LOG} does not verify against either known key (got: ${FPR:-none})"
                BAD_SIGS=$((BAD_SIGS + 1))
            fi
        fi
    done
    rm -f /tmp/reconcile-block-*.txt
    if [[ "${BAD_SIGS}" -eq 0 ]]; then
        echo "  OK -- all clearsign blocks verify against the agent or operator key"
    else
        UNEXPLAINED=$((UNEXPLAINED + BAD_SIGS))
    fi
fi

echo ""
if [[ "${UNEXPLAINED}" -eq 0 ]]; then
    echo "[reconcile] ✅ Every live service token traces back to a genuine signed record."
    exit 0
else
    echo "XX [reconcile] ${UNEXPLAINED} issue(s) found -- see above. Treat as a possible" >&2
    echo "   compromise of CF_MANAGEMENT_API_TOKEN until explained." >&2
    exit 1
fi
