#!/usr/bin/env bash
# scripts/agent-segmentation/srv-migrate-20261026.sh -- one-time move to the new /srv layout
# (operator 2026-10-26). Run AFTER srv-layout.sh has created the folders.
#
# MIRRORS (copies, never moves or deletes) every item of the two retired folders to its
# new home, owners and modes preserved, then checks each copy byte for byte:
#
#   /srv/ctdc-publish-drop/es-*, *substack*, feed-test-*, week-*  -> /srv/ctdc-blog/
#   /srv/ctdc-publish-drop/<anything else>                        -> /srv/ctdc-handoff/
#   /srv/ctdc-publish-research/<anything>                         -> /srv/ctdc-handoff/research-intake/
#
# Then each retired folder gets MOVED-2026-10-26.md (root 0444: where every item went)
# and loses its write bits, so nothing new lands in a retired path. The originals stay
# where they are, readable as before. Re-running is safe: an item already mirrored
# identically is reported and skipped; one that differs stops the run.
#
#   srv-migrate-20261026.sh [--execute]     dry run by default; --execute needs root
set -euo pipefail

SRV="${SRV_ROOT:-/srv}"
EXECUTE=0; [[ "${1:-}" == --execute ]] && EXECUTE=1
if (( EXECUTE )) && [[ -z "${SRV_ROOT:-}" ]]; then
    (( EUID == 0 )) || { echo "srv-migrate: --execute needs root" >&2; exit 77; }
fi
DROP="${SRV}/ctdc-publish-drop"; RESEARCH="${SRV}/ctdc-publish-research"
BLOG="${SRV}/ctdc-blog"; HANDOFF="${SRV}/ctdc-handoff"; INTAKE="${HANDOFF}/research-intake"
for d in "$BLOG" "$HANDOFF" "$INTAKE"; do
    [[ -d "$d" ]] || { echo "srv-migrate: ${d} is missing -- run srv-layout.sh --execute first" >&2; exit 2; }
done

dest_for() {  # <old folder> <item name> -> new parent folder
    if [[ "$1" == "$RESEARCH" ]]; then echo "$INTAKE"; return; fi
    case "$2" in
        es-*|*substack*|feed-test-*|week-*) echo "$BLOG" ;;
        *) echo "$HANDOFF" ;;
    esac
}

declare -A moved=()
for old in "$DROP" "$RESEARCH"; do
    [[ -d "$old" ]] || { echo "   # ${old} does not exist -- nothing to mirror"; continue; }
    lines=""
    shopt -s nullglob dotglob
    for src in "$old"/*; do
        item="$(basename "$src")"
        [[ "$item" == MOVED-2026-10-26.md ]] && continue
        to="$(dest_for "$old" "$item")"
        if [[ -e "${to}/${item}" ]]; then
            if diff -rq --no-dereference "$src" "${to}/${item}" >/dev/null 2>&1; then
                echo "   = ${item} already mirrored to ${to}/"
            else
                echo "srv-migrate: ${to}/${item} exists and differs from ${src} -- stopping, nothing overwritten" >&2; exit 3
            fi
        else
            echo "   $ cp -a ${src} ${to}/"
            if (( EXECUTE )); then
                cp -a --no-dereference "$src" "${to}/"
                diff -rq --no-dereference "$src" "${to}/${item}" >/dev/null || { echo "srv-migrate: copy of ${item} differs -- stopping" >&2; exit 4; }
            fi
        fi
        lines+="| \`${item}\` | \`${to}/${item}\` |"$'\n'
    done
    shopt -u nullglob dotglob
    moved[$old]="$lines"
done

for old in "$DROP" "$RESEARCH"; do
    [[ -d "$old" ]] || continue
    ptr="${old}/MOVED-2026-10-26.md"
    # retired already (pointer present, nothing can have been added since): leave it as it is
    # (mode bits, not -w: root passes -w on anything)
    if [[ -f "$ptr" ]] && (( (8#$(stat -c %a "$old") & 8#222) == 0 )); then echo "   = ${old} already retired (${ptr})"; continue; fi
    echo "   $ write ${ptr}; chmod a-w ${old}"
    (( EXECUTE )) || continue
    tmp="$(mktemp)"
    {
        echo "# This folder is retired (2026-10-26)"
        echo
        echo "Operator decision 2026-10-26: the shared /srv folders are now"
        echo
        echo "- \`${HANDOFF}/\` -- the one shared drop for every account (patches: scripts/handoff-put.sh)"
        echo "- \`${INTAKE}/\` -- research intake (was ${RESEARCH})"
        echo "- \`${BLOG}/\` -- Executive Standard / Substack material (operator only)"
        echo "- \`${SRV}/ctdc-<name>/\` -- one private folder per agent account (that account and the operator only)"
        echo
        echo "Everything that was here was COPIED to its new home (originals left in place, read-only):"
        echo
        echo "| item | new home |"
        echo "|---|---|"
        printf '%s' "${moved[$old]:-}"
        echo
        echo "Write nothing here. Details: docs/AGENT_SEGMENTATION.md ('Shared /srv folders')."
    } >"$tmp"
    install -m 0444 "$tmp" "$ptr"
    (( EUID != 0 )) || chown root:root "$ptr"
    rm -f "$tmp"
    chmod a-w "$old"
done
(( EXECUTE )) || echo "(dry run: nothing changed)"
