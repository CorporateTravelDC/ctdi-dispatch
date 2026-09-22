#!/usr/bin/env bash
# demo-portal.sh -- mount / unmount / inspect what sits behind one of the
# two persistent website demo portals (2026-09-06, docs/DEMO_PORTALS.md).
#
# The portals are two long-running nginx containers whose host ports and
# Cloudflare Tunnel hostnames are fixed ONCE and never change:
#
#   personal  port 8087  preview.example.com
#   client    port 8088  client-preview.example.com
#
# What each one serves is chosen by a single relative symlink,
#   /home/corporatetraveldc/demos/_portal/<portal>/current -> ../../<slug>
# which nginx follows per request (the whole demos/ tree is bind-mounted
# read-only into the container), so swapping it needs NO unit restart.
# A demo is any /home/corporatetraveldc/demos/<slug>/ that has site/ and
# auth/.htpasswd -- the same layout scripts/new-client-demo.sh scaffolds.
# Each demo keeps its own .htpasswd, so a client mounted on the client
# portal sees only their own credential.
#
# Usage:
#   demo-portal.sh <personal|client> status
#   demo-portal.sh <personal|client> mount <slug>
#   demo-portal.sh <personal|client> unmount
#
# Idempotent, no sudo, no systemctl start/stop -- this script only ever
# touches the symlink. Env overrides (tests use them):
#   DEMO_PORTAL_ROOT        demos root (default /home/corporatetraveldc/demos)
#   DEMO_PORTAL_SKIP_PROBE  =1 skips the systemctl / curl probes in `status`
set -euo pipefail

DEMOS_ROOT="${DEMO_PORTAL_ROOT:-/home/corporatetraveldc/demos}"
PORTAL_ROOT="${DEMOS_ROOT}/_portal"
UNIT_PREFIX="corporatetraveldc-demo-portal-"
DOMAIN="example.com"

# The fixed allocation. Changing a port here means changing PORTS.md, the
# tracked .container file, the rendered nginx.conf (re-run
# demo-portal-init.sh) AND the tunnel ingress -- that is the whole point
# of "persistent": you should never need to.
portal_port() {
    case "$1" in
        personal) echo 8087 ;;
        client)   echo 8088 ;;
        *) return 1 ;;
    esac
}

portal_hostname() {
    case "$1" in
        personal) echo "preview.${DOMAIN}" ;;
        client)   echo "client-preview.${DOMAIN}" ;;
        *) return 1 ;;
    esac
}

portal_display_name() {
    case "$1" in
        personal) echo "[operator LLC] -- personal preview" ;;
        client)   echo "Client preview" ;;
        *) return 1 ;;
    esac
}

usage() {
    cat >&2 <<EOF
usage: demo-portal.sh <personal|client> <status|mount <slug>|unmount>
EOF
    exit 2
}

die() { echo "ERROR: $*" >&2; exit 1; }

require_portal() {
    case "${1:-}" in
        personal|client) ;;
        *) echo "ERROR: portal must be 'personal' or 'client' (got: '${1:-}')" >&2; usage ;;
    esac
}

# What `current` points at, as a slug, or empty when unmounted/dangling.
current_slug() {
    local portal="$1" link target
    link="${PORTAL_ROOT}/${portal}/current"
    [[ -L "$link" ]] || return 0
    target="$(readlink "$link")"
    # Only the ../../<slug> shape this script writes is a valid mount.
    if [[ "$target" =~ ^\.\./\.\./([a-z0-9][a-z0-9-]*)/?$ ]]; then
        echo "${BASH_REMATCH[1]}"
    else
        echo "$target"
    fi
}

demo_is_mountable() {
    local slug="$1"
    [[ -d "${DEMOS_ROOT}/${slug}/site" ]] && [[ -f "${DEMOS_ROOT}/${slug}/auth/.htpasswd" ]]
}

cmd_mount() {
    local portal="$1" slug="${2:-}"
    [[ -n "$slug" ]] || { echo "ERROR: mount needs a <slug>" >&2; usage; }
    if [[ ! "$slug" =~ ^[a-z0-9][a-z0-9-]*$ ]]; then
        die "slug must be lowercase alnum/hyphens (got: ${slug})"
    fi
    [[ "$slug" != "_portal" ]] || die "'_portal' is the portal directory, not a demo"
    local demo="${DEMOS_ROOT}/${slug}"
    [[ -d "$demo" ]] || die "no such demo: ${demo}"
    [[ -d "${demo}/site" ]] || die "refusing: ${demo}/site does not exist"
    [[ -f "${demo}/auth/.htpasswd" ]] || die "refusing: ${demo}/auth/.htpasswd does not exist (every demo keeps its own credential; create it with htpasswd first)"
    [[ -d "${PORTAL_ROOT}/${portal}" ]] || die "portal '${portal}' is not initialised -- run scripts/demo-portal-init.sh first"

    local link="${PORTAL_ROOT}/${portal}/current"
    local tmp="${PORTAL_ROOT}/${portal}/.current.tmp.$$"
    if [[ "$(current_slug "$portal")" == "$slug" ]]; then
        echo "[demo-portal] ${portal}: '${slug}' already mounted (no change)"
    else
        # Atomic replace: build the new link beside the old one, then
        # rename over it. Readers (nginx, per request) see either the old
        # target or the new one, never a missing link.
        rm -f "$tmp"
        ln -s "../../${slug}" "$tmp"
        mv -T "$tmp" "$link"
        echo "[demo-portal] ${portal}: mounted '${slug}' (current -> ../../${slug})"
    fi
    echo "[demo-portal] local:   http://127.0.0.1:$(portal_port "$portal")/"
    echo "[demo-portal] tailnet: http://100.x.x.x:$(portal_port "$portal")/"
    echo "[demo-portal] public:  https://$(portal_hostname "$portal")/"
}

cmd_unmount() {
    local portal="$1"
    local link="${PORTAL_ROOT}/${portal}/current"
    if [[ -L "$link" ]]; then
        local was
        was="$(current_slug "$portal")"
        rm -f "$link"
        echo "[demo-portal] ${portal}: unmounted '${was}' (placeholder page now served)"
    elif [[ -e "$link" ]]; then
        die "${link} exists but is not a symlink -- refusing to remove it"
    else
        echo "[demo-portal] ${portal}: nothing mounted (no change)"
    fi
}

cmd_status() {
    local portal="$1" port unit link slug state="unmounted"
    port="$(portal_port "$portal")"
    unit="${UNIT_PREFIX}${portal}.service"
    link="${PORTAL_ROOT}/${portal}/current"
    slug="$(current_slug "$portal")"
    if [[ -n "$slug" ]]; then
        if demo_is_mountable "$slug"; then
            state="mounted"
        else
            state="dangling (placeholder served)"
        fi
    fi
    echo "portal:   ${portal}"
    echo "port:     ${port}"
    echo "hostname: $(portal_hostname "$portal")"
    echo "unit:     ${unit}"
    echo "mounted:  ${slug:-<nothing>}"
    echo "state:    ${state}"
    [[ -d "${PORTAL_ROOT}/${portal}" ]] || echo "warning:  ${PORTAL_ROOT}/${portal} missing -- run scripts/demo-portal-init.sh"

    if [[ "${DEMO_PORTAL_SKIP_PROBE:-0}" == "1" ]]; then
        echo "active:   (probe skipped)"
        echo "http:     (probe skipped)"
        return 0
    fi
    local active="unknown"
    if command -v systemctl >/dev/null 2>&1; then
        active="$(systemctl --user is-active "$unit" 2>/dev/null || true)"
        [[ -n "$active" ]] || active="unknown"
    fi
    echo "active:   ${active}"
    local code="unreachable"
    if command -v curl >/dev/null 2>&1; then
        code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 "http://127.0.0.1:${port}/" 2>/dev/null || true)"
        [[ -n "$code" && "$code" != "000" ]] || code="unreachable"
    fi
    # Expect 401 when a demo is mounted (Basic Auth, no creds given),
    # 200 when the placeholder is up, "unreachable" when the unit is down.
    echo "http:     ${code}"
}

main() {
    [[ $# -ge 2 ]] || usage
    local portal="$1" action="$2"
    require_portal "$portal"
    case "$action" in
        status)  cmd_status "$portal" ;;
        mount)   cmd_mount "$portal" "${3:-}" ;;
        unmount) cmd_unmount "$portal" ;;
        *) echo "ERROR: unknown action '${action}'" >&2; usage ;;
    esac
}

# Sourced by demo-portal-init.sh for the port/hostname table; only run
# main when executed directly.
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi
