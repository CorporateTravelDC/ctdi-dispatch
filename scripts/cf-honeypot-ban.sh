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
# Secrets handling. 2026-09-24: REWRITTEN -- the previous design was wrong in a
# way worth recording, because the reasoning was sound and the conclusion was
# still unsafe.
#
# It read cfzone/cftoken from the environment (CFZONE/CFTOKEN), set by the
# actionban/actionunban line as `VAR=val script ...` rather than as script
# ARGUMENTS, on the grounds that arguments appear in `ps aux` to any local user
# while environment variables only appear in /proc/<pid>/environ, which is
# root-only. That is all true, and it is not the leak.
#
# The leak is that fail2ban EXPANDS and LOGS the action command string before
# executing it. `CFTOKEN="<cftoken>"` becomes the literal token in
# /var/log/fail2ban.log on every ban, every unban, and -- worst -- every error,
# where it is repeated alongside the failure context. The token is in the
# command line before it is ever an environment variable, so reasoning about
# `ps` and /proc never reaches it. Operator observed it across multiple log
# files 2026-09-23.
#
# NOW: the token is read from a FILE by this script. It never appears in the
# action line, so there is nothing for fail2ban to expand or log. Pass the
# PATH, not the secret:
#   CFTOKEN_FILE=/etc/corporatetraveldc/cf-honeypot.token   (root:root 0600)
#
# CFTOKEN is still honoured as a deprecated fallback so a stale jail.local does
# not break banning mid-deploy, but it warns to stderr every invocation. Remove
# the fallback once jail.local is confirmed migrated.
#
# CFZONE stays in the environment: a zone ID is not a credential, and it is
# already public in every API URL this script builds.
set -uo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SELF_DIR}/.." && pwd)"
NOTES_SCRIPT="${SELF_DIR}/cf-honeypot-notes.sh"

# Signed-manifest integrity check (docs/COMPLIANCE_SECURITY.md "Signed
# Manifest Integrity") -- refuse to run at all if this script or the notes
# script it calls doesn't match its signed hash. A script can't fully
# guarantee it detects tampering with its OWN check (see that doc section
# for the honest limitation); fail2ban's action.d config chains an
# independent verify-manifest.sh call before ever invoking this script, as
# defense in depth against exactly that.
if ! "${REPO_ROOT}/scripts/verify-manifest.sh" "scripts/cf-honeypot-ban.sh" \
   || ! "${REPO_ROOT}/scripts/verify-manifest.sh" "scripts/cf-honeypot-notes.sh"; then
    echo "cf-honeypot-ban: INTEGRITY CHECK FAILED -- refusing to run" >&2
    exit 1
fi

mode="${1:?usage: cf-honeypot-ban.sh <ban|unban> <ip> [target]}"
ip="${2:?usage: cf-honeypot-ban.sh <ban|unban> <ip> [target]}"
target="${3:-ip}"

zone="${CFZONE:?CFZONE env var not set}"

# Token resolution: file first, deprecated env fallback second. See the
# "Secrets handling" note at the top for why the env path is unsafe.
# Lives in ~/.secrets with every other credential on this box -- one place, by
# operator directive. That requires one SELinux exception, documented here
# because it is non-obvious and will look like a mistake to a future reader:
#
# fail2ban runs CONFINED as fail2ban_t. It is the only confined consumer of
# ~/.secrets on this box -- the poller, web, the containers and the operator's
# own shell all run unconfined_service_t, which is why every other credential
# here "just works" and this one does not. Verified 2026-09-24: sesearch finds
# NO allow rule for fail2ban_t -> user_home_t, so an unlabelled token here is
# denied and banning fails SILENTLY.
#
# Fix is to relabel the single file, not to move it:
#   semanage fcontext -a -t etc_t '/home/corporatetraveldc/\.secrets/cf-honeypot\.token'
#   restorecon -v /home/corporatetraveldc/.secrets/cf-honeypot.token
# etc_t carries base_ro_file_type, which fail2ban_t reads via
# `allow domain base_ro_file_type:file read`. The rule is persistent across
# relabels; a plain chcon is NOT and will be reverted by the next restorecon.
#
# If banning ever stops working after a filesystem relabel, check this first:
#   ausearch -m avc --start today | grep fail2ban
CFTOKEN_FILE="${CFTOKEN_FILE:-$HOME/.secrets/cf-honeypot.token}"
# fail2ban runs as root, so $HOME is /root when invoked by the service. Fall
# back to the literal path rather than depending on the caller's environment.
if [[ ! -r "${CFTOKEN_FILE}" && -r /home/corporatetraveldc/.secrets/cf-honeypot.token ]]; then
    CFTOKEN_FILE=/home/corporatetraveldc/.secrets/cf-honeypot.token
fi
token=""
if [[ -r "${CFTOKEN_FILE}" ]]; then
    # Tolerate a trailing newline, surrounding whitespace, a leading
    # `CFTOKEN=` and surrounding quotes, so the same file works whether it was
    # written as a bare token or as an env-file line.
    token="$(sed -E 's/^[[:space:]]*(CFTOKEN=)?//; s/^["'"'"']//; s/["'"'"'][[:space:]]*$//; s/[[:space:]]+$//' \
             "${CFTOKEN_FILE}" | grep -m1 -E '.' || true)"
fi
if [[ -z "${token}" && -n "${CFTOKEN:-}" ]]; then
    token="${CFTOKEN}"
    echo "cf-honeypot-ban: WARNING -- token came from the CFTOKEN env var." >&2
    echo "cf-honeypot-ban: that value is expanded into fail2ban's action log on" >&2
    echo "cf-honeypot-ban: every invocation. Migrate jail.local to CFTOKEN_FILE." >&2
fi
if [[ -z "${token}" ]]; then
    # Fail closed and say which path was tried -- never echo the value.
    echo "cf-honeypot-ban: no token available (tried ${CFTOKEN_FILE}, then CFTOKEN env)" >&2
    exit 1
fi

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
