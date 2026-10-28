#!/usr/bin/env bash
# scripts/agent-segmentation/srv-access.sh -- who else may open a private /srv folder
# (operator 2026-10-26). Root runs the installed copy (/usr/local/libexec/ctdc/srv-access.sh,
# put there by install-root-copies.sh after the manifest verifies), never the checkout.
#
# Private folders (/srv/ctdc-blog and every per-account /srv/ctdc-<name>) are reachable
# only by their owner and the operator. Any other account gets in only by an explicit
# directive, which this records:
#
#   sudo srv-access.sh grant  ACCOUNT FOLDER --council council-<id> [--write]
#   sudo srv-access.sh grant  ACCOUNT FOLDER --operator "<reason>"   [--write]
#   sudo srv-access.sh revoke ACCOUNT FOLDER
#   srv-access.sh list [FOLDER]
#
# FOLDER is the folder name (ctdc-ea, ctdc-blog, ...). A grant is read-only unless
# --write; it adds a named ACL entry for ACCOUNT on the folder and everything in it
# (plus a default entry, so new files are covered). Revoke removes exactly that
# account's entries. /srv/ctdc-handoff is shared already and is refused here.
# Every grant and revoke is appended to /var/log/ctdc-srv-access.log (root 0640):
# UTC time, the human behind sudo, action, account, folder, mode, directive.
set -euo pipefail

SRV="${SRV_ROOT:-/srv}"
REGISTRY="${CTDC_REGISTRY:-/etc/ctdc-accounts.conf}"
LOG="${SRV_ACCESS_LOG:-/var/log/ctdc-srv-access.log}"
die() { echo "srv-access: $*" >&2; exit 2; }

folder_path() {
    [[ "$1" =~ ^ctdc-[a-z0-9]{2,24}$ ]] || die "folder must look like ctdc-<name>"
    [[ "$1" != ctdc-handoff ]] || die "ctdc-handoff is shared with every account already"
    [[ "$1" != ctdc-publish-drop && "$1" != ctdc-publish-research ]] || die "$1 is retired"
    local p="${SRV}/$1"
    [[ -d "$p" && ! -L "$p" ]] || die "${p} does not exist"
    echo "$p"
}

audit() {
    local who="${SUDO_USER:-$(id -un)}"
    printf '%s\t%s\t%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$who" "$*" >>"$LOG"
    chmod 0640 "$LOG" 2>/dev/null || true
}

cmd="${1:-}"; shift || true
case "$cmd" in
    list)
        for p in "${SRV}"/ctdc-*/; do
            p="${p%/}"; n="$(basename "$p")"
            [[ -z "${1:-}" || "$1" == "$n" ]] || continue
            extra="$(getfacl -cp "$p" 2>/dev/null | awk -F: '$1=="user" && $2!="" {print $2"("$3")"}' | sort -u | tr '\n' ' ')"
            printf '%-24s owner %-32s named: %s\n' "$n" "$(stat -c %U "$p")" "${extra:-none}"
        done
        exit 0 ;;
    grant|revoke) ;;
    *) sed -n '2,24p' "$0" >&2; exit 2 ;;
esac

[[ -n "${SRV_ROOT:-}" ]] || (( EUID == 0 )) || die "${cmd} needs root (sudo)"
A="${1:-}"; F="${2:-}"; shift 2 || die "usage: ${cmd} ACCOUNT FOLDER ..."
awk -v n="$A" '$1==n {f=1} END {exit !f}' "$REGISTRY" || die "${A} is not a registered team account"
P="$(folder_path "$F")"
[[ "$(stat -c %U "$P")" != "$A" ]] || die "${A} owns ${F} already"

if [[ "$cmd" == revoke ]]; then
    # -P: never follow a symlink an account left in its folder (root walks the tree)
    setfacl -R -P -x "u:${A}" "$P" 2>/dev/null || true
    find "$P" -type d -exec setfacl -P -d -x "u:${A}" {} + 2>/dev/null || true
    audit "revoke	${A}	${F}"
    echo "revoked ${A} on ${P}"
    exit 0
fi

mode=rX; directive=""
while (( $# )); do
    case "$1" in
        --write) mode=rwX; shift ;;
        --council) [[ "${2:-}" =~ ^council-[A-Za-z0-9-]{3,64}$ ]] || die "--council needs council-<id>"; directive="council ${2}"; shift 2 ;;
        --operator) [[ -n "${2:-}" && "${2}" != *$'\n'* && "${2}" != *$'\t'* ]] || die "--operator needs a one-line reason"; directive="operator: ${2}"; shift 2 ;;
        *) die "unknown option $1" ;;
    esac
done
[[ -n "$directive" ]] || die "a grant needs --council council-<id> or --operator \"<reason>\""
# -P: never follow a symlink an account left in its folder (root walks the tree)
setfacl -R -P -m "u:${A}:${mode}" "$P"
find "$P" -type d -exec setfacl -P -d -m "u:${A}:${mode}" {} +   # defaults: directories only
audit "grant	${A}	${F}	${mode}	${directive}"
echo "granted ${A} ${mode} on ${P} (${directive}); revoke: sudo $0 revoke ${A} ${F}"
