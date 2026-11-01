#!/usr/bin/env bash
# scripts/agent-segmentation/publish-pamphlet-to-vault.sh ACCOUNT
#
# Initially for a cloud identity (2026-10-05: Cowork runs in Anthropic's cloud and
# never reaches its Unix home, so /home/ACCOUNT/CLAUDE.md is unreadable to
# it). Renders the account's onboarding pamphlet, prepends the cloud-client
# rules, runs it through the vault scrub gate and writes it to a STABLE
# research-scope address the agent reads with its connector's research_read
# tool (parity audit 2026-10-15: cloud agents use the agent gateway's MCP
# connector -- src/web/routes/agent_gateway.py TOOLS -- not an X-Board-Key
# board token; the cloud-client text below said X-Board-Key + a 12 h
# /api/v1/board/refresh task):
#
#   01-Sources/personal-notes/Series/agents/ACCOUNT/PAMPHLET.md
#   -> research_read 01-Sources/personal-notes/Series/agents/ACCOUNT/PAMPHLET.md
#
# Publish a registry-derived edition for an agent or service identity.
# --render-only is a preview: no env files loaded and no vault writes.
# Run publication as the operator, never as an agent.
set -euo pipefail
NAME="${1:-}"; MODE="${2:-}"
[[ "$NAME" =~ ^ctdc-[a-z0-9-]+$ && ( -z "$MODE" || "$MODE" == --render-only ) && $# -le 2 ]] || { echo "usage: $0 ACCOUNT [--render-only]" >&2; exit 64; }
REGISTRY="${CTDC_ACCOUNTS_FILE:-/etc/ctdc-accounts.conf}"
[[ -r "$REGISTRY" ]] || { echo "account registry unreadable; nothing published" >&2; exit 66; }
ROW=$(awk -v n="$NAME" '$1==n {print $2, $3, $4; count++} END {if(count != 1) exit 1}' "$REGISTRY") || { echo "account must have exactly one registry row; nothing published" >&2; exit 65; }
read -r ACCOUNT_KIND LOGIN_MODE PRELOADED <<<"$ROW"
case "$ACCOUNT_KIND:$LOGIN_MODE" in
  agent:claude|agent:codex|agent:ssh) CLIENT_CONTEXT="on-box account, login mode ${LOGIN_MODE}; a remote client may attach to its own Unix session" ;;
  service:none) CLIENT_CONTEXT="service identity, no interactive login; its integration may run locally or in a cloud client" ;;
  *) echo "unsupported account kind/login mode; nothing published" >&2; exit 65 ;;
esac
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; REPO="$(cd "$HERE/../.." && pwd)"
tmp=$(mktemp); trap 'rm -f "$tmp"' EXIT
cat >"$tmp" <<EOF
> **Account-specific connector edition -- read this first.** Account \`${NAME}\`:
> ${CLIENT_CONTEXT}. This page does not determine where the current client runs.
> Use actual session tools and the account registry; do not assume a cloud client
> has shell access, or that an on-box Codex session lacks one.
> - When available, use only your OWN MCP connector (the agent gateway,
>   \`/mcp/<your connector>\`), linked by OAuth with operator-signed consent.
>   Never substitute another account's connector or an X-Board-Key board token.
> - The gateway catalog currently exposes \`status\`, \`board_read\`, \`board_post\`,
>   \`research_list\`, \`research_read\`, \`workspace_contribute\`, \`council_request\`,
>   \`skill_read\`. Reading a skill grants no execution capability or API action;
>   this catalog has no watchlist action. Check the current tool list before claiming otherwise.
> - On-box personal action tokens, if separately granted, live in the account's own
>   \`~/.config/ctdc/api-token\`; the env loader does not load that file. A cloud
>   client cannot assume it can read this path. Missing capability means report it,
>   never seek the shared administrator token. See \`docs/PER_AGENT_API_ACTIONS.md\`.
> - You draft; you never publish or approve. Home paths below apply only when your
>   current session actually has that account's authorized on-box filesystem access.
> - Stable address: \`01-Sources/personal-notes/Series/agents/${NAME}/PAMPHLET.md\`;
>   re-read it when the operator says it changed.

EOF
# Derive the deployment text from the account registry, not a fixed cloud list.
# A render-only preview never loads credentials or contacts the vault.
env -u DEPLOYMENT_PROFILE_OVERRIDE CTDC_ACCOUNTS_FILE="$REGISTRY" \
  bash "$HERE/render-onboarding.sh" "$NAME" >>"$tmp"
if [[ "$MODE" == --render-only ]]; then
  cat "$tmp"
  exit 0
fi
DEST="01-Sources/personal-notes/Series/agents/${NAME}/PAMPHLET.md"
SRC="$tmp" DEST="$DEST" NEXTCLOUD_ADMIN_USER="${NEXTCLOUD_ADMIN_USER:-corporatetraveldc}" \
  "$REPO/scripts/with-dispatch-env.sh" env PYTHONPATH="$REPO/src" python3 - <<'PYEOF'
import os
from second_brain import webdav_client
from second_brain.scrub_gate import gate
text = open(os.environ["SRC"]).read()
gate(text, source="agent-pamphlet")          # raises on CUI/PII -- nothing is written
webdav_client.put(f"{webdav_client.BUSINESS_ROOT}/{os.environ['DEST']}", text)
print(f"published {os.environ['DEST']} ({len(text.splitlines())} lines)")
PYEOF
