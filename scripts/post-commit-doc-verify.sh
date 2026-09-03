#!/bin/bash
# post-commit-doc-verify.sh -- fires a live-system documentation drift check
# after a "major" commit or deployment, using Claude Code headless (-p) with
# the Fable model. Companion to the weekly cloud drift-check routine, which
# only sees git history/text -- this is the half that can actually check
# systemctl/podman/curl/SELinux state against what the docs claim.
#
# Install as a git hook:
#   cp scripts/post-commit-doc-verify.sh .git/hooks/post-commit && chmod +x .git/hooks/post-commit
#
# Also called directly from build-models.sh after a successful build (the
# "deployment" trigger, not just "commit").
#
# Runs in the BACKGROUND (disown) so it never blocks a commit or a deploy
# script waiting on a multi-minute Claude Code run.
set -uo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_DIR}"

REASON="${1:-commit}"   # "commit" or "deploy"
DATE_TAG="$(date +%Y-%m-%d)"
LOG="/tmp/post-commit-doc-verify-${DATE_TAG}.log"

# "Major" gate for the commit path -- don't fire on every trivial commit.
# Deploy-triggered calls always run (build-models.sh only calls this on a
# real successful build already).
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

PROMPT="You are checking for documentation drift on the CorporateTravelDC
dispatch platform repo at ${REPO_DIR}, right after a ${REASON} that touched
enough surface area to matter. Do NOT do a from-scratch rewrite -- check
whether anything the CURRENT docs (README.md, CLAUDE.md, everything under
docs/, src/ingest/README.md, src/shared/watchlist_README.md) claim has been
invalidated by this specific change. Verify against the real live system
where relevant (systemctl --user status, podman ps, actual file contents,
curl to local services) rather than trusting old docs.

BEFORE checking, search the second-brain for prior findings on the same
area so you don't re-derive or contradict something already known, using
scripts/second-brain-search.sh. Default mode is an EXACT-PHRASE match
(2026-09-02: hyphenated terms are safe, but a multi-word query only hits
notes containing that literal phrase and silently under-returns) -- so use
a single distinctive word or a phrase you expect verbatim; for multi-word
topic queries use --raw 'term1 AND term2', and --semantic for concept
queries with variant spellings. If this specific drift/area was
already investigated before, say so and build on it rather than starting
cold.

Write findings to a new dated file docs/LIVE_STATE_CHECK_${DATE_TAG}.md --
what you checked, what (if anything) drifted, what's still accurate. If
nothing drifted, say so plainly and briefly rather than padding the file.
If you find a REAL, non-trivial drift or bug (not a false alarm), also
persist it to the second-brain the same way session checkpoints do (see
src/second_brain/remember.py's remember_text()) so it's searchable by
future passes -- this file alone isn't enough, it's read-in-repo not
indexed for search. DO NOT commit or stage anything -- leave changes as
uncommitted working-tree edits only, that is a hard rule with no
exceptions. DO NOT touch any git branch, checkout, or run git commit/add
yourself."

nohup claude --model fable -p "${PROMPT}" --permission-mode acceptEdits \
    --allowedTools "Bash,Read,Write,Edit,Glob,Grep" \
    >> "${LOG}" 2>&1 &
disown

exit 0
