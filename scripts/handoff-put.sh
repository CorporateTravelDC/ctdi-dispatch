#!/usr/bin/env bash
# scripts/handoff-put.sh -- an agent hands a change to the operator (2026-10-26).
#
# Agent accounts read the checkout but cannot write it, and cannot commit
# (managed Claude policy). They work in their OWN clone, then run this from
# that clone:
#
#   scripts/handoff-put.sh NAME [--subject "one line"] [--repo DIR]
#
# It writes the clone's whole uncommitted change against HEAD (tracked edits,
# new files, deletions; the clone's own index is not touched) as ONE patch:
#
#   /srv/ctdc-handoff/NAME-from-ACCOUNT/NAME.patch   (dir 2755, file 0644)
#
# and prints its sha256. Tell the operator the account, NAME and that sha256;
# they apply it with scripts/handoff-apply.sh, which refuses any byte that
# does not match the hash you reported. Nothing here commits, signs or pushes.
# Binary files, symlinks and submodules cannot be handed off this way.
set -euo pipefail

DROP="${HANDOFF_DROP:-/srv/ctdc-handoff}"
NAME=""; SUBJECT=""; REPO=""
while (( $# )); do
    case "$1" in
        --subject) SUBJECT="${2:?--subject needs a value}"; shift 2 ;;
        --repo) REPO="${2:?--repo needs a value}"; shift 2 ;;
        -h|--help) sed -n '2,21p' "$0"; exit 0 ;;
        -*) echo "unknown option $1" >&2; exit 2 ;;
        *) [[ -z "$NAME" ]] || { echo "one NAME only" >&2; exit 2; }; NAME="$1"; shift ;;
    esac
done
[[ "$NAME" =~ ^[a-z0-9][a-z0-9._-]{0,63}$ ]] || { echo "NAME must match [a-z0-9][a-z0-9._-]{0,63}" >&2; exit 2; }
[[ "$SUBJECT" != *$'\n'* ]] || { echo "--subject must be one line" >&2; exit 2; }
REPO="$(git -C "${REPO:-.}" rev-parse --show-toplevel)"
ACCOUNT="$(id -un)"
BASE="$(git -C "$REPO" rev-parse --verify 'HEAD^{commit}')"

dir="${DROP}/${NAME}-from-${ACCOUNT}"
out="${dir}/${NAME}.patch"
[[ -e "$out" ]] && { echo "${out} already exists -- pick a new NAME (hand-offs are never overwritten)" >&2; exit 1; }

# a private copy of the index, so the agent's own staging is left as it was
idx="$(mktemp)"; body="$(mktemp)"
trap 'rm -f "$idx" "$body"' EXIT
gitdir_index="$(git -C "$REPO" rev-parse --git-path index)"
[[ "$gitdir_index" == /* ]] || gitdir_index="${REPO}/${gitdir_index}"
if [[ -f "$gitdir_index" ]]; then cp "$gitdir_index" "$idx"; else rm -f "$idx"; fi
GIT_INDEX_FILE="$idx" git -C "$REPO" add -A
GIT_INDEX_FILE="$idx" git -C "$REPO" diff --cached --full-index --no-color --no-ext-diff --no-textconv HEAD >"$body"
[[ -s "$body" ]] || { echo "nothing to hand off: the clone has no change against HEAD" >&2; exit 1; }
if grep -qE '^(Binary files |GIT binary patch)' "$body"; then
    echo "the change contains binary files; hand those off another way" >&2; exit 1
fi

umask 022
install -d -m 2755 "$dir"
tmp="$(mktemp "${dir}/.${NAME}.XXXXXX")"
{
    printf 'ctdc-handoff: 1\n'
    printf 'account: %s\n' "$ACCOUNT"
    printf 'base-commit: %s\n' "$BASE"
    printf 'created-utc: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'subject: %s\n' "${SUBJECT:-$NAME}"
    printf '\n'
    cat "$body"
} >"$tmp"
chmod 0644 "$tmp"
mv -n "$tmp" "$out"
[[ -e "$tmp" ]] && { rm -f "$tmp"; echo "${out} appeared meanwhile -- pick a new NAME" >&2; exit 1; }

sum="$(sha256sum "$out" | cut -d' ' -f1)"
echo "handed off: ${out}"
git -C "$REPO" apply --numstat "$out" | awk '{a+=$1; d+=$2; n++} END {printf "  %d file(s), +%d -%d\n", n, a, d}'
echo "  sha256: ${sum}"
echo "operator applies it with:"
echo "  scripts/handoff-apply.sh ${ACCOUNT} ${out} ${sum}"
