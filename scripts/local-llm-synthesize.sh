#!/bin/bash
# scripts/local-llm-synthesize.sh
# Reads deterministic facts on stdin, returns a narrative summary on stdout,
# using the LOCAL model server only. No cloud, no API key, no credits.
#
# 2026-09-21, operator directive: the doc-drift checks used to shell out to
# `claude --model fable -p`, which failed on six consecutive runs across three
# different auth/credit failure modes (org subscription disabled, expired
# OAuth token, out of usage credits) and had been silently dead for ~2 weeks.
# The platform's standing posture is local-by-default with cloud as a
# deliberate, explicit, optional offload -- an unattended maintenance check
# quietly depending on frontier credits is the opposite of that.
#
# This is the SYNTHESIS half of a hybrid split: callers gather facts
# deterministically (scripts/check-claude-md-drift.sh, systemctl, podman, git)
# and pass them here purely to be written up. That division matters -- the
# local 4B model is measurably good at turning already-gathered structured
# facts into prose (that is exactly what the 2026-09-21 model bake-off
# measured), and NOT a credible substitute for multi-step agentic tool use,
# which is what the old `claude -p` invocation was really doing.
#
# NEVER fails the caller. If the model is down, suppressed, or slow, the raw
# facts are emitted instead. A drift report with unpolished prose is useful;
# a drift check that dies because the model was restarting is not.
#
# Usage:  <facts on stdin> | local-llm-synthesize.sh "<system prompt>" [max_tokens]
set -uo pipefail

SYSTEM_PROMPT="${1:-Summarize these system facts concisely and factually.}"
MAX_TOKENS="${2:-700}"
ENDPOINT="${LLAMA_ENDPOINT:-http://100.x.x.x:8093/v1/chat/completions}"

# Box-wide long-runner lock, same one weekly-summary/second-brain-weekly take.
# Honoring it here is not optional: the operator directive on
# corporatetraveldc-llama.service is "No 2 long runners can run together
# either EVER". A drift check that grabbed the model mid-brief would violate
# exactly that. -w 300: wait up to 5min for the slot, then give up and fall
# back to raw facts rather than queue behind a long report indefinitely.
LOCK="/var/lib/corporatetraveldc/llama-pool/long-runner-unit.lock"

FACTS="$(cat)"
if [[ -z "${FACTS//[[:space:]]/}" ]]; then
    echo "(no facts supplied to synthesize)"
    exit 0
fi

emit_raw() {
    echo "_Narrative synthesis unavailable (${1}). Raw deterministic findings below._"
    echo
    echo '```'
    printf '%s\n' "${FACTS}"
    echo '```'
}

if ! systemctl --user is-active --quiet corporatetraveldc-llama.service; then
    emit_raw "local model server not running"
    exit 0
fi

mkdir -p "$(dirname "${LOCK}")" 2>/dev/null || true

# jq builds the request so that facts containing quotes, newlines, or braces
# cannot break out of the JSON -- string-concatenating this would be a
# correctness bug the first time a unit name contained an odd character.
REQ=$(jq -n \
    --arg sys "${SYSTEM_PROMPT}" \
    --arg usr "${FACTS}" \
    --argjson maxtok "${MAX_TOKENS}" \
    '{messages: [{role:"system",content:$sys},{role:"user",content:$usr}],
      max_tokens: $maxtok, temperature: 0.3}' 2>/dev/null)

if [[ -z "${REQ}" ]]; then
    emit_raw "could not build request (jq missing or facts unencodable)"
    exit 0
fi

RESP=$(flock -w 300 "${LOCK}" \
    curl -sf --max-time 600 -X POST "${ENDPOINT}" \
        -H "Content-Type: application/json" \
        -d "${REQ}" 2>/dev/null)

if [[ -z "${RESP}" ]]; then
    emit_raw "model call failed, timed out, or slot unavailable"
    exit 0
fi

NARRATIVE=$(printf '%s' "${RESP}" | jq -r '.choices[0].message.content // empty' 2>/dev/null)

if [[ -z "${NARRATIVE//[[:space:]]/}" ]]; then
    emit_raw "model returned an empty response"
    exit 0
fi

printf '%s\n' "${NARRATIVE}"
