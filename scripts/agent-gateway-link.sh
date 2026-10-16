#!/usr/bin/env bash
# scripts/agent-gateway-link.sh -- link a vendor MCP client to the agent gateway
# from this headless box, with no browser and no phone (operator 2026-10-16).
#
#   agent-gateway-link.sh [AUTHORIZE_URL]
#
# Why: the vendor's OAuth flow (codex `mcp login`, and the same for any
# loopback-redirect client) prints an authorize URL and waits on
# http://127.0.0.1:<port>/callback/... for the code. Opening that URL on a
# phone does not work: the gateway redirects the PHONE's browser to 127.0.0.1,
# which is the phone, not this box, so the client never gets its code
# (2026-10-16: two signed codex links stalled exactly there).
#
# What this does, all on the box:
#   1. GET the authorize URL (that creates the pending request and the
#      connector-link approval; the consent page is parsed, not displayed);
#   2. sign the approval with scripts/approve.sh allow <id> (your approval key,
#      interactive, from the terminal);
#   3. poll /oauth/authorize/status until the gateway issues the code;
#   4. GET the loopback callback URL locally so the waiting client receives it.
# The code is consumed by that local fetch and never printed.
#
# Run the vendor login in one terminal (e.g. `sudo -u <agent> -i codex mcp login <name>`),
# this script in another, and paste the URL when asked (or pass it as $1).
# Test hooks: LINK_FETCH (command used instead of curl, given the URL),
# LINK_APPROVE (instead of approve.sh allow), LINK_POLL_SLEEP (seconds).
set -uo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BASE="${GATEWAY_BASE_URL:-https://agents.example.com}"
FETCH="${LINK_FETCH:-curl -sS --max-time 20}"
APPROVE="${LINK_APPROVE:-${REPO_ROOT}/scripts/with-dispatch-env.sh bash ${REPO_ROOT}/scripts/approve.sh allow}"
POLL_SLEEP="${LINK_POLL_SLEEP:-3}"
TTY_IN=/dev/tty; ( : </dev/tty ) 2>/dev/null || TTY_IN=/dev/null   # no terminal (tests, timers): nothing interactive

URL="${1:-}"
if [[ -z "$URL" ]]; then
    printf 'Paste the authorize URL printed by the vendor login, then Enter: '
    read -r URL <"$TTY_IN"
fi
case "$URL" in
    "${BASE}/oauth/authorize?"*) ;;
    *) echo "agent-gateway-link: not an authorize URL for ${BASE}" >&2; exit 64 ;;
esac

PAGE="$($FETCH "$URL")" || { echo "agent-gateway-link: authorize request failed" >&2; exit 1; }
REQ="$(sed -n "s/.*status?req=\([^'&\"]*\).*/\1/p" <<<"$PAGE" | sed -n 1p)"
AID="$(sed -n 's/.*approve\.sh allow \([0-9a-f-]\{36\}\).*/\1/p' <<<"$PAGE" | sed -n 1p)"
if [[ -z "$REQ" || -z "$AID" ]]; then
    echo "agent-gateway-link: no pending request in the gateway's answer (refused, expired, or the account is not live):" >&2
    printf '%s\n' "$PAGE" | sed -n '1,8p' >&2
    exit 1
fi
echo "pending request ${REQ}; approval ${AID} -- signing"
$APPROVE "$AID" <"$TTY_IN" || { echo "agent-gateway-link: approval not signed" >&2; exit 1; }

LOC=""
for _ in $(seq 1 40); do
    LOC="$($FETCH "${BASE}/oauth/authorize/status?req=${REQ}" \
          | python3 -c 'import json,sys
try: print(json.load(sys.stdin).get("location") or "")
except Exception: print("")')"
    [[ -n "$LOC" ]] && break
    sleep "$POLL_SLEEP"
done
[[ -n "$LOC" ]] || { echo "agent-gateway-link: the gateway issued no redirect (approval denied or expired?)" >&2; exit 1; }
case "$LOC" in
    http://127.0.0.1:*|http://localhost:*|http://\[::1\]:*) ;;
    *) echo "agent-gateway-link: redirect is not a loopback URL; deliver it to the client yourself: ${LOC%%\?*}" >&2; exit 2 ;;
esac
$FETCH "$LOC" >/dev/null || { echo "agent-gateway-link: the local client did not accept the callback (is the login still waiting?)" >&2; exit 1; }
echo "callback delivered to the waiting client"
