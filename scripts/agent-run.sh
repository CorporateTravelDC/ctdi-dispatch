#!/bin/bash
# scripts/agent-run.sh -- run an interactive agent (claude, codex, ...) inside
# agents.slice so an SSH-started session is resource-isolated from production.
#
# Without this, an SSH session's processes live in session-<id>.scope, a
# sibling of user@1000.service -- i.e. each agent session competes at equal
# weight with ALL of production combined, uncapped (2026-10-03 live case:
# an agent diagnostic pushed load1 to 40 and the thermal guard shed every
# SWIM ingest container). See .config/systemd/user/agents.slice for the
# numbers and docs/AGENT_SEGMENTATION.md for the design.
#
# Usage:
#   scripts/agent-run.sh claude --resume
#   scripts/agent-run.sh codex
#   scripts/agent-run.sh --status            # show the slice's live usage
# To make it the default for a shell, the OPERATOR adds to their own profile:
#   alias claude='/opt/corporatetraveldc/private/ctdi-dispatch-internal/scripts/agent-run.sh claude'
#   alias codex='/opt/corporatetraveldc/private/ctdi-dispatch-internal/scripts/agent-run.sh codex'
# (deliberately not done by this script or any agent -- a shell profile is the
# operator's file).
#
# The transient scope is named agent-<cmd>-<pid> and vanishes when the
# command exits; nothing persists, nothing to clean up. ASCII output only.

set -uo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SELF_DIR}/.." && pwd)"
if ! "${REPO_ROOT}/scripts/verify-manifest.sh" "scripts/agent-run.sh" >/dev/null 2>&1; then
    echo "agent-run: INTEGRITY CHECK FAILED -- refusing to run" >&2
    exit 1
fi

SLICE="agents.slice"

if [[ "${1:-}" == "--status" ]]; then
    systemctl --user show "${SLICE}" -p Id,ActiveState,CPUWeight,CPUQuotaPerSecUSec,MemoryCurrent,MemoryHigh,MemoryMax,TasksCurrent,TasksMax 2>/dev/null
    echo "--- scopes/units currently in the slice ---"
    systemctl --user list-units --no-legend "agent-*" 2>/dev/null
    systemctl --user list-units --no-legend 2>/dev/null | awk '{print $1}' | while read -r u; do
        [[ "$(systemctl --user show "$u" -p Slice --value 2>/dev/null)" == "${SLICE}" ]] && echo "  $u"
    done
    exit 0
fi

[[ $# -ge 1 ]] || { echo "usage: $0 <command> [args...]   |   $0 --status" >&2; exit 2; }
command -v "$1" >/dev/null 2>&1 || { echo "agent-run: command not found: $1" >&2; exit 127; }

if ! systemctl --user show "${SLICE}" -p LoadState --value 2>/dev/null | grep -q loaded; then
    echo "agent-run: ${SLICE} is not loaded -- install .config/systemd/user/agents.slice and daemon-reload" >&2
    exit 3
fi

# --scope keeps the command attached to THIS terminal (interactive agents need
# a tty); --collect drops the scope record on exit; the unit name makes it
# findable in --status and in the guard's view of who is using what.
exec systemd-run --user --scope --quiet --collect \
    --slice="${SLICE}" \
    --unit="agent-$(basename "$1")-$$" \
    --description="agent session: $* (pid $$, $(date '+%Y-%m-%d %H:%M'))" \
    -- "$@"
