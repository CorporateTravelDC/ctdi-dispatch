#!/usr/bin/env bash
# scripts/agent-relink.sh -- link or relink an on-box Claude Code agent to its own agent-gateway
# connector, end to end (2026-11-05, operator: "generalize that same exact workflow for any other
# future agent relinks and/or initial linking").
#
#   scripts/agent-relink.sh ACCOUNT [--slug SLUG] [--dry-run]
#   scripts/relink/<account>.sh                     the same, one file per agent
#
# Run as the operator (corporatetraveldc), in screen claude-ea. You type two things: your sudo
# password once, and ALLOW + your approver passphrase at the link step.
#
#   1  find the account's connector (agent-gateway.sh connectors). None -> add one: slug from
#      --slug, else the account name without "ctdc-agent-anthropic-" (initial link). An enabled
#      SECOND connector for the same account is reported, never touched (the operator decides).
#   2  point the agent's Claude Code at /mcp/<slug> at LOCAL scope in its home (no .mcp.json
#      approval prompt in the way)
#   3  start `claude mcp login --no-browser ctdi-dispatch` as the agent, under a pseudo-terminal
#      (script(1)), in the background: it prints the authorize URL and listens on localhost
#   4  read the URL from the login's log and run agent-gateway.sh link with it at once (the listener
#      times out: the earlier manual flow failed every time the two steps were apart)
#   5  report what the agent's login printed and the connector's session status
#
# Proven 2026-11-05 on ctdc-agent-anthropic-claude (claude-code: active, renewed 11-05 02:08Z).
# Claude Code clients only: a Codex or cloud client has no `claude mcp login`; refused with a reason.
# The log lives in the account's own /srv folder (found by owner) and never reaches the vault
# (.log, not .md/.txt); the URL's state and code_challenge are never printed.
set -euo pipefail

usage() { sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-2}"; }
ACCOUNT=""; SLUG=""; DRY=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --slug) SLUG="${2:-}"; shift 2 ;;
    --dry-run) DRY=1; shift ;;
    -h|--help) usage 0 ;;
    -*) echo "unknown option: $1" >&2; usage ;;
    *) [[ -z "$ACCOUNT" ]] || usage; ACCOUNT="$1"; shift ;;
  esac
done
[[ -n "$ACCOUNT" ]] || usage
[[ "$ACCOUNT" =~ ^ctdc-agent-[a-z0-9-]+$ ]] || { echo "not an agent account name: $ACCOUNT" >&2; exit 2; }
[[ -z "$SLUG" || "$SLUG" =~ ^[a-z0-9][a-z0-9-]{0,40}$ ]] || { echo "bad slug: $SLUG" >&2; exit 2; }

REPO="$(cd "$(dirname "$0")/.." && pwd)"
GW="${AGENT_RELINK_GW:-$REPO/scripts/agent-gateway.sh}"   # override for tests only
BASE="${GATEWAY_BASE_URL:-https://agents.example.com}"
run() { if (( DRY )); then printf 'DRY: %s\n' "$*"; else "$@"; fi; }

if (( ! DRY )); then
  [[ "$(id -un)" == corporatetraveldc ]] || { echo "Run this as corporatetraveldc (you are $(id -un))." >&2; exit 1; }
fi
getent passwd "$ACCOUNT" >/dev/null || { echo "no such account: $ACCOUNT" >&2; exit 1; }
DIR="$(find /srv -maxdepth 1 -name 'ctdc-*' -user "$ACCOUNT" -type d 2>/dev/null | head -1)"
[[ -n "$DIR" ]] || { echo "no /srv/ctdc-* folder owned by $ACCOUNT; the login log needs one" >&2; exit 1; }
LOG="$DIR/gateway-login.log"

if (( ! DRY )); then
  echo "== sudo: authenticate once"
  sudo -v </dev/tty
  sudo -n -u "$ACCOUNT" -i bash -c 'command -v claude >/dev/null' </dev/null \
    || { echo "$ACCOUNT has no Claude Code CLI; this flow is for Claude Code clients only" >&2; exit 1; }
fi

echo "== 1. Connector for $ACCOUNT"
CONN="$("$GW" connectors </dev/null 2>/dev/null || true)"
mine="$(printf '%s\n' "$CONN" | awk -v a="$ACCOUNT" '$2==a && $0 !~ /DISABLED/ {print $1}')"
n="$(printf '%s\n' "$mine" | grep -c . || true)"
if [[ -n "$SLUG" ]] && ! printf '%s\n' "$mine" | grep -qx "$SLUG"; then
  if printf '%s\n' "$CONN" | awk -v s="$SLUG" '$1==s {f=1} END {exit !f}'; then
    echo "!! slug $SLUG exists but is not an enabled connector of $ACCOUNT; stopping" >&2; exit 1
  fi
  echo "-- adding connector $SLUG -> $ACCOUNT (initial link)"
  run "$GW" add-connector "$SLUG" "$ACCOUNT" anthropic
elif [[ -z "$SLUG" && "$n" -eq 0 ]]; then
  SLUG="${ACCOUNT#ctdc-agent-anthropic-}"; SLUG="${SLUG#ctdc-agent-}"
  echo "-- no connector yet; adding $SLUG -> $ACCOUNT (initial link)"
  run "$GW" add-connector "$SLUG" "$ACCOUNT" anthropic
elif [[ -z "$SLUG" && "$n" -eq 1 ]]; then
  SLUG="$mine"; echo "-- relinking existing connector $SLUG"
elif [[ -z "$SLUG" ]]; then
  echo "!! $ACCOUNT has $n enabled connectors ($(echo $mine)); rerun with --slug to pick one" >&2; exit 1
else
  echo "-- relinking existing connector $SLUG"
fi
MCP_URL="$BASE/mcp/$SLUG"

echo "== 2. $ACCOUNT's Claude Code -> $MCP_URL (local scope)"
run sudo -n -u "$ACCOUNT" -i bash -c "cd ~; claude mcp remove ctdi-dispatch -s project >/dev/null 2>&1 || true; claude mcp remove ctdi-dispatch -s local >/dev/null 2>&1 || true; claude mcp add --transport http --scope local ctdi-dispatch $MCP_URL" </dev/null

echo "== 3. Start the agent's login in the background (log: $LOG)"
if (( DRY )); then
  echo "DRY: sudo -n -u $ACCOUNT -i: sleep 900 | script -qfc 'claude mcp login --no-browser ctdi-dispatch' $LOG"
  echo "DRY: wait for the authorize URL in $LOG, then: $GW link <URL>, then: $GW status $SLUG"
  exit 0
fi
rm -f "$LOG"
sudo -n -u "$ACCOUNT" -i bash -c "cd ~; nohup bash -c 'sleep 900 | script -qfc \"claude mcp login --no-browser ctdi-dispatch\" $LOG' >/dev/null 2>&1 &" </dev/null
URL=""
for _ in $(seq 1 36); do
  URL="$(grep -aoE "https://[A-Za-z0-9.-]+/oauth/authorize\?[A-Za-z0-9%._~&=+-]+" "$LOG" 2>/dev/null | head -1 || true)"
  [[ -n "$URL" ]] && break
  sleep 5
done
clean() { sed -e 's/\x1b\[[0-9;?]*[A-Za-z]//g' -e 's/\x1b\][^\x07]*\x07//g' "$LOG" 2>/dev/null | tr -d '\r' \
            | grep -v '^\s*$' | grep -viE 'oauth/authorize|code_challenge' || true; }
if [[ -z "$URL" ]]; then
  echo "!! No authorize URL after 3 minutes. The login printed:"; clean | sed -n '1,20p'; exit 1
fi
[[ "$URL" == "$BASE/oauth/authorize?"* ]] || { echo "!! the login asked for a different host; stopping" >&2; exit 1; }
echo "-- authorize URL ready (length ${#URL})"

echo "== 4. Link (type ALLOW, then your passphrase)"
"$GW" link "$URL"

echo "== 5. Result"
for _ in $(seq 1 12); do
  grep -aqiE "authenticated|success" "$LOG" 2>/dev/null && break
  sleep 5
done
echo "-- the agent's login printed:"; clean | tail -4
"$GW" status "$SLUG" </dev/null || true
echo "agent-relink.sh: $ACCOUNT -> $SLUG done. Its next Claude session uses the stored login."
