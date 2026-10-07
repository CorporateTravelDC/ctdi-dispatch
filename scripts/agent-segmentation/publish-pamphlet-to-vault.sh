#!/usr/bin/env bash
# scripts/agent-segmentation/publish-pamphlet-to-vault.sh ACCOUNT
#
# For a CLOUD agent identity (2026-10-05: Cowork runs in Anthropic's cloud and
# never reaches its Unix home, so /home/ACCOUNT/CLAUDE.md is unreadable to
# it). Renders the account's onboarding pamphlet, prepends the cloud-client
# rules, runs it through the vault scrub gate and writes it to a STABLE
# research-scope address the agent can read with its board token:
#
#   01-Sources/personal-notes/Series/agents/ACCOUNT/PAMPHLET.md
#   -> GET /api/v1/vault/research?path=01-Sources/personal-notes/Series/agents/ACCOUNT/PAMPHLET.md
#
# Standing convention: every cloud (non-SSH) agent account gets its pamphlet
# published here whenever it is (re)rendered. Run as the operator.
set -euo pipefail
NAME="${1:-}"; [[ "$NAME" =~ ^ctdc-[a-z0-9-]+$ ]] || { echo "usage: $0 ACCOUNT" >&2; exit 64; }
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; REPO="$(cd "$HERE/../.." && pwd)"
tmp=$(mktemp); trap 'rm -f "$tmp"' EXIT
cat >"$tmp" <<EOF
> **Cloud client edition -- read this first.** You run outside the dispatch Pi (Anthropic's
> cloud). You never SSH in and you have no Unix session: the account \`${NAME}\` below is your
> IDENTITY on the platform (attribution, liveness, kill switch), not a place you log in.
> - Your only credential is the board token in your shared settings, sent as \`X-Board-Key\` to
>   \`https://dispatch.example.com\`. One shared copy for every chat and scheduled
>   task; exactly ONE scheduled task refreshes it (\`GET /api/v1/board/refresh\`) every 12 h and
>   writes the new value back; a refresh retires the previous token at once.
> - You can: post to the board, read the board, read the vault research scope
>   (\`GET /api/v1/vault/research?path=...\`, \`.../list?path=...\`). You draft; you never publish.
> - Sections below about your home directory, \`scripts/...\`, secrets subsets, skills and SSH keys
>   describe the account on the box -- they do not apply to you. Everything else (the rules,
>   what you must not do, how you are switched off, councils, never approving) does.
> - This page lives at \`01-Sources/personal-notes/Series/agents/${NAME}/PAMPHLET.md\`; re-read it
>   when the operator says it changed.

EOF
"$HERE/render-onboarding.sh" "$NAME" >>"$tmp"
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
