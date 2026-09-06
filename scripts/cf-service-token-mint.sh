#!/usr/bin/env bash
# scripts/cf-service-token-mint.sh
# Mints (first run) or rotates (every run after) a Cloudflare Access
# Service Token, using the account-scoped CF_MANAGEMENT_API_TOKEN
# (Access: Service Tokens: Edit only -- see dispatch-secrets.env comment
# for how that credential was created and scoped, 2026-08-13).
#
# Why rotate-in-place rather than always minting fresh: Cloudflare's rotate
# endpoint keeps the same client_id and issues a new client_secret. As long
# as the Access application's policy trusts "Any Service Token" (not a
# pinned specific token id -- see the policy setup step), the policy never
# needs to be touched again after the first mint, on this run or any
# future rotation. That's the whole point of doing it this way instead of
# deleting+recreating each time.
#
# Output: writes CF_ACCESS_CLIENT_ID / CF_ACCESS_CLIENT_SECRET to
# /var/lib/corporatetraveldc/cf-service-token.env (mode 600, atomic
# replace), a small dedicated EnvironmentFile -- NOT appended into the
# main dispatch-secrets.env, since this file gets fully rewritten on every
# rotation and dispatch-secrets.env is hand-edited/append-only by
# convention elsewhere in this repo. Deliberately NOT under
# /etc/corporatetraveldc/ either -- that directory is root:corporatetraveldc
# 750 (group can read existing files, but the corporatetraveldc user can't
# create new ones there -- confirmed live 2026-08-13). /var/lib/corporatetraveldc
# is fully corporatetraveldc-owned and already holds other auto-managed
# local state (ollama-lock/ etc.), so it's the natural fit for a file this
# script rewrites on its own on a schedule, vs. the hand-managed *.env
# files in /etc/corporatetraveldc.
#
# Usage:
#   scripts/cf-service-token-mint.sh <token-name>
#   scripts/cf-service-token-mint.sh ctdi-mcp-bypass
#
# Never prints CF_MANAGEMENT_API_TOKEN, the resulting client_secret, or
# CF_AGENT_SIGNING_KEY_PASSPHRASE to stdout/logs -- only success/failure
# and non-secret identifiers.
#
# 2026-08-14: every successful run clear-signs an audit record (action,
# token name, non-secret client_id, timestamp) with the passphrase-
# protected CTDI Pi Agent Signing Key (security/pi-agent-signing-key.pub.asc,
# signing-subkey fingerprint 1946CA0DD89CD2A3A46DEFCD333D560EF177107C --
# primary key fingerprint is C0E92095063C7AE670E590563A0E7B60576BBF22, a
# DIFFERENT value; gpg --verify reports whichever actually signed, see
# cf-service-token-reconcile.sh's own comment for how this was confirmed)
# and appends it to
# AUDIT_LOG. The passphrase lives in dispatch-secrets.env specifically so
# this script can read and supply it -- it's at-rest protection (raises
# the bar past "just steal the key file"), not proof of human involvement.
# This signature is a tamper-evident record of ROUTINE automated activity
# ONLY. It is NOT accepted as authorization by
# scripts/cf-service-token-breakglass.sh, which requires a signature from
# the separate, genuinely-human-passphrase-protected operator key instead
# -- the whole point of two keys is that this one's signature can never be
# mistaken for proof of operator authorization. See the reconciliation
# check (scripts/cf-service-token-reconcile.sh) for how an unsigned or
# wrong-key-signed live token gets flagged as presumptively unauthorized.
set -euo pipefail

AGENT_SIGNING_KEY="pi-agent-signing@corporatetraveldc.local"
AUDIT_LOG="/var/lib/corporatetraveldc/cf-token-audit.log"

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if ! "${REPO_DIR}/scripts/verify-manifest.sh"; then
    echo "XX INTEGRITY CHECK FAILED -- refusing to run cf-service-token-mint.sh" >&2
    exit 5
fi

if [[ $# -ne 1 ]]; then
    echo "Usage: $0 <token-name>" >&2
    exit 2
fi
TOKEN_NAME="$1"

SECRETS_ENV="/etc/corporatetraveldc/dispatch-secrets.env"
OUT_ENV="/var/lib/corporatetraveldc/cf-service-token.env"

if [[ ! -f "${SECRETS_ENV}" ]]; then
    echo "XX ${SECRETS_ENV} not found" >&2
    exit 2
fi

CF_MANAGEMENT_API_TOKEN="$(grep -E '^CF_MANAGEMENT_API_TOKEN=' "${SECRETS_ENV}" | tail -1 | cut -d'=' -f2-)"
CF_ACCOUNT_ID="$(grep -E '^CF_ACCOUNT_ID=' "${SECRETS_ENV}" | tail -1 | cut -d'=' -f2-)"

if [[ -z "${CF_MANAGEMENT_API_TOKEN}" || -z "${CF_ACCOUNT_ID}" ]]; then
    echo "XX CF_MANAGEMENT_API_TOKEN or CF_ACCOUNT_ID not set in ${SECRETS_ENV}" >&2
    exit 2
fi

API_BASE="https://api.cloudflare.com/client/v4/accounts/${CF_ACCOUNT_ID}/access/service_tokens"

echo "[cf-service-token-mint] Looking up existing service token named '${TOKEN_NAME}'..."
LIST_RESP="$(curl -s -m 20 -X GET "${API_BASE}" \
    -H "Authorization: Bearer ${CF_MANAGEMENT_API_TOKEN}" \
    -H "Content-Type: application/json")"

if [[ "$(echo "${LIST_RESP}" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("success"))')" != "True" ]]; then
    echo "XX Failed to list service tokens:" >&2
    echo "${LIST_RESP}" | python3 -m json.tool >&2 || true
    exit 3
fi

EXISTING_ID="$(echo "${LIST_RESP}" | python3 -c "
import json, sys
data = json.load(sys.stdin)
for t in data.get('result', []):
    if t.get('name') == '${TOKEN_NAME}':
        print(t.get('id'))
        break
")"

if [[ -n "${EXISTING_ID}" ]]; then
    echo "[cf-service-token-mint] Found existing token (id=${EXISTING_ID}) -- rotating secret..."
    RESP="$(curl -s -m 20 -X POST "${API_BASE}/${EXISTING_ID}/rotate" \
        -H "Authorization: Bearer ${CF_MANAGEMENT_API_TOKEN}" \
        -H "Content-Type: application/json")"
    ACTION="rotated"
else
    echo "[cf-service-token-mint] No existing token named '${TOKEN_NAME}' -- minting new..."
    RESP="$(curl -s -m 20 -X POST "${API_BASE}" \
        -H "Authorization: Bearer ${CF_MANAGEMENT_API_TOKEN}" \
        -H "Content-Type: application/json" \
        -d "{\"name\": \"${TOKEN_NAME}\"}")"
    ACTION="minted"
fi

SUCCESS="$(echo "${RESP}" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("success"))' 2>/dev/null || echo "False")"
if [[ "${SUCCESS}" != "True" ]]; then
    echo "XX Failed to ${ACTION%ed} service token:" >&2
    echo "${RESP}" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    print(json.dumps(d.get('errors', d), indent=2))
except Exception:
    print('(unparseable response)')
" >&2
    exit 3
fi

CLIENT_ID="$(echo "${RESP}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["result"]["client_id"])')"
CLIENT_SECRET="$(echo "${RESP}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["result"]["client_secret"])')"

if [[ -z "${CLIENT_ID}" || -z "${CLIENT_SECRET}" ]]; then
    echo "XX Response missing client_id/client_secret -- not writing anything" >&2
    exit 3
fi

TMP_ENV="$(mktemp "$(dirname "${OUT_ENV}")/.cf-service-token.env.XXXXXX")"
{
    echo "# Auto-generated by scripts/cf-service-token-mint.sh -- DO NOT EDIT BY HAND."
    echo "# Last ${ACTION} $(date -u +%Y-%m-%dT%H:%M:%SZ) for token name '${TOKEN_NAME}'."
    echo "CF_ACCESS_CLIENT_ID=${CLIENT_ID}"
    echo "CF_ACCESS_CLIENT_SECRET=${CLIENT_SECRET}"
} > "${TMP_ENV}"
chmod 600 "${TMP_ENV}"
mv -f "${TMP_ENV}" "${OUT_ENV}"

echo "[cf-service-token-mint] ${ACTION} OK -- client_id=${CLIENT_ID} (secret written to ${OUT_ENV}, not logged)"

# ── Audit-sign this routine action with the agent key ───────────────────────
# Best-effort: a signing failure here does NOT undo the mint/rotate above
# (access is already restored/working by this point) -- it just means this
# one action won't have an audit record, which the reconciliation check
# will flag on its own. Never block a successful token operation on the
# audit step failing.
AGENT_KEY_PASSPHRASE="$(grep -E '^CF_AGENT_SIGNING_KEY_PASSPHRASE=' "${SECRETS_ENV}" | tail -1 | cut -d'=' -f2-)"
if [[ -z "${AGENT_KEY_PASSPHRASE}" ]]; then
    echo "!! CF_AGENT_SIGNING_KEY_PASSPHRASE not set -- skipping audit signature (non-fatal)" >&2
else
    AUDIT_RECORD="action=${ACTION}
token_name=${TOKEN_NAME}
client_id=${CLIENT_ID}
timestamp=$(date -u +%Y-%m-%dT%H:%M:%SZ)
signed_by=agent"
    if SIGNED="$(echo "${AUDIT_RECORD}" | gpg --batch --pinentry-mode loopback \
        --passphrase "${AGENT_KEY_PASSPHRASE}" --local-user "${AGENT_SIGNING_KEY}" \
        --clearsign 2>/dev/null)"; then
        mkdir -p "$(dirname "${AUDIT_LOG}")"
        { echo "${SIGNED}"; echo ""; } >> "${AUDIT_LOG}"
        echo "[cf-service-token-mint] audit record signed and appended to ${AUDIT_LOG}"
    else
        echo "!! Audit signing failed (non-fatal, token operation above already succeeded)" >&2
    fi
fi
unset AGENT_KEY_PASSPHRASE
