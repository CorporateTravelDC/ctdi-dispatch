#!/bin/bash
# weekly-doc-drift-check.sh -- time-triggered (not commit-triggered) sibling
# of post-commit-doc-verify.sh. Runs weekly regardless of whether anything was
# committed, with full live-system access (systemctl/podman/git) since it runs
# on the box itself.
#
# 2026-09-21 REWRITE -- hybrid, fully local. This used to shell out to
# `claude --model fable -p` with an agentic toolset. That failed SIX
# consecutive runs across three distinct failure modes -- org subscription
# disabled (Sep 7, 8), expired OAuth token (Sep 14), out of usage credits
# (Sep 17, 21) -- and had been silently dead for ~2 weeks while its
# OnFailure= notifications fired into ops-health unheeded.
#
# The split now is deliberate:
#   FACTS     gathered deterministically, no model involved. Mechanical
#             drift is better checked mechanically -- scripts/check-claude-md-drift.sh
#             already does 11 such checks and is rigorous enough to block
#             manifest signing, which is a far stronger guarantee than asking
#             a model to notice something.
#   NARRATIVE written by the LOCAL model via scripts/local-llm-synthesize.sh.
#             Turning already-gathered structured facts into prose is exactly
#             what the 2026-09-21 bake-off measured Qwen3-4B to be good at.
#
# The model is therefore never in the detection path -- only the write-up. If
# it is down, the report still lands with raw findings. That is the whole
# point: a maintenance check must not depend on an LLM being available, let
# alone on frontier credits.
set -uo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_DIR}" || exit 1

DATE_TAG="$(date +%Y-%m-%d)"
LOG_DIR="/var/lib/corporatetraveldc/docs-drift-check"
LOG="${LOG_DIR}/ctdi-dispatch-internal-${DATE_TAG}.log"
REPORT="docs/LIVE_STATE_CHECK_${DATE_TAG}.md"
mkdir -p "${LOG_DIR}"

exec >>"${LOG}" 2>&1
echo "=== weekly-doc-drift-check ${DATE_TAG} $(date '+%H:%M:%S %Z') ==="

# ── Facts: deterministic, no model ───────────────────────────────────────────
FACTS_FILE="$(mktemp)"
trap 'rm -f "${FACTS_FILE}"' EXIT

{
    echo "## Deterministic drift checks (scripts/check-claude-md-drift.sh)"
    # Not --pre-sign: that mode suppresses [OK] lines and skips the
    # manifest-vs-signature check, both of which we want recorded here.
    bash scripts/check-claude-md-drift.sh 2>&1 || true

    echo
    echo "## Failed / crash-looping units"
    systemctl --user list-units 'corporatetraveldc-*' --all --plain --no-legend \
        --state=failed,auto-restart --no-pager 2>/dev/null | awk '{print $1}' || true
    echo "(empty above means none)"

    echo
    echo "## Running containers"
    podman ps --format '{{.Names}}\t{{.Status}}' 2>/dev/null || true

    echo
    echo "## Commits since the previous LIVE_STATE_CHECK"
    PREV="$(git log -1 --format=%H -- 'docs/LIVE_STATE_CHECK_*.md' 2>/dev/null)"
    if [[ -n "${PREV}" ]]; then
        git log --oneline "${PREV}..HEAD" 2>/dev/null | head -40 || true
    else
        git log --oneline -20 2>/dev/null || true
    fi

    echo
    echo "## Load / thermal (most recent samples)"
    tail -3 /var/lib/corporatetraveldc/ollama-keepwarm/thermal-samples.csv 2>/dev/null || true
} >"${FACTS_FILE}"

# ── Narrative: local model only ──────────────────────────────────────────────
SYSTEM_PROMPT="You are documenting the live state of a dispatch platform \
running on a Raspberry Pi 5. You are given deterministic check output. Write a \
short markdown report: what drifted, what is failing, what changed since the \
last check. Be specific and name units and files exactly as given. State only \
what the facts support -- if something is not in the facts, do not mention it, \
and never invent a cause for a failure."

NARRATIVE="$(./scripts/local-llm-synthesize.sh "${SYSTEM_PROMPT}" 900 <"${FACTS_FILE}")"

# APPEND, never truncate. post-commit-doc-verify.sh writes into this same
# dated file, and the two genuinely collide: the weekly timer and a major
# commit can land on the same day (they did on 2026-09-21, first run). An
# earlier version of this used `>` and would have silently erased the
# post-commit section. The title line is written only when creating the file,
# so whichever check runs first owns the header and the other appends under it.
if [[ ! -f "${REPORT}" ]]; then
    {
        echo "# Live State Check — ${DATE_TAG}"
        echo
        echo "_Findings are gathered deterministically; narratives are written"
        echo "by the local model (no cloud, no credits) and summarize those"
        echo "findings only — the raw facts are authoritative._"
    } >"${REPORT}"
fi

{
    echo
    echo "## Weekly drift check — $(date '+%H:%M %Z')"
    echo
    echo "${NARRATIVE}"
    echo
    echo "<details><summary>Raw deterministic findings</summary>"
    echo
    echo '```'
    cat "${FACTS_FILE}"
    echo '```'
    echo
    echo "</details>"
} >>"${REPORT}"

echo "wrote ${REPORT}"
# Deliberately does NOT commit. Same rule the previous version carried: this
# runs unattended, and an unattended process must never write to git history.
