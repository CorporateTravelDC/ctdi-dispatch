#!/bin/bash
# scripts/second-brain-weekly-dump.sh
#
# Weekly (or on-demand "clean full reset") consolidation: dumps the FULL
# current CLAUDE.md content plus this Claude Code session's own persistent
# memory (~/.claude/projects/<project>/memory/*.md) into ONE richly-written
# second-brain note, then -- ONLY if that write is confirmed to have
# actually succeeded -- resets both CLAUDE.md and the memory files back to
# their minimal scratchpad state.
#
# 2026-08-26, operator directive (Gate 2 of the two-gate documentation
# policy -- see the memory entry this session wrote for the full rationale):
# "the entirety of your persistent memory to be dropped into Second Brain
# with that same context and have it and everything on the Claude MD files
# completely wiped. A fresh run through Second Brain should be run by
# yourself at least once a week, or any time I say we need a clean full
# reset... Going forward... second brain will be the source of data for
# myself, for you, for Ollama, and any other agent that touches the
# platform ever. Claude.md and your own persistent memory are only meant
# to be used as scratch pads."
#
# Model: Opus (operator's choice -- this needs real synthesis of scattered
# facts into a well-structured, semantically-linked note, not a mechanical
# transcription). Same headless-invocation pattern as weekly-doc-drift-
# check.sh / post-commit-doc-verify.sh.
#
# Safety (operator's choice): auto-wipe ONLY on confirmed success. If the
# second-brain write can't be confirmed, nothing is deleted -- an ntfy
# alert fires instead so this needs a human look, same as
# claude-md-drift-daily.sh's failure-notification pattern.
set -uo pipefail

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
    [[ -n "${NTFY_TOKEN}" ]] && auth_args=(-H "Authorization: Bearer ${NTFY_TOKEN}")
    curl -sf --max-time 5 \
        "${auth_args[@]}" \
        -H "Title: ${title}" \
        -H "Priority: ${priority}" \
        -H "Tags: brain" \
        -d "${msg}" \
        "${NTFY_BASE}/${NTFY_OPS}" >/dev/null 2>&1
}

PROMPT="You are running the weekly (or on-demand) Second Brain
consolidation for the CorporateTravelDC dispatch platform repo at
${REPO_DIR}. This is Gate 2 of a two-gate documentation policy the
operator established 2026-08-26 -- read
/home/corporatetraveldc/.claude/projects/-opt-corporatetraveldc-private-ctdi-dispatch-internal/memory/feedback_document_immediately.md
first for the exact rule and rationale before doing anything else.

Your job, in this exact order, with a hard safety rule: DO NOT DELETE OR
RESET ANYTHING until step 3 (verification) has genuinely succeeded. If you
are ever in doubt whether the second-brain write actually landed, STOP at
step 3 and go straight to the failure path at the bottom -- never guess
and wipe anyway.

1. Read the CURRENT, FULL content of CLAUDE.md in this repo, and the
   current, full content of every .md file under
   ${MEMORY_DIR}/ (including MEMORY.md).

2. Synthesize this into ONE well-structured second-brain note (or, only
   if genuinely necessary for size, a small deliberately-limited set of
   clearly cross-linked notes -- not one note per fix/finding; the
   operator was explicit that a flood of micro-notes is the wrong shape).
   The note's actual prose is what the second-brain's semantic layer
   parses for contextual/causal/semantic edges, so give it real
   structure: a heading per distinct topic/fix/finding, [[wikilink]]-style
   references to prior notes or entities where you have genuine grounded
   knowledge of them (never invent a link), and one or more '## Provenance'
   sections (Leaned on / Derived / Reutilized lines) per
   feedback_document_immediately.md's documented format. Timestamp the
   note with today's date. Never fabricate provenance or content beyond
   what CLAUDE.md and the memory files actually say -- this is a
   consolidation of real material, not a creative rewrite.

3. Write the note via:
     PYTHONPATH=src python3 -m second_brain.remember --stdin --tags
     weekly-dump,session-archive --author-kind agent
   (source NEXTCLOUD_ADMIN_USER from /etc/corporatetraveldc/dispatch.env
   first if it's not already in your environment -- the module raises
   RuntimeError without it; NEXTCLOUD_APP_PASSWORD has a file fallback to
   dispatch-secrets.env so you may not need to export it explicitly).
   VERIFY the write actually succeeded -- check the command's exit code
   AND, if the second_brain module offers any way to read back/list the
   just-written note, confirm it is genuinely retrievable. Do not treat a
   silent success or an ambiguous result as confirmed.

4. ONLY if step 3 is confirmed successful: reset CLAUDE.md to a minimal
   scratchpad state (keep just its top-of-file structural rules -- the
   'this file is not a historical record' framing and the absolute
   no-real-secrets rule -- drop all current-task content, since it now
   has a durable home in the note you just wrote), and reset the memory
   directory: delete every individual topic .md file under
   ${MEMORY_DIR}/ and reset MEMORY.md to an empty index (keep the file,
   clear its content to just a one-line header noting the last dump
   date/note reference).

5. If step 3 could NOT be confirmed successful: change nothing (leave
   CLAUDE.md and the memory files exactly as they were), and instead
   write a clear failure summary to stdout explaining exactly what failed
   and why, so the wrapper script's ntfy alert has something specific to
   report.

Do not commit, stage, or push any git changes as part of this. Do not
touch any git branch or run git checkout/reset/stash."

OUTPUT="$(nohup claude --model opus -p "${PROMPT}" --permission-mode acceptEdits \
    --allowedTools "Bash,Read,Write,Edit,Glob,Grep" 2>&1)"
RC=$?

{
    echo "# Second-brain weekly dump -- ${DATE_TAG}"
    echo
    echo "${OUTPUT}"
} >> "${LOG}"

if [[ ${RC} -ne 0 ]] || echo "${OUTPUT}" | grep -qi "could not be confirmed\|failed\|error"; then
    ntfy_send "Second-brain weekly dump needs attention" \
        "Automated Gate-2 dump did not confirm success -- CLAUDE.md/memory NOT wiped. See ${LOG}" \
        4
    exit 1
fi

exit 0
