#!/bin/bash
# scripts/cf-dns-record.sh -- upsert (or delete) ONE Cloudflare DNS record
# with the locally-managed management token, instead of the dashboard.
#
# Same post-incident pattern as cf-service-token-*.sh (2026-09-22):
#   - CF_MANAGEMENT_API_TOKEN / CF_ACCOUNT_ID read line-wise from
#     dispatch-secrets.env (never shell-sourced), used only inside curl,
#     never echoed, never on argv, never in an error message.
#   - curl -s -4: the token is IP-filtered to the box's two public IPv4
#     egresses; a v6 call returns code 1000 "Invalid API Token", which is
#     indistinguishable from a revoked token.
#   - integrity self-check first; audit line (no secrets) on every write.
#   - error paths print only Cloudflare's errors[].code/message (which never
#     contain the token) or a curl exit code -- never the raw response.
#   - any UUID in printed record content is masked (tunnel ids are treated as
#     sensitive by scrub-public-tree.py; keep them out of transcripts).
#
# First use (2026-10-03): executivestandard.example.com -> CNAME
# target.substack-custom-domains.com, proxied=false (Substack custom domain;
# Substack requires DNS-only). Refuses to act if the name has more than one
# record (A+AAAA, etc.) -- that's an operator decision, not a guess.
#
# Usage:
#   cf-dns-record.sh NAME TYPE CONTENT [--proxied true|false] [--ttl N] [--dry-run]
#   cf-dns-record.sh NAME --show
#   cf-dns-record.sh NAME --delete [--dry-run]
# ASCII output only.

set -uo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SELF_DIR}/.." && pwd)"
if ! "${REPO_ROOT}/scripts/verify-manifest.sh" "scripts/cf-dns-record.sh"; then
    echo "cf-dns-record: INTEGRITY CHECK FAILED -- refusing to run" >&2
    exit 1
fi

SECRETS_ENV="/etc/corporatetraveldc/dispatch-secrets.env"
AUDIT_LOG="/var/lib/corporatetraveldc/cf-token-audit.log"
API="https://api.cloudflare.com/client/v4"

NAME="${1:-}"; shift || true
TYPE=""; CONTENT=""; PROXIED="false"; TTL="1"; DRY=0; MODE="upsert"
if [[ -z "${NAME}" ]]; then echo "usage: $0 NAME TYPE CONTENT [--proxied true|false] [--ttl N] [--dry-run] | NAME --show | NAME --delete" >&2; exit 2; fi
while [[ $# -gt 0 ]]; do
    case "$1" in
        --show)    MODE="show"; shift ;;
        --delete)  MODE="delete"; shift ;;
        --proxied) PROXIED="${2:-false}"; shift 2 ;;
        --ttl)     TTL="${2:-1}"; shift 2 ;;
        --dry-run) DRY=1; shift ;;
        -*)        echo "cf-dns-record: unknown option $1" >&2; exit 2 ;;
        *)         if [[ -z "${TYPE}" ]]; then TYPE="$1"; elif [[ -z "${CONTENT}" ]]; then CONTENT="$1"; else echo "cf-dns-record: unexpected arg $1" >&2; exit 2; fi; shift ;;
    esac
done
if [[ "${MODE}" == "upsert" && ( -z "${TYPE}" || -z "${CONTENT}" ) ]]; then echo "cf-dns-record: NAME TYPE CONTENT required for an upsert" >&2; exit 2; fi
[[ "${PROXIED}" =~ ^(true|false)$ ]] || { echo "cf-dns-record: --proxied must be true or false" >&2; exit 2; }

TOKEN="$(grep -E '^CF_MANAGEMENT_API_TOKEN=' "${SECRETS_ENV}" 2>/dev/null | tail -1 | cut -d'=' -f2-)"
ACCOUNT="$(grep -E '^CF_ACCOUNT_ID=' "${SECRETS_ENV}" 2>/dev/null | tail -1 | cut -d'=' -f2-)"
if [[ -z "${TOKEN}" || -z "${ACCOUNT}" ]]; then echo "cf-dns-record: CF_MANAGEMENT_API_TOKEN / CF_ACCOUNT_ID not set in ${SECRETS_ENV}" >&2; exit 3; fi

mask() { sed -E 's/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/<uuid>/g'; }

# cf METHOD PATH [JSON-BODY] -> prints response body; exit code = curl's.
cf() {
    local method="$1" path="$2" body="${3:-}"
    local args=(-s -4 -m 20 -X "${method}" "${API}${path}" -H "Authorization: Bearer ${TOKEN}" -H "Content-Type: application/json")
    [[ -n "${body}" ]] && args+=(--data "${body}")
    curl "${args[@]}"
}
# errs JSON -> one line of code/message pairs from errors[] (never the token)
# No escaped quotes inside inline python -- bash single-quoting turns \" into
# a literal backslash and python throws SyntaxError (bit this script twice on
# 2026-10-03; the DNS write itself had succeeded, only the reporting broke).
errs() { python3 -c "import json,sys
try: d=json.load(sys.stdin)
except Exception: print('(unparseable response)'); sys.exit()
print('ok' if d.get('success') else '; '.join('%s: %s' % (e.get('code'), e.get('message')) for e in d.get('errors',[])) or 'unknown error')"; }
jq1() { python3 -c "import json,sys; d=json.load(sys.stdin); print($1)"; }

# zone = apex of NAME (last two labels). Good enough for this zone.
APEX="$(echo "${NAME}" | awk -F. '{print $(NF-1)"."$NF}')"
ZR="$(cf GET "/zones?name=${APEX}&per_page=1")" || { echo "cf-dns-record: zone lookup failed (curl exit $?)" >&2; exit 4; }
ZONE_ID="$(echo "${ZR}" | jq1 'd["result"][0]["id"] if d.get("success") and d.get("result") else ""' 2>/dev/null)"
if [[ -z "${ZONE_ID}" ]]; then echo "cf-dns-record: zone ${APEX} not found or token lacks Zone:Read -- $(echo "${ZR}" | errs)" >&2; exit 4; fi

RR="$(cf GET "/zones/${ZONE_ID}/dns_records?name=${NAME}&per_page=50")" || { echo "cf-dns-record: record lookup failed (curl exit $?)" >&2; exit 4; }
COUNT="$(echo "${RR}" | jq1 'len(d.get("result",[]))' 2>/dev/null || echo 0)"
echo "cf-dns-record: ${NAME} -- ${COUNT} existing record(s):"
RESP="${RR}" python3 -c "import json,os
for r in json.loads(os.environ['RESP']).get('result',[]):
    print('  %s  %-5s proxied=%-5s ttl=%s  -> %s' % (r['id'], r['type'], str(r.get('proxied')).lower(), r.get('ttl'), r.get('content')))" | mask

[[ "${MODE}" == "show" ]] && exit 0

if (( COUNT > 1 )); then
    echo "cf-dns-record: more than one record for ${NAME} -- refusing to guess which to change. Delete/merge on the dashboard or extend this script with a --type selector." >&2; exit 5
fi
REC_ID="$(echo "${RR}" | jq1 'd["result"][0]["id"] if d.get("result") else ""' 2>/dev/null)"
CUR_TYPE="$(echo "${RR}" | jq1 'd["result"][0]["type"] if d.get("result") else ""' 2>/dev/null)"

audit() { printf '%s cf-dns-record %s %s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1" "${NAME}" "$2" >> "${AUDIT_LOG}" 2>/dev/null || true; }

if [[ "${MODE}" == "delete" ]]; then
    [[ -z "${REC_ID}" ]] && { echo "cf-dns-record: nothing to delete"; exit 0; }
    (( DRY )) && { echo "[DRY-RUN] would DELETE record ${REC_ID} (${CUR_TYPE})"; exit 0; }
    R="$(cf DELETE "/zones/${ZONE_ID}/dns_records/${REC_ID}")" || { echo "cf-dns-record: delete failed (curl exit $?)" >&2; exit 6; }
    S="$(echo "${R}" | errs)"; audit DELETE "${CUR_TYPE} -> ${S}"
    [[ "${S}" == "ok" ]] && echo "cf-dns-record: deleted ${NAME} (${CUR_TYPE})" || { echo "cf-dns-record: delete refused -- ${S}" >&2; exit 6; }
    exit 0
fi

BODY="$(python3 -c "import json;print(json.dumps({'type':'${TYPE}','name':'${NAME}','content':'${CONTENT}','proxied':'${PROXIED}'=='true','ttl':int('${TTL}')}))")"
if [[ -n "${REC_ID}" ]]; then
    echo "cf-dns-record: will UPDATE ${REC_ID} (${CUR_TYPE}) -> ${TYPE} ${CONTENT} proxied=${PROXIED} ttl=${TTL}"
    (( DRY )) && { echo "[DRY-RUN] no change made"; exit 0; }
    R="$(cf PUT "/zones/${ZONE_ID}/dns_records/${REC_ID}" "${BODY}")" || { echo "cf-dns-record: update failed (curl exit $?)" >&2; exit 6; }
    VERB="UPDATE"
else
    echo "cf-dns-record: will CREATE ${TYPE} ${NAME} -> ${CONTENT} proxied=${PROXIED} ttl=${TTL}"
    (( DRY )) && { echo "[DRY-RUN] no change made"; exit 0; }
    R="$(cf POST "/zones/${ZONE_ID}/dns_records" "${BODY}")" || { echo "cf-dns-record: create failed (curl exit $?)" >&2; exit 6; }
    VERB="CREATE"
fi
S="$(echo "${R}" | errs)"; audit "${VERB}" "${TYPE} ${CONTENT} proxied=${PROXIED} -> ${S}"
if [[ "${S}" == "ok" ]]; then
    echo "cf-dns-record: ${VERB} ok:"; RESP="${R}" python3 -c "import json,os
r=json.loads(os.environ['RESP'])['result']
print('  %s %s -> %s proxied=%s ttl=%s' % (r['type'], r['name'], r['content'], str(r.get('proxied')).lower(), r.get('ttl')))" | mask
else
    echo "cf-dns-record: ${VERB} refused by Cloudflare -- ${S}" >&2; exit 6
fi
