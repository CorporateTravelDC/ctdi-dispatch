#!/bin/bash
# post-commit-doc-verify.sh -- fires a live-system documentation drift check
# after a "major" commit or deployment. Companion to weekly-doc-drift-check.sh.
#
# Install as a git hook:
#   cp scripts/post-commit-doc-verify.sh .git/hooks/post-commit && chmod +x .git/hooks/post-commit
#
# Also called from build-models.sh after a successful build (the "deployment"
# trigger, not just "commit").
#
# Runs in the BACKGROUND so it never blocks a commit or a deploy script.
#
# 2026-09-21 REWRITE -- hybrid, fully local, same change as its weekly
# sibling. This previously called `claude --model fable -p` with an agentic
# toolset and had been failing on every invocation (confirmed in
# /tmp/post-commit-doc-verify-*.log: "out of usage credits"), meaning every
# commit for ~2 weeks silently produced nothing.
#
# Detection is deterministic; the local model only writes the summary. Two
# capabilities the old agentic version had are deliberately NOT reproduced,
# because a 4B local model cannot do them honestly and pretending otherwise
# would be worse than dropping them:
#   * autonomous investigation of WHY something drifted (it could read files,
#     curl services, and form a hypothesis -- this cannot)
#   * conditionally persisting real findings into the second brain
# What remains is: mechanical drift detection, a second-brain lookup for
# prior findings on the touched area, and a written summary. If the deeper
# investigation is wanted for a specific commit, run it by hand.
set -uo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_DIR}" || exit 0

REASON="${1:-commit}"   # "commit" or "deploy"
DATE_TAG="$(date +%Y-%m-%d)"
LOG="/tmp/post-commit-doc-verify-${DATE_TAG}.log"

# "Major" gate -- don't fire on every trivial commit. Unchanged from the
# previous version: this part was always deterministic and always worked.
is_major() {
    [ "${REASON}" = "deploy" ] && return 0
    local changed
    changed="$(git diff --name-only HEAD~1 HEAD 2>/dev/null || true)"
    [ -z "${changed}" ] && return 1
    local count
    count="$(printf '%s\n' "${changed}" | grep -c .)"
    [ "${count}" -gt 5 ] && return 0
    printf '%s\n' "${changed}" | grep -qE '^(src/|scripts/|docs/|Containerfile|build-models\.sh|.*\.service$|.*\.container$|.*\.timer$|corporatetraveldc\.[a-z-]+$)' && return 0
    return 1
}

if ! is_major; then
    exit 0
fi

# Everything below runs detached so a commit never waits on it. The local
# model is slow (~5 tok/s on this hardware) and this can take several
# minutes; blocking a git commit on that would be unacceptable.
(
    exec >>"${LOG}" 2>&1
    echo "=== post-commit-doc-verify (${REASON}) $(date '+%F %H:%M:%S %Z') ==="

    CHANGED="$(git diff --name-only HEAD~1 HEAD 2>/dev/null || true)"

    FACTS_FILE="$(mktemp)"
    trap 'rm -f "${FACTS_FILE}"' EXIT

    {
        echo "## Trigger"
        echo "reason=${REASON}  commit=$(git log -1 --format='%h %s' 2>/dev/null)"

        echo
        echo "## Files changed in this commit"
        printf '%s\n' "${CHANGED}"

        echo
        echo "## Deterministic drift checks"
        bash scripts/check-claude-md-drift.sh 2>&1 || true

        echo
        echo "## Failed / crash-looping units"
        systemctl --user list-units 'corporatetraveldc-*' --all --plain --no-legend \
            --state=failed,auto-restart --no-pager 2>/dev/null | awk '{print $1}' || true
        echo "(empty above means none)"

        # Prior second-brain findings on the touched area. Exact-phrase is the
        # default mode and silently under-returns on multi-word queries (see
        # scripts/second-brain-search.sh), so query ONE distinctive token --
        # the basename of the most-changed path -- rather than a phrase.
        echo
        echo "## Prior second-brain findings on the touched area"
        TOPIC="$(printf '%s\n' "${CHANGED}" | head -1 | xargs -r basename 2>/dev/null | sed 's/\.[a-z]*$//')"
        if [[ -n "${TOPIC}" ]]; then
            echo "query: ${TOPIC}"
            timeout 60 bash scripts/second-brain-search.sh "${TOPIC}" 2>/dev/null | head -20 || echo "(search unavailable)"
        else
            echo "(no topic derived)"
        fi
    } >"${FACTS_FILE}"

    SYSTEM_PROMPT="You are documenting a post-commit drift check on a dispatch \
platform. You are given the files a commit changed plus deterministic check \
output. Write a SHORT markdown summary: did this commit invalidate anything \
the docs claim, and is anything failing now. Name files and units exactly as \
given. If nothing drifted, say so in one line and stop -- do not pad. Never \
invent a cause for a failure that is not stated in the facts."

    NARRATIVE="$(./scripts/local-llm-synthesize.sh "${SYSTEM_PROMPT}" 500 <"${FACTS_FILE}")"

    REPORT="docs/LIVE_STATE_CHECK_${DATE_TAG}.md"
    {
        echo
        echo "## Post-commit check — $(date '+%H:%M %Z') (${REASON})"
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

    echo "appended to ${REPORT}"
) &
disown

exit 0
