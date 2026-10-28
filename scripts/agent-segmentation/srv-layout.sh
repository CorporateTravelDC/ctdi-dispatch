#!/usr/bin/env bash
# scripts/agent-segmentation/srv-layout.sh -- the shared /srv folders (operator 2026-10-26).
#
#   /srv/ctdc-handoff            root:ctdc-dev 3775   the ONE shared drop: every account writes its
#                                                     own entries, nobody deletes another's (sticky)
#     research-intake/           root:ctdc-dev 3775   the operator's research drops; agents watch it
#     status/                    operator:ctdc-dev 2750  scripts/agent-status-snapshot.py writes here
#   /srv/ctdc-blog               operator:operator 2770  Executive Standard / Substack material
#   /srv/ctdc-<name>             <account>:operator 2770 one PRIVATE folder per agent or service
#                                                     account in ctdc-dev (box or cloud); <name> is
#                                                     the last part of the account name
#                                                     (ctdc-agent-anthropic-claude-ea -> ctdc-ea)
#
# Private folders are reachable only by their account and the operator. Anyone
# else gets in only through scripts/agent-segmentation/srv-access.sh (root):
# the operator's own directive or a convened council's, logged, revocable.
# Default ACLs keep the operator (and the owning account) able to read what the
# other writes, whatever the writer's umask. Named ACL entries added by
# srv-access.sh are never removed here.
#
#   srv-layout.sh [--execute]     dry run by default; --execute needs root
#   SRV_ROOT / CTDC_REGISTRY / CTDC_OPERATOR override paths for tests
set -euo pipefail

SRV="${SRV_ROOT:-/srv}"
REGISTRY="${CTDC_REGISTRY:-/etc/ctdc-accounts.conf}"
OPERATOR="${CTDC_OPERATOR:-corporatetraveldc}"
DEV_GROUP="${CTDC_DEV_GROUP:-ctdc-dev}"
RESERVED="handoff blog status publish-drop publish-research"
EXECUTE=0; [[ "${1:-}" == --execute ]] && EXECUTE=1
if (( EXECUTE )) && [[ -z "${SRV_ROOT:-}" ]]; then
    (( EUID == 0 )) || { echo "srv-layout: --execute needs root" >&2; exit 77; }
fi

run() { printf '   $ %s\n' "$*"; (( EXECUTE )) || return 0; "$@"; }

# short folder name for an account: the last dash-separated part of its name
short_name() { local n="${1##*-}"; [[ "$n" =~ ^[a-z0-9]{2,24}$ ]] && echo "$n"; }

dir() {  # path owner group mode
    run install -d -m "$4" -o "$2" -g "$3" "$1"
    run chown "$2:$3" "$1"
    run chmod "$4" "$1"
}

private() {  # path owner -- owner + operator only, defaults so each can read what the other writes
    dir "$1" "$2" "$OPERATOR" 2770
    run setfacl -m "u::rwx,g::rwx,o::---,d:u::rwx,d:g::rwx,d:o::---,d:u:${OPERATOR}:rwx,d:u:$2:rwx" "$1"
}

echo "== shared folders under ${SRV}"
dir "${SRV}/ctdc-handoff" root "$DEV_GROUP" 3775
dir "${SRV}/ctdc-handoff/research-intake" root "$DEV_GROUP" 3775
dir "${SRV}/ctdc-handoff/status" "$OPERATOR" "$DEV_GROUP" 2750
private "${SRV}/ctdc-blog" "$OPERATOR"

echo "== one private folder per account in ${DEV_GROUP}"
members=" $(getent group "$DEV_GROUP" | cut -d: -f4 | tr ',' ' ') "
declare -A seen=()
fails=0
while read -r name kind _; do
    [[ -n "$name" && "$name" != \#* ]] || continue
    [[ "$kind" == agent || "$kind" == service ]] || continue
    [[ "$members" == *" ${name} "* ]] || { echo "   # skip ${name}: not in ${DEV_GROUP}"; continue; }
    short="$(short_name "$name")" || true
    if [[ -z "$short" ]]; then echo "   ! ${name}: cannot derive a folder name" >&2; fails=1; continue; fi
    if [[ " $RESERVED " == *" $short "* ]]; then echo "   ! ${name}: folder name '${short}' is reserved" >&2; fails=1; continue; fi
    if [[ -n "${seen[$short]:-}" ]]; then echo "   ! ${name}: ctdc-${short} already belongs to ${seen[$short]}" >&2; fails=1; continue; fi
    seen[$short]="$name"
    owner="$(stat -c %U "${SRV}/ctdc-${short}" 2>/dev/null || true)"
    if [[ -n "$owner" && "$owner" != "$name" ]]; then echo "   ! ctdc-${short} exists and is owned by ${owner}, not ${name} -- left alone" >&2; fails=1; continue; fi
    echo "   # ${name} -> ${SRV}/ctdc-${short}"
    private "${SRV}/ctdc-${short}" "$name"
done < <(awk 'NF>=2 {print $1, $2}' "$REGISTRY")
(( fails == 0 )) || { echo "srv-layout: some accounts were skipped (see above)" >&2; exit 1; }
(( EXECUTE )) || echo "(dry run: nothing changed)"
