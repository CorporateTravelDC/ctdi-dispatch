#!/bin/bash
# scripts/second-brain-weekly-dump.sh
#
# Weekly session-memory consolidation. Shared platform instructions live at
# /srv/ctdc-context/PLATFORM_CONTEXT.md and are NEVER reset by this job.
# Only a verified, retrievable vault archive permits a session-memory reset.
# The existing local-model fallback remains draft-only and cannot clear memory.
set -uo pipefail
# 2026-10-05 (argv-token sweep): a bearer token never goes on a command line --
# /proc/<pid>/cmdline is world-readable here (no hidepid), i.e. readable by
# every team account. authhdr NAME TOKEN puts the header on a private fd and
# sets NAME=(-H @/dev/fd/N) for ONE curl call (re-run it before each call).
authhdr() { local -n _ah="$1"; [[ -n "${_AUTHHDR_FD:-}" ]] && exec {_AUTHHDR_FD}<&-; exec {_AUTHHDR_FD}<<<"Authorization: Bearer $2"; _ah=(-H "@/dev/fd/${_AUTHHDR_FD}"); }

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "${REPO_DIR}"

MEMORY_DIR="/home/corporatetraveldc/.claude/projects/-opt-corporatetraveldc-private-ctdi-dispatch-internal/memory"
DATE_TAG="$(date +%Y-%m-%d)"
LOG_DIR="/var/lib/corporatetraveldc/second-brain-weekly-dump"
LOG="${LOG_DIR}/${DATE_TAG}.log"
mkdir -p "${LOG_DIR}"

ENV_FILE="/etc/corporatetraveldc/dispatch.env"
SECRETS_FILE="/etc/corporatetraveldc/dispatch-secrets.env"

read_env_var() {
    local key="$1" file="$2"
    [[ -f "${file}" ]] || return 0
    grep -m1 "^${key}=" "${file}" 2>/dev/null | cut -d'=' -f2-
}

NTFY_BASE="$(read_env_var NTFY_BASE_URL "${ENV_FILE}")"
NTFY_BASE="${NTFY_BASE:-http://127.0.0.1:2586}"
NTFY_OPS="$(read_env_var NTFY_OPS_TOPIC "${ENV_FILE}")"
NTFY_OPS="${NTFY_OPS:-ops-health}"
NTFY_TOKEN="$(read_env_var NTFY_TOKEN "${SECRETS_FILE}")"
NTFY_TOKEN="${NTFY_TOKEN%%:*}"

ntfy_send() {
    local title="$1" msg="$2" priority="${3:-3}"
    local auth_args=()
    [[ -n "${NTFY_TOKEN}" ]] && authhdr auth_args "${NTFY_TOKEN}"
    curl -sf --max-time 5 \
        "${auth_args[@]}" \
        -H "Title: ${title}" \
        -H "Priority: ${priority}" \
        -H "Tags: brain" \
        -d "${msg}" \
        "${NTFY_BASE}/${NTFY_OPS}" >/dev/null 2>&1
}

PROMPT="Run the weekly session-memory consolidation for the dispatch platform.
Read /srv/ctdc-context/PLATFORM_CONTEXT.md and the two canonical vault
rules notes first. The shared context is instructions, never archival scratch.

1. Read the complete current markdown files under ${MEMORY_DIR}/, including
   MEMORY.md. These are archival input, not authority for operational claims.
2. Synthesize one well-structured second-brain note with real provenance and
   grounded wikilinks; never invent facts or links. Preserve the material's
   uncertainty and dates. Do not create a flood of micro-notes.
3. Write through PYTHONPATH=src python3 -m second_brain.remember --stdin --tags
   weekly-dump,session-archive --author-kind agent. The command environment
   is already loaded verbatim by scripts/with-dispatch-env.sh. Verify both
   successful exit and that the written note is retrievable.
4. ONLY after that confirmed write: reset session memory under ${MEMORY_DIR}/,
   deleting individual topic notes and keeping MEMORY.md as a minimal index
   with the verified archive reference. Never change any other directory.
5. If the write cannot be confirmed, preserve every memory file and report
   the failure. Never infer success from an ambiguous or silent response.

Never reset, rewrite or create repo instruction files, the shared platform
context, its config/platform-context.md source, native loaders or AGENTS.md.
Never commit, stage, push, sign, switch branches, reset or stash as part of this job."

OUTPUT="$(nohup "${REPO_DIR}/scripts/with-dispatch-env.sh" claude --model opus -p "${PROMPT}" --permission-mode acceptEdits \
    --allowedTools "Bash,Read,Write,Edit,Glob,Grep" 2>&1)"
RC=$?

{
    echo "# Second-brain weekly dump -- ${DATE_TAG}"
    echo
    echo "${OUTPUT}"
} >> "${LOG}"

if [[ ${RC} -ne 0 ]] || echo "${OUTPUT}" | grep -qi "could not be confirmed\|failed\|error"; then
    ntfy_send "Second-brain weekly dump needs attention" \
        "Automated Gate-2 dump did not confirm success -- session memory NOT wiped. See ${LOG}" \
        4
    # 2026-09-17: arm the overnight-Llama-fallback marker -- see
    # common/claude_weekly_fallback.py and
    # corporatetraveldc-claude-weekly-llama-fallback.timer/.service. The
    # fallback runner only ever WRITES a best-effort consolidation note via
    # Llama; it never performs this script's destructive CLAUDE.md/memory
    # reset -- that stays gated on a confirmed real Claude/Opus run, same
    # safety posture as this script's own step 3/4 split above.
    PYTHONPATH="${REPO_DIR}/src" python3 - <<'PY' 2>/dev/null || true
from common.claude_weekly_fallback import mark_failure
mark_failure('second-brain-weekly-dump', 'session-memory dump unconfirmed; inspect the operator log')
PY

    exit 1
fi

# Confirmed success -- clear any previously-owed fallback (the common case:
# nothing pending most weeks).
PYTHONPATH="${REPO_DIR}/src" python3 - <<'PY' 2>/dev/null || true
from common.claude_weekly_fallback import clear_marker
clear_marker('second-brain-weekly-dump')
PY

exit 0
