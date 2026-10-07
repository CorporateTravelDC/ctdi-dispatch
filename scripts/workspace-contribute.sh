#!/usr/bin/env bash
# scripts/workspace-contribute.sh -- add a draft / finding / fact-check to the
# shared ghostwriting workspace, signed with THIS account's key. Create-only:
# the server picks the path Series/contributions/<task>/<account>/<UTC>-<slug>.md
# and can never overwrite or delete anything. Agents draft; they never publish.
#
#   workspace-contribute.sh [--task general|<task-id>|council-<id>] [--title T] FILE.md
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TASK=general; TITLE=""; FILE=""
while (( $# )); do case "$1" in --task) TASK="$2"; shift ;; --title) TITLE="$2"; shift ;; *) FILE="$1" ;; esac; shift; done
[[ -r "$FILE" ]] || { sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'; exit 64; }
tmp="$(mktemp)"; trap 'rm -f "$tmp"' EXIT
python3 - "$TASK" "${TITLE:-$(basename "$FILE" .md)}" "$FILE" > "$tmp" <<'PYEOF'
import json, sys
task, title, path = sys.argv[1:4]
print(json.dumps({"task": task, "title": title, "content": open(path, encoding="utf-8").read()}))
PYEOF
exec "${REPO_ROOT}/scripts/board-sign.sh" POST /api/v1/workspace/contribute "$tmp" --send
