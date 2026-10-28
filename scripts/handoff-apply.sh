#!/usr/bin/env bash
# scripts/handoff-apply.sh -- the operator applies an agent's hand-off (2026-10-26).
#
#   scripts/handoff-apply.sh ACCOUNT PATCH SHA256     apply into a fresh worktree
#   scripts/handoff-apply.sh --list ACCOUNT           list that account's hand-offs
#
# PATCH is what scripts/handoff-put.sh wrote, under
# /srv/ctdc-handoff/NAME-from-ACCOUNT/NAME.patch; SHA256 is the hash the
# agent reported to you (the pin). The hand-off folder is shared by every
# account on the box, so the pin -- not the folder -- is what makes the bytes the agent's:
#
#   - PATCH must be a regular file (no symlink anywhere on the path) inside the
#     drop, owned by ACCOUNT, in a folder owned by ACCOUNT;
#   - it is copied to a private temp file FIRST and the copy is hashed, so a
#     swap after the check changes nothing;
#   - then scripts/lib/handoff_check.sh checks the copy: the pin; header ctdc-handoff 1,
#     account = ACCOUNT, base-commit present in this repo; and refuses outright binary files, symlinks, submodules, anything under .git/,
#     the signed manifest, security/, and the Claude policy itself
#     (config/claude-code/, the no-agent hook, the policy check) -- change those
#     by hand, never through an agent hand-off;
#   - applied with `git apply --index` into a NEW detached worktree at the base
#     commit, next to this checkout. Nothing is committed, signed or pushed:
#     review it there, then commit and sign as usual.
#
# Run as the operator, without sudo.
set -euo pipefail

REPO="${HANDOFF_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
DROP="${HANDOFF_DROP:-/srv/ctdc-handoff}"
WT_PARENT="${HANDOFF_WORKTREE_PARENT:-$(dirname "$REPO")}"
MAX_BYTES=$((5 * 1024 * 1024))

die() { echo "refused: $*" >&2; exit 1; }
account_ok() { [[ "$1" =~ ^ctdc-[a-z0-9-]{1,48}$ ]] && getent passwd "$1" >/dev/null; }

if [[ "${1:-}" == --list ]]; then
    A="${2:-}"; account_ok "$A" || die "unknown account '${A}'"
    shopt -s nullglob
    found=0
    for f in "${DROP}"/*-from-"${A}"/*.patch; do
        [[ -L "$f" || ! -f "$f" ]] && continue
        found=1
        printf '%s  %8s B  %s  %s\n' "$(sha256sum "$f" | cut -d' ' -f1)" "$(stat -c %s "$f")" \
            "$(date -u -d "@$(stat -c %Y "$f")" +%Y-%m-%dT%H:%MZ)" "$f"
    done
    (( found )) || echo "no hand-offs from ${A} in ${DROP}"
    exit 0
fi

(( $# == 3 )) || { sed -n '2,8p' "$0" >&2; exit 2; }
A="$1"; P="$2"; PIN="${3,,}"
account_ok "$A" || die "unknown account '${A}'"
[[ "$PIN" =~ ^[0-9a-f]{64}$ ]] || die "SHA256 must be 64 hex characters"
[[ "$(id -un)" != "$A" ]] || die "an account cannot apply its own hand-off"

# path: inside the drop, no symlink on the way, owned by the account
[[ "$P" == /* ]] || P="${DROP}/${P}"
dir="$(dirname "$P")"; base="$(basename "$P")"
[[ "$(dirname "$dir")" == "$DROP" ]] || die "${P} is not directly in a folder of ${DROP}"
[[ "$(basename "$dir")" == *-from-"$A" ]] || die "folder $(basename "$dir") does not end in -from-${A}"
[[ "$base" =~ ^[a-z0-9][a-z0-9._-]{0,63}\.patch$ ]] || die "patch name '${base}' is not a hand-off name"
[[ -L "$DROP" || -L "$dir" || -L "$P" ]] && die "symlink on the path"
[[ -f "$P" ]] || die "${P} is not a regular file"
[[ "$(stat -c %U "$dir")" == "$A" ]] || die "folder is owned by $(stat -c %U "$dir"), not ${A}"
[[ "$(stat -c %U "$P")" == "$A" ]] || die "patch is owned by $(stat -c %U "$P"), not ${A}"
(( $(stat -c %s "$P") <= MAX_BYTES )) || die "patch is larger than ${MAX_BYTES} bytes"

# copy first, then hash the copy: what is checked is what is applied
work="$(mktemp -d)"; chmod 0700 "$work"
trap 'rm -rf "$work"' EXIT
C="${work}/handoff.patch"
cat -- "$P" >"$C"
(( $(stat -c %s "$C") <= MAX_BYTES )) || die "patch grew past ${MAX_BYTES} bytes while copying"
# hash, header and content: scripts/lib/handoff_check.sh (also what the tests drive directly)
checked="$(bash "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/handoff_check.sh" "$C" "$PIN" "$A" "$REPO")" || exit 1
BASE="$(sed -n 's/^base //p' <<<"$checked")"
SUBJECT="$(sed -n 's/^subject //p' <<<"$checked")"
careful="$(sed -n 's/^careful //p' <<<"$checked")"

# apply into a fresh worktree at the base commit
name="${base%.patch}"
WT="${WT_PARENT}/$(basename "$REPO")-handoff-${name}"
[[ -e "$WT" ]] && die "${WT} already exists (an earlier apply?) -- remove it with: git -C ${REPO} worktree remove ${WT}"
git -C "$REPO" worktree add --quiet --detach "$WT" "$BASE"
if ! git -C "$WT" apply --index --check "$C" 2>"${work}/err" || ! git -C "$WT" apply --index "$C" 2>"${work}/err"; then
    git -C "$REPO" worktree remove --force "$WT" >/dev/null 2>&1 || true
    sed 's/^/  /' "${work}/err" >&2
    die "the patch does not apply cleanly at ${BASE:0:12}"
fi

echo "applied ${A}'s hand-off (sha256 ${PIN:0:16}..., pinned) into ${WT}"
echo "  base ${BASE:0:12}: $(git -C "$REPO" log -1 --format=%s "$BASE")"
echo "  subject: ${SUBJECT}"
behind="$(git -C "$REPO" rev-list --count "${BASE}..HEAD" 2>/dev/null || echo '?')"
[[ "$behind" == 0 ]] || echo "  note: the base is ${behind} commit(s) behind this checkout's HEAD"
git -C "$WT" diff --cached --stat | sed 's/^/  /'
if [[ -n "$careful" ]]; then
    echo "  REVIEW WITH CARE -- touches privilege-related paths:"
    sed 's/^/    /' <<<"$careful"
fi
echo "nothing was committed. Review in ${WT} (git -C ${WT} diff --cached), then commit and sign as usual,"
echo "or discard with: git -C ${REPO} worktree remove --force ${WT}"
