#!/bin/bash
# scripts/cf-honeypot-ban.sh -- actually calls the Cloudflare Firewall Access
# Rules API for a honeypot ban/unban, with real error visibility.
#
# Why this exists (2026-08-09 postmortem): the first version of this action
# piped cf-honeypot-notes.sh straight into `curl -s -X POST ...` from
# fail2ban's actionban line. fail2ban's own log confirmed the ban fired
# (filter matched, NOTICE Ban <ip>) but no Cloudflare rule was ever created --
# and nothing showed as an ERROR in fail2ban.log either. Root cause: plain
# `curl -s` only returns a non-zero exit code on connection-level failures,
# NOT on HTTP error responses (4xx/5xx) -- so if the API call failed for any
# reason, curl still exited 0, fail2ban treated the action as successful, and
# the failure was invisible. A manual run of the same pipeline (as an
# interactive user, not via fail2ban's actual invocation) worked fine, which
# is what flagged this as an environment/invocation-specific gap rather than
# a broken script. This version checks the actual HTTP status and writes a
# clear message to stderr + a non-zero exit on failure, so fail2ban surfaces
# it as a real ERROR log line instead of silence.
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

case "${mode}" in
  ban)
    payload="$("${NOTES_SCRIPT}" "${ip}")"
    resp_body="$(mktemp)"
    http_code=$(curl -s -o "${resp_body}" -w '%{http_code}' -X POST "${API_URL}" \
                    -H "${AUTH_HDR}" -H "Content-Type: application/json" \
                    --data-binary "${payload}")
    if [[ "${http_code}" != 2* ]]; then
        echo "cf-honeypot-ban: BAN FAILED ip=${ip} http=${http_code} body=$(tr -d '\n' < "${resp_body}")" >&2
        rm -f "${resp_body}"
        exit 1
    fi
    rm -f "${resp_body}"
    ;;

  unban)
    list_body="$(mktemp)"
    http_code=$(curl -s -o "${list_body}" -w '%{http_code}' -X GET "${API_URL}" \
                    -H "${AUTH_HDR}" -H "Content-Type: application/json" \
                    --data-urlencode "mode=block" \
                    --data-urlencode "configuration.target=${target}" \
                    --data-urlencode "configuration.value=${ip}")
    if [[ "${http_code}" != 2* ]]; then
        echo "cf-honeypot-ban: UNBAN LOOKUP FAILED ip=${ip} http=${http_code} body=$(tr -d '\n' < "${list_body}")" >&2
        rm -f "${list_body}"
        exit 1
    fi
    rule_id="$(jq -r '.result[0].id // empty' < "${list_body}")"
    rm -f "${list_body}"
    if [[ -z "${rule_id}" ]]; then
        echo "cf-honeypot-ban: unban: no rule found for ip=${ip} (already gone, or never created -- not an error)"
        exit 0
    fi
    del_body="$(mktemp)"
    http_code=$(curl -s -o "${del_body}" -w '%{http_code}' -X DELETE "${API_URL}/${rule_id}" \
                    -H "${AUTH_HDR}" -H "Content-Type: application/json" \
                    --data '{"cascade":"none"}')
    if [[ "${http_code}" != 2* ]]; then
        echo "cf-honeypot-ban: UNBAN DELETE FAILED ip=${ip} rule_id=${rule_id} http=${http_code} body=$(tr -d '\n' < "${del_body}")" >&2
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
