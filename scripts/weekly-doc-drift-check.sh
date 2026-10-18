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
#
# 2026-10-16 (operator): the 09-21 rewrite dropped the one thing the weekly
# job was for -- PROSE docs against the live system; it only re-ran the
# CLAUDE.md checker and listed units/containers. The "Doc claims" section
# below is deterministic: scripts/doc-claims-check.py extracts every repo
# path, unit name and documented timer schedule from README.md, docs/*.md
# (non-dated) and skills/*/SKILL.md and verifies each against git ls-files,
# systemctl list-unit-files and the installed timer's OnCalendar /
# OnUnitActiveSec. Misses are [DRIFT] lines in the report and the ntfy
# summary carries the count. Narrative stays optional (local model only).
set -uo pipefail
# A bearer token never goes on a command line (/proc/<pid>/cmdline is readable
# by every team account): authhdr NAME TOKEN puts the header on a private fd
# and sets NAME=(-H @/dev/fd/N) for ONE curl call. Same helper as
# scripts/claude-md-drift-daily.sh.
authhdr() { local -n _ah="$1"; [[ -n "${_AUTHHDR_FD:-}" ]] && exec {_AUTHHDR_FD}<&-; exec {_AUTHHDR_FD}<<<"Authorization: Bearer $2"; _ah=(-H "@/dev/fd/${_AUTHHDR_FD}"); }

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_DIR}" || exit 1

ENV_FILE="/etc/corporatetraveldc/dispatch.env"
SECRETS_FILE="/etc/corporatetraveldc/dispatch-secrets.env"
read_env_var() {
    local key="$1" file="$2"
    [[ -f "${file}" ]] || return 0
    grep -m1 "^${key}=" "${file}" 2>/dev/null | cut -d'=' -f2-
}
NTFY_BASE="$(read_env_var NTFY_BASE_URL "${ENV_FILE}")"; NTFY_BASE="${NTFY_BASE:-http://127.0.0.1:2586}"
NTFY_OPS="$(read_env_var NTFY_OPS_TOPIC "${ENV_FILE}")";  NTFY_OPS="${NTFY_OPS:-ops-health}"
NTFY_TOKEN="$(read_env_var NTFY_TOKEN "${SECRETS_FILE}")"; NTFY_TOKEN="${NTFY_TOKEN%%:*}"
ntfy_send() {
    local title="$1" msg="$2" priority="${3:-3}"
    local auth_args=()
    [[ -n "${NTFY_TOKEN}" ]] && authhdr auth_args "${NTFY_TOKEN}"
    curl -sf --max-time 5 "${auth_args[@]}" \
        -H "Title: ${title}" -H "Priority: ${priority}" -H "Tags: memo" \
        -d "${msg}" "${NTFY_BASE}/${NTFY_OPS}" >/dev/null 2>&1
}

DATE_TAG="$(date +%Y-%m-%d)"
LOG_DIR="/var/lib/corporatetraveldc/docs-drift-check"
LOG="${LOG_DIR}/ctdi-dispatch-internal-${DATE_TAG}.log"
# 2026-10-12: reports live OUTSIDE the repo -- a timer writing into the tracked tree made every relay
# move files aside and failed one at its clean-tree check. Committed records stay where they are.
REPORTS_DIR="${CTDC_REPORTS_DIR:-/var/lib/corporatetraveldc/reports/docs}"; mkdir -p "${REPORTS_DIR}"
REPORT="${REPORTS_DIR}/LIVE_STATE_CHECK_${DATE_TAG}.md"
mkdir -p "${LOG_DIR}"

exec >>"${LOG}" 2>&1
echo "=== weekly-doc-drift-check ${DATE_TAG} $(date '+%H:%M:%S %Z') ==="

# ── Facts: deterministic, no model ───────────────────────────────────────────
FACTS_FILE="$(mktemp)"
CLAIMS_FILE="$(mktemp)"
trap 'rm -f "${FACTS_FILE}" "${CLAIMS_FILE}"' EXIT

# Doc claims vs live system (2026-10-16): deterministic, stdlib python.
# Captured to its own file first so the miss count can go into the ntfy
# summary; exit 1 = misses found, which is a finding, not a failure.
python3 scripts/doc-claims-check.py >"${CLAIMS_FILE}" 2>&1 || true
CLAIM_MISSES="$(grep -c '^\[DRIFT\]' "${CLAIMS_FILE}" || true)"
CLAIM_SUMMARY="$(grep '^\[SUMMARY\]' "${CLAIMS_FILE}" || echo '[SUMMARY] doc-claims-check did not run')"

{
    echo "## Deterministic drift checks (scripts/check-claude-md-drift.sh)"
    # Not --pre-sign: that mode suppresses [OK] lines and skips the
    # manifest-vs-signature check, both of which we want recorded here.
    bash scripts/check-claude-md-drift.sh 2>&1 || true

    echo
    echo "## Doc claims vs live system (scripts/doc-claims-check.py)"
    echo "README.md, docs/*.md (non-dated), skills/*/SKILL.md: repo paths vs git ls-files,"
    echo "unit names vs systemctl list-unit-files, documented timer schedules vs the installed"
    echo "timer's OnCalendar/OnUnitActiveSec. Struck text is skipped. One [DRIFT] line per miss."
    cat "${CLAIMS_FILE}"

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
    # previous report = newest earlier LIVE_STATE_CHECK in the reports dir (or, before 2026-10-12, in git)
    PREV_FILE="$(ls -1t "${REPORTS_DIR}"/LIVE_STATE_CHECK_*.md 2>/dev/null | grep -v "_${DATE_TAG}" | head -1)"
    PREV=""
    if [[ -n "${PREV_FILE}" ]]; then
        git log --oneline --since="@$(stat -c %Y "${PREV_FILE}")" 2>/dev/null | head -40 || true
    elif PREV="$(git log -1 --format=%H -- 'docs/LIVE_STATE_CHECK_*.md' 2>/dev/null)" && [[ -n "${PREV}" ]]; then
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

# Optional: the local model may be down or shedding; the report lands either way.
NARRATIVE="$(./scripts/local-llm-synthesize.sh "${SYSTEM_PROMPT}" 900 <"${FACTS_FILE}" 2>/dev/null || true)"
[[ -n "${NARRATIVE}" ]] || NARRATIVE="_(no narrative: local model unavailable; the raw findings below are the report)_"

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
echo "${CLAIM_SUMMARY}"

# ntfy summary (ops-health, p3 with misses, p2 clean): the miss count is the
# headline; the report path is where the [DRIFT] lines are.
CLAUDE_MD_DRIFT="$(grep -c '^\[DRIFT\]' "${FACTS_FILE}" || true)"
CLAUDE_MD_DRIFT=$(( CLAUDE_MD_DRIFT - CLAIM_MISSES ))
if [[ "${CLAIM_MISSES}" -gt 0 || "${CLAUDE_MD_DRIFT}" -gt 0 ]]; then
    ntfy_send "Weekly doc drift: ${CLAIM_MISSES} doc-claim miss(es)" \
        "${CLAIM_SUMMARY#\[SUMMARY\] }; CLAUDE.md drift lines: ${CLAUDE_MD_DRIFT} -- see ${REPORT}" 3
else
    ntfy_send "Weekly doc drift: clean" "${CLAIM_SUMMARY#\[SUMMARY\] } -- ${REPORT}" 2
fi
# Deliberately does NOT commit. Same rule the previous version carried: this
# runs unattended, and an unattended process must never write to git history.
