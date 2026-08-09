#!/bin/bash
# scripts/cf-honeypot-ban.sh -- actually calls the Cloudflare Firewall Access
# Rules API for a honeypot ban/unban, with real error visibility.
#
# Why this exists (2026-08-09 postmortem, round 1): the first version of this
# action piped cf-honeypot-notes.sh straight into `curl -s -X POST ...` from
# fail2ban's actionban line. fail2ban's own log confirmed the ban fired
# (filter matched, NOTICE Ban <ip>) but no Cloudflare rule was ever created --
# and nothing showed as an ERROR in fail2ban.log either. Root cause: plain
# `curl -s` only returns a non-zero exit code on connection-level failures,
# NOT on HTTP error responses (4xx/5xx) -- so if the API call failed for any
# reason, curl still exited 0, fail2ban treated the action as successful, and
# the failure was invisible. This version checks the actual HTTP status.
#
# Round 2 (same day, discovered testing the unban path): checking HTTP status
# alone is still not enough for this API -- Cloudflare can return 200 OK with
# {"success": false, ...} in the body for validation failures, which the round-1
# check would have silently treated as success. Every call below now also
# checks the JSON body's .success field. This round also fixed two bugs in the
# unban lookup specifically: (1) `curl -X GET --data-urlencode ...` without
# `-G` sends the params as a request BODY, not a URL query string -- Cloudflare
# ignored them entirely and returned the FULL unfiltered rule list; (2) with
# that bug, `.result[0].id` would grab whatever rule happened to be first in
# the unfiltered list and delete THAT one -- a real risk of unbanning a
# completely unrelated IP. Fixed with -G, and as defense in depth even against
# a correctly-filtered-but-surprising response, the delete step now verifies
# the looked-up rule's own configuration.value actually matches the target ip
# before deleting anything.
#
# Secrets handling: cfzone/cftoken are read from environment (CFZONE/CFTOKEN),
# set by the actionban/actionunban command line as `VAR=val script ...` --
# NOT passed as script arguments, which would appear in `ps aux` output to
# any local user who can see the process list. Env-var passing only exposes
# them via /proc/<pid>/environ, readable only by root (fail2ban already runs
# as root, same as jail.local itself) -- no wider exposure than what already
# exists.
set -uo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NOTES_SCRIPT="${SELF_DIR}/cf-honeypot-notes.sh"

mode="${1:?usage: cf-honeypot-ban.sh <ban|unban> <ip> [target]}"
ip="${2:?usage: cf-honeypot-ban.sh <ban|unban> <ip> [target]}"
target="${3:-ip}"

zone="${CFZONE:?CFZONE env var not set}"
token="${CFTOKEN:?CFTOKEN env var not set}"

API_URL="https://api.cloudflare.com/client/v4/zones/${zone}/firewall/access_rules/rules"
AUTH_HDR="Authorization: Bearer ${token}"

# Checks BOTH the HTTP status AND the JSON body's .success field -- Cloudflare
# can return 200 with success:false, which an HTTP-status-only check misses.
# Prints a clear failure message to stderr and returns non-zero; the caller's
# body file is left in place either way for the caller to read/clean up.
cf_call_ok() {
    local label="$1" http_code="$2" body_file="$3"
    if [[ "${http_code}" != 2* ]]; then
        echo "cf-honeypot-ban: ${label} FAILED ip=${ip} http=${http_code} body=$(tr -d '\n' < "${body_file}")" >&2
        return 1
    fi
    if [[ "$(jq -r '.success' < "${body_file}" 2>/dev/null)" != "true" ]]; then
        echo "cf-honeypot-ban: ${label} FAILED ip=${ip} http=${http_code} (success:false) body=$(tr -d '\n' < "${body_file}")" >&2
        return 1
    fi
    return 0
}

case "${mode}" in
  ban)
    payload="$("${NOTES_SCRIPT}" "${ip}")"
    resp_body="$(mktemp)"
    http_code=$(curl -s -o "${resp_body}" -w '%{http_code}' -X POST "${API_URL}" \
                    -H "${AUTH_HDR}" -H "Content-Type: application/json" \
                    --data-binary "${payload}")
    if ! cf_call_ok "BAN" "${http_code}" "${resp_body}"; then
        rm -f "${resp_body}"
        exit 1
    fi
    rm -f "${resp_body}"
    ;;

  unban)
    list_body="$(mktemp)"
    # -G: --data-urlencode below becomes URL query params (a GET's actual
    # filter), NOT a request body, which Cloudflare would silently ignore.
    http_code=$(curl -s -G -o "${list_body}" -w '%{http_code}' -X GET "${API_URL}" \
                    -H "${AUTH_HDR}" -H "Content-Type: application/json" \
                    --data-urlencode "mode=block" \
                    --data-urlencode "configuration.target=${target}" \
                    --data-urlencode "configuration.value=${ip}")
    if ! cf_call_ok "UNBAN LOOKUP" "${http_code}" "${list_body}"; then
        rm -f "${list_body}"
        exit 1
    fi
    rule_id="$(jq -r '.result[0].id // empty' < "${list_body}")"
    found_ip="$(jq -r '.result[0].configuration.value // empty' < "${list_body}")"
    rm -f "${list_body}"
    if [[ -z "${rule_id}" ]]; then
        echo "cf-honeypot-ban: unban: no rule found for ip=${ip} (already gone, or never created -- not an error)"
        exit 0
    fi
    # Defense in depth: never delete a rule whose own IP doesn't match the one
    # we were asked to unban, even if the lookup above somehow returned the
    # wrong thing.
    if [[ "${found_ip}" != "${ip}" ]]; then
        echo "cf-honeypot-ban: UNBAN ABORTED ip=${ip} -- lookup returned a rule for a DIFFERENT ip (${found_ip}, rule_id=${rule_id}); refusing to delete it" >&2
        exit 1
    fi
    del_body="$(mktemp)"
    http_code=$(curl -s -o "${del_body}" -w '%{http_code}' -X DELETE "${API_URL}/${rule_id}" \
                    -H "${AUTH_HDR}" -H "Content-Type: application/json" \
                    --data '{"cascade":"none"}')
    if ! cf_call_ok "UNBAN DELETE" "${http_code}" "${del_body}"; then
        rm -f "${del_body}"
        exit 1
    fi
    rm -f "${del_body}"
    ;;

  *)
    echo "cf-honeypot-ban: unknown mode '${mode}'" >&2
    exit 1
    ;;
esac
