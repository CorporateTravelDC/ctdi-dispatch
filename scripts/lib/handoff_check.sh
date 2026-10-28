#!/usr/bin/env bash
# scripts/lib/handoff_check.sh COPY PIN ACCOUNT REPO -- the content checks of an agent hand-off
# (2026-10-26). Called by scripts/handoff-apply.sh on its PRIVATE COPY of the patch, after that
# script has checked the account, the folder, ownership and symlinks. Kept separate so the tests
# exercise every refusal directly, with no bypass in handoff-apply.sh itself.
#
# Refuses (exit 1, "refused: ..." on stderr): a sha256 that is not the pin; a missing or wrong
# header (ctdc-handoff 1, account = ACCOUNT, base-commit present in REPO); binary content;
# symlinks or submodules; quoted (unusual) paths; and any path an agent hand-off may never
# change: .git/, the signed manifest, security/, and the Claude policy itself.
# On success prints, one per line: "base <sha>", "subject <text>", "careful <path>" for each
# privilege-related path the operator should review with extra care.
set -euo pipefail

REFUSED_PATHS='^(\.git(/|$)|.*/\.git(/|$)|MANIFEST\.sha256(\.asc)?$|security/|config/claude-code/|scripts/ctdc-claude-no-agent-hook\.py$|scripts/claude-policy-check\.sh$)'
CAREFUL_PATHS='^(scripts/agent-segmentation/|scripts/[^/]*(sudo|approv|sign|liveness|grant|access)[^/]*$|scripts/lib/handoff_check\.sh$|\.config/systemd/|systemd/|config/sudoers)'

die() { echo "refused: $*" >&2; exit 1; }
(( $# == 4 )) || { echo "usage: handoff_check.sh COPY PIN ACCOUNT REPO" >&2; exit 2; }
C="$1"; PIN="${2,,}"; A="$3"; REPO="$4"
[[ -f "$C" && ! -L "$C" ]] || die "${C} is not a regular file"
[[ "$PIN" =~ ^[0-9a-f]{64}$ ]] || die "SHA256 must be 64 hex characters"

got="$(sha256sum "$C" | cut -d' ' -f1)"
[[ "$got" == "$PIN" ]] || die "sha256 mismatch: the file is ${got}, the pin is ${PIN} -- ask ${A} to re-check, never re-pin to what the file says"

hdr() { awk -v k="$1" 'NR>8 || /^diff --git /{exit} index($0, k": ")==1 {print substr($0, length(k)+3); exit}' "$C"; }
[[ "$(hdr ctdc-handoff)" == 1 ]] || die "not a ctdc hand-off (missing 'ctdc-handoff: 1' header)"
[[ "$(hdr account)" == "$A" ]] || die "header names account '$(hdr account)', not ${A}"
BASE="$(hdr base-commit)"
[[ "$BASE" =~ ^[0-9a-f]{40}$ ]] || die "header base-commit is not a full commit id"
git -C "$REPO" cat-file -e "${BASE}^{commit}" 2>/dev/null || die "base-commit ${BASE} is not in ${REPO}"

grep -qE '^(Binary files |GIT binary patch)' "$C" && die "binary content"
grep -qE '^(new file mode|old mode|new mode|deleted file mode|index [0-9a-f]+\.\.[0-9a-f]+) 1[26]0000\b' "$C" && die "symlink or submodule entry"
grep -qE '^(diff --git .*"|(rename|copy) (from|to) ")' "$C" && die "quoted path (unusual characters in a file name)"
paths="$(awk '/^diff --git /{print substr($3,3); print substr($4,3)} /^rename (from|to) /{print substr($0, index($0,$3))} /^copy (from|to) /{print substr($0, index($0,$3))}' "$C" | sort -u)"
[[ -n "$paths" ]] || die "no file changes in the patch"
bad="$(grep -E "$REFUSED_PATHS" <<<"$paths" || true)"
[[ -z "$bad" ]] || die "touches paths an agent hand-off may not change: $(tr '\n' ' ' <<<"$bad")"

echo "base ${BASE}"
echo "subject $(hdr subject)"
grep -E "$CAREFUL_PATHS" <<<"$paths" | sed 's/^/careful /' || true
