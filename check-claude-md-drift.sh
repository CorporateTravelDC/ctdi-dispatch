#!/usr/bin/env bash
# scripts/check-claude-md-drift.sh
#
# Compares CLAUDE.md's claims against the live system and emits an EDIT LIST,
# not prose. Read-only: reads files, systemd unit state, and the local API.
# Nothing is written, restarted, or modified.
#
# Exit 0 = no drift. Exit 1 = drift found (list on stdout).
#
# Wiring:
#   1. commit boundary (primary) -- called from scripts/sign-manifest.sh,
#      with --pre-sign, before it generates the manifest, so a change pass
#      cannot complete with CLAUDE.md stale.
#   2. daily backstop -- corporatetraveldc-claude-md-drift-daily, full check
#      (no --pre-sign), catching drift from outside a signing pass (live
#      deploys, hand-edited units).
#   3. weekly doc-drift-weekly (Fable, broader prose check, unrelated tool)
#      is NOT this checker and does not call it.
#
# --pre-sign skips check 8 (manifest vs signature). sign-manifest.sh is the
# thing that brings MANIFEST.sha256/.asc back in sync -- calling this
# checker from inside it, before it has regenerated either file, would
# otherwise flag the exact stale-signature state a signing run exists to
# fix and block every pass. The daily/standalone run has no such exemption:
# it runs the full set including check 8.
#
# Usage: scripts/check-claude-md-drift.sh [--quiet] [--pre-sign]

set -uo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "${REPO_DIR}"
DOC="CLAUDE.md"
API="${DISPATCH_API:-http://127.0.0.1:8000}"
DRIFT=0
QUIET=0
PRE_SIGN=0
for arg in "$@"; do
    case "${arg}" in
        --quiet) QUIET=1 ;;
        --pre-sign) PRE_SIGN=1 ;;
    esac
done

drift() { DRIFT=1; echo "[DRIFT] $*"; }
ok()    { [[ ${QUIET} -eq 1 ]] || echo "[OK] $*"; }
warn()  { echo "[WARN] $*"; }

[[ -f "${DOC}" ]] || { echo "[FAIL] ${DOC} not found in ${REPO_DIR}"; exit 1; }

# -- 1. retired terms still described as live -------------------------------
# Add a term here the same day you retire the thing it names, then remove it
# once CLAUDE.md is confirmed to only mention it in past-tense/retirement
# context (this is a plain substring grep -- it cannot tell "X is live" from
# "X was retired", so it's a short-lived tripwire, not a permanent guard).
# mcpo / MCP bridge / gemma / dispatch-persona removed 2026-08-19: verified
# by hand that every remaining mention in CLAUDE.md documents their removal
# (Ollama model-inventory section, Known bad section), not current live use.
RETIRED=(
  "ops.example.com"
)
for t in "${RETIRED[@]}"; do
    if grep -qiF -- "${t}" "${DOC}"; then
        drift "${DOC} still mentions retired item: ${t} -- line(s): $(grep -inF -- "${t}" "${DOC}" | cut -d: -f1 | tr '\n' ',' | sed 's/,$//')"
    fi
done
[[ ${DRIFT} -eq 0 ]] && ok "no retired terms in ${DOC}"

# -- 2. hardcoded unit counts ----------------------------------------------
if grep -qE '[0-9]{2,4} loaded units|[0-9]{2,4} (containers|units|quadlets)' "${DOC}"; then
    drift "${DOC} hardcodes a unit/container count -- these go stale weekly; cite the command instead"
else
    ok "no hardcoded unit counts"
fi

# -- 3. model inventory -----------------------------------------------------
# "N dedicated models" means N wired into build-models.sh's MODELS map, not
# raw `corporatetraveldc.*` Modelfiles on disk -- a staged/proposed Modelfile
# (e.g. a `.PROPOSED-...` draft awaiting operator review) is real drift for
# the scrub-coverage check (section 4) but is not yet a "model", so counting
# it here would false-positive on every legitimate staged draft.
if [[ -f "build-models.sh" ]]; then
    MF_COUNT=$(sed -n '/declare -A MODELS=(/,/^)/p' build-models.sh | grep -cE '^\s*\[corporatetraveldc-pi5-')
else
    MF_COUNT=$(ls -1 corporatetraveldc.* 2>/dev/null | grep -vcE '\.(bak|example|template)' || echo 0)
fi
DOC_COUNT=$(grep -oE '[0-9]+ dedicated models' "${DOC}" | grep -oE '[0-9]+' | head -1)
if [[ -n "${DOC_COUNT}" && "${DOC_COUNT}" != "${MF_COUNT}" ]]; then
    drift "model count: ${DOC} says ${DOC_COUNT}, build-models.sh wires up ${MF_COUNT}"
else
    ok "model count matches (${MF_COUNT})"
fi

BASES=$(grep -h '^FROM' corporatetraveldc.* 2>/dev/null | awk '{print $2}' | sort -u | tr '\n' ' ')
BASE_N=$(echo "${BASES}" | wc -w)
if [[ ${BASE_N} -gt 1 ]]; then
    warn "mixed model bases in tree: ${BASES}-- confirm ${DOC} describes the split correctly"
else
    ok "single model base: ${BASES}"
fi

# -- 4. Modelfile scrub coverage (EXPOSURE CHECK) ---------------------------
# Every corporatetraveldc.* Modelfile carries the operator persona. Coverage
# is satisfied either by an exact-basename entry in DROP_FILES, or by the
# pattern rule in scrub_tree() that drops any blob whose basename starts
# with "corporatetraveldc." (added 2026-08-18 to stop variant names from
# silently bypassing the enumerated list -- see the EXPOSURE finding that
# motivated it). Only fall back to per-file DROP_FILES membership if that
# pattern rule isn't present.
SCRUB="scripts/scrub-public-tree.py"
if [[ -f "${SCRUB}" ]]; then
    if grep -qF 'name.startswith("corporatetraveldc.")' "${SCRUB}"; then
        ok "all Modelfiles covered by scrub_tree()'s corporatetraveldc. pattern rule"
    else
        UNCOVERED=0
        while IFS= read -r mf; do
            [[ -z "${mf}" ]] && continue
            grep -qF "\"${mf}\"" "${SCRUB}" || { drift "EXPOSURE: ${mf} is not in DROP_FILES -- publishes on next push-public.sh"; UNCOVERED=1; }
        done < <(ls -1 corporatetraveldc.* 2>/dev/null | grep -vE '\.(bak|example|template)')
        [[ ${UNCOVERED} -eq 0 ]] && ok "all Modelfiles covered by DROP_FILES"
    fi
else
    warn "${SCRUB} not found -- scrub coverage unchecked"
fi

# -- 5. failed units vs the Known bad section -------------------------------
# 2026-08-26: was `awk '$0 ~ /failed|auto-restart/ {print $2}'` over the
# unfiltered --all listing -- matched the literal word anywhere on the
# line, including inside a unit's own DESCRIPTION text (e.g.
# corporatetraveldc-unit-failure-notify@... whose description is "ntfy
# alert for a failed weekly unit"), and $2 is only really the unit name
# when the line has no leading bullet and a short-enough unit name for
# awk's default field splitting to line up -- false positives printed
# "loaded" as the unit. --state= filters server-side on the real ACTIVE/
# SUB state, --plain drops the bullet, so $1 is always the actual unit.
FAILED=$(systemctl --user list-units 'corporatetraveldc-*' --all --plain --no-legend \
             --state=failed,auto-restart --no-pager 2>/dev/null \
         | awk '{print $1}' | sed 's/\.service$//')
if [[ -n "${FAILED}" ]]; then
    while IFS= read -r u; do
        [[ -z "${u}" ]] && continue
        grep -qF "${u}" "${DOC}" || drift "unit ${u} is failed/crash-looping and is absent from ${DOC}'s Known bad section"
    done <<< "${FAILED}"
else
    ok "no failed or crash-looping units"
fi

# -- 6. Known bad staleness -------------------------------------------------
KB_DATE=$(grep -oE '## Known bad \(as of ([0-9]{4}-[0-9]{2}-[0-9]{2})\)' "${DOC}" | grep -oE '[0-9]{4}-[0-9]{2}-[0-9]{2}')
if [[ -n "${KB_DATE}" ]]; then
    AGE=$(( ( $(date +%s) - $(date -d "${KB_DATE}" +%s) ) / 86400 ))
    if [[ ${AGE} -gt 7 ]]; then
        drift "Known bad section is ${AGE} days old (${KB_DATE}) -- re-verify or restamp"
    else
        ok "Known bad section is ${AGE} days old"
    fi
else
    warn "${DOC} has no dated Known bad section"
fi

# -- 7. paths cited by the doc still exist ----------------------------------
while IFS= read -r p; do
    [[ -e "${p}" ]] || drift "path cited in ${DOC} does not exist: ${p}"
done < <(grep -oE '`(/(opt|etc|var|run)/[a-zA-Z0-9._/-]+)`' "${DOC}" | tr -d '`' | sort -u)

# -- 8. manifest signature freshness ----------------------------------------
if [[ ${PRE_SIGN} -eq 1 ]]; then
    warn "check 8 (manifest vs signature) skipped -- --pre-sign, sign-manifest.sh is about to regenerate both"
elif git diff --quiet MANIFEST.sha256 MANIFEST.sha256.asc 2>/dev/null; then
    ok "manifest and signature both clean"
else
    if git diff --quiet MANIFEST.sha256 2>/dev/null && ! git diff --quiet MANIFEST.sha256.asc 2>/dev/null; then
        drift "MANIFEST.sha256.asc modified but MANIFEST.sha256 is not -- signature no longer covers the manifest; re-run scripts/sign-manifest.sh"
    fi
fi

# -- 9. API reachable (routes are never transcribed into the doc) -----------
CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "${API}/healthz" 2>/dev/null)
if [[ "${CODE}" == "200" ]]; then
    ok "API healthy at ${API}"
else
    warn "API at ${API} returned ${CODE:-no response} -- live checks limited"
fi

# -- 10. installed git hooks match their tracked source (2026-08-26, Opus
# blind review C-34) -- .git/hooks/* is never tracked by git itself, so a
# hook edited in scripts/ (pre-commit, pre-push, post-commit-doc-verify.sh)
# silently stops being what's actually installed and enforced unless
# someone remembers to re-copy it by hand. Confirmed live: post-commit had
# drifted this same way before this check existed. Runs on every check
# (not just --pre-sign) since hook drift has nothing to do with manifest
# signing timing.
declare -A HOOK_SOURCES=(
    [pre-commit]="scripts/pre-commit"
    [pre-push]="scripts/pre-push"
    [post-commit]="scripts/post-commit-doc-verify.sh"
)
for hook in "${!HOOK_SOURCES[@]}"; do
    src="${HOOK_SOURCES[${hook}]}"
    installed=".git/hooks/${hook}"
    if [[ ! -f "${src}" ]]; then
        warn "hook source ${src} not found -- skipping drift check for ${hook}"
    elif [[ ! -f "${installed}" ]]; then
        drift "${installed} is not installed at all (source: ${src})"
    elif ! diff -q "${src}" "${installed}" >/dev/null 2>&1; then
        drift "${installed} does not match ${src} -- re-run: cp ${src} ${installed} && chmod +x ${installed}"
    else
        ok "${installed} matches ${src}"
    fi
done

# -- 11. tracked .config/ mirrors (systemd units, podman quadlets) match
# their live installed copy where both exist (2026-08-26) -- same root
# cause as check 10, one directory up: this repo tracks a mirror of
# ~/.config/systemd/user/ and ~/.config/containers/systemd/ at the same
# relative path, kept in sync by hand via cp, with nothing enforcing it.
# Confirmed live: the C-22 quadlet edits (DEMO_MODE=true, demo-secrets.env
# EnvironmentFile) were made only to the installed copy and drifted from
# the tracked mirror for hours before an unrelated doc-verify pass caught
# it. Deliberately does NOT flag "tracked but not installed" as drift --
# a *.disabled file or a deliberately-dormant timer (e.g.
# research-board-mirror.timer, documented elsewhere as intentionally not
# installed) is a real, legitimate state this repo carries on purpose;
# only a genuine CONTENT disagreement between two copies that both exist
# is the failure mode this check exists to catch.
HOME_DIR="${HOME:-/home/corporatetraveldc}"
for rel in .config/systemd/user .config/containers/systemd; do
    [[ -d "${rel}" ]] || continue
    while IFS= read -r -d '' src; do
        installed="${HOME_DIR}/${src}"
        if [[ ! -f "${installed}" ]]; then
            warn "tracked ${src} has no live installed copy at ${installed} -- fine if deliberately disabled/dormant, otherwise install it"
        elif ! diff -q "${src}" "${installed}" >/dev/null 2>&1; then
            drift "${installed} does not match tracked ${src} -- re-run: cp ${src} ${installed}"
        else
            ok "${installed} matches tracked ${src}"
        fi
    done < <(find "${rel}" -maxdepth 1 -type f -print0)
done

echo "--"
if [[ ${DRIFT} -eq 1 ]]; then
    echo "[FAIL] drift found -- reconcile ${DOC} above, then re-run"
    exit 1
fi
echo "[OK] ${DOC} matches live state"
exit 0
