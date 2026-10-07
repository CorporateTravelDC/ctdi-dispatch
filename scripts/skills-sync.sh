#!/usr/bin/env bash
# scripts/skills-sync.sh -- twin gate for agent skills (backlog 2A, 2026-10-03).
#
# The signed manifest covers the skills tracked in this repo; the copies
# that actually EXECUTE live under ~/.claude/skills. Until tonight those two
# trees had diverged for weeks (513 live files, 2 tracked; the live
# flight-hifi-track was the pre-2026-08-31 version that still called a
# banned third-party API; the live context-guardian scripts were a stale
# Sep-28 snapshot). This script makes the repo the single source of truth.
#
#   skills-sync.sh check                 hash-compare tracked <-> live; exit 1 on any
#                                        divergence or missing-live; WARN on live
#                                        project dirs that are untracked
#   skills-sync.sh apply --i-am-the-operator
#                                        copy repo -> live (overwrites live). Operator-
#                                        run only: it changes what agents execute.
#   skills-sync.sh list                  print the tracked<->live mapping
#
# Mapping: every dir under skills/<name>/ maps to ~/.claude/skills/<name>/,
# plus .claude/skills/dispatch-context-guardian (tracked there because the
# user-level Stop hook in ~/.claude/settings.json runs it by absolute repo
# path) maps to ~/.claude/skills/dispatch-context-guardian. Vendor dirs are
# listed in scripts/lib/skills-vendor-ignore.txt (shared with the drift
# check) and never touched in either direction.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LIVE_ROOT="${SKILLS_LIVE_ROOT:-${HOME}/.claude/skills}"
IGNORE_FILE="${REPO_ROOT}/scripts/lib/skills-vendor-ignore.txt"

# Tracked location -> live name. Add a line here when a skill is tracked
# somewhere other than skills/<name>.
declare -A EXTRA_MAP=(
    [".claude/skills/dispatch-context-guardian"]="dispatch-context-guardian"
)

usage() { sed -n '2,25p' "$0" | sed 's/^# \{0,1\}//'; exit 64; }

load_ignore() {
    IGNORE_EXACT=(); IGNORE_GLOB=()
    while IFS= read -r line; do
        line="${line%%#*}"; line="${line// /}"
        [[ -z "$line" ]] && continue
        if [[ "$line" == *'*'* ]]; then IGNORE_GLOB+=("$line"); else IGNORE_EXACT+=("$line"); fi
    done < "$IGNORE_FILE"
}

is_vendor() {
    local name="$1" g
    for g in "${IGNORE_EXACT[@]}"; do [[ "$name" == "$g" ]] && return 0; done
    for g in "${IGNORE_GLOB[@]}"; do [[ "$name" == $g ]] && return 0; done
    return 1
}

# Emit "tracked_dir<TAB>live_name" for every tracked skill.
mapping() {
    local d
    for d in "${REPO_ROOT}"/skills/*/; do
        [[ -d "$d" ]] || continue
        d="${d%/}"
        printf '%s\t%s\n' "skills/$(basename "$d")" "$(basename "$d")"
    done
    local k
    for k in "${!EXTRA_MAP[@]}"; do
        [[ -d "${REPO_ROOT}/${k}" ]] && printf '%s\t%s\n' "$k" "${EXTRA_MAP[$k]}"
    done
}

hash_tree() {  # $1 = dir; prints "relpath<TAB>sha256" sorted
    ( cd "$1" 2>/dev/null && find . -type f -not -path '*/__pycache__/*' -print0 | sort -z | xargs -0 -r sha256sum | awk '{print $2 "\t" $1}' | sed 's#^\./##' )
}

cmd_list() { mapping | awk -F'\t' '{printf "  %-45s -> %s/%s\n", $1, ENVIRON["LIVE_ROOT"], $2}'; }

cmd_check() {
    local rc=0 tracked live name
    local n_tracked=0 n_div=0 n_missing=0 n_untracked=0
    while IFS=$'\t' read -r tracked name; do
        n_tracked=$((n_tracked+1))
        live="${LIVE_ROOT}/${name}"
        if [[ ! -d "$live" ]]; then
            echo "[MISSING-LIVE] ${tracked} is tracked but ${live} does not exist"
            n_missing=$((n_missing+1)); rc=1; continue
        fi
        local diffout
        diffout="$(diff <(hash_tree "${REPO_ROOT}/${tracked}") <(hash_tree "$live") || true)"
        if [[ -n "$diffout" ]]; then
            echo "[DIVERGENT]    ${tracked} != ${live}"
            # file-level detail: which paths differ / exist on one side only
            diff <(hash_tree "${REPO_ROOT}/${tracked}" | cut -f1) <(hash_tree "$live" | cut -f1) \
                | sed -n 's/^< /    only-in-repo: /p; s/^> /    only-in-live: /p'
            comm -12 <(hash_tree "${REPO_ROOT}/${tracked}" | cut -f1) <(hash_tree "$live" | cut -f1) | while read -r f; do
                a="$(sha256sum "${REPO_ROOT}/${tracked}/${f}" | cut -c1-12)"; b="$(sha256sum "${live}/${f}" | cut -c1-12)"
                [[ "$a" != "$b" ]] && echo "    differs:      ${f} (repo ${a} / live ${b})"
            done
            n_div=$((n_div+1)); rc=1
        fi
    done < <(mapping)

    # Live project dirs with no tracked counterpart (not vendor) -> WARN only.
    local d
    for d in "${LIVE_ROOT}"/*/ "${LIVE_ROOT}"/.[!.]*/; do
        [[ -d "$d" ]] || continue
        name="$(basename "${d%/}")"
        is_vendor "$name" && continue
        if ! mapping | cut -f2 | grep -qx -- "$name"; then
            echo "[WARN]         ${d%/} executes live but is not tracked anywhere in the repo"
            n_untracked=$((n_untracked+1))
        fi
    done

    echo "skills-sync check: ${n_tracked} tracked, ${n_div} divergent, ${n_missing} missing-live, ${n_untracked} untracked-live (vendor dirs ignored via $(basename "$IGNORE_FILE"))"
    if [[ $rc -ne 0 ]]; then
        echo "To make live match the signed repo (operator-run; changes what agents execute):"
        echo "  ${REPO_ROOT}/scripts/skills-sync.sh apply --i-am-the-operator"
    fi
    return $rc
}

cmd_apply() {
    if [[ "${1:-}" != "--i-am-the-operator" ]]; then
        echo "apply overwrites ${LIVE_ROOT}/<skill> with the repo copy -- the trees that agents" >&2
        echo "actually execute. Operator-run only. Re-run as:" >&2
        echo "  ${REPO_ROOT}/scripts/skills-sync.sh apply --i-am-the-operator" >&2
        return 77
    fi
    local tracked name live
    while IFS=$'\t' read -r tracked name; do
        live="${LIVE_ROOT}/${name}"
        mkdir -p "$live"
        # mirror: files removed from the repo copy are removed live too
        rsync -a --delete --exclude='__pycache__' "${REPO_ROOT}/${tracked}/" "${live}/"
        echo "applied ${tracked} -> ${live}"
    done < <(mapping)
    cmd_check
}

load_ignore
export LIVE_ROOT
case "${1:-}" in
    check) cmd_check ;;
    apply) shift; cmd_apply "$@" ;;
    list)  cmd_list ;;
    *)     usage ;;
esac
