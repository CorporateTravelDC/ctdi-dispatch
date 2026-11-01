#!/usr/bin/env bash
# Local draft: run Codex only as its assigned agent account, in its private clone.
set -euo pipefail
[[ "$(id -un)" == ctdc-agent-openai-codex ]] || { echo 'Use the Codex agent account.' >&2; exit 77; }
agent_repo=/home/ctdc-agent-openai-codex/Documents/Codex/2026-10-30-system-reconciliation/repo
[[ -d "$agent_repo/.git" && ! -L "$agent_repo" ]] || { echo 'Private clone unavailable.' >&2; exit 1; }
[[ "$(stat -c %u "$agent_repo")" == "$(id -u)" ]] || { echo 'Private clone ownership mismatch.' >&2; exit 1; }
cd -- "$agent_repo"
exec "$HOME/.local/bin/codex" --no-daemon --cd "$agent_repo" --sandbox danger-full-access --ask-for-approval on-request "$@"
