#!/usr/bin/env bash
# demo-portal-init.sh -- create (or refresh) the on-disk tree the two
# persistent demo portals serve from (2026-09-06, docs/DEMO_PORTALS.md):
#
#   /home/corporatetraveldc/demos/_portal/
#     personal/
#       nginx.conf              rendered from scripts/templates/demo-portal-nginx.conf.tmpl
#       placeholder/index.html  "nothing mounted" page (served when current is absent)
#       current -> ../../<slug> NOT created here -- scripts/demo-portal.sh mount
#     client/
#       (same)
#
# Mounts nothing. Safe to re-run: nginx.conf and the placeholder are
# rewritten in place (same inode, so a running container's bind mount of
# nginx.conf keeps pointing at the live file); an existing `current`
# symlink is left alone. nginx only reads nginx.conf at start, so a
# re-render of the config still needs a unit restart to take effect --
# the symlink swap is the thing that does not.
#
# Usage: demo-portal-init.sh            (both portals)
#        demo-portal-init.sh personal   (one portal)
# Env: DEMO_PORTAL_ROOT overrides /home/corporatetraveldc/demos (tests).
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=scripts/demo-portal.sh
source "${REPO_DIR}/scripts/demo-portal.sh"

TEMPLATE="${REPO_DIR}/scripts/templates/demo-portal-nginx.conf.tmpl"
[[ -f "$TEMPLATE" ]] || die "template missing: ${TEMPLATE}"

render_placeholder() {
    local portal="$1" out="$2"
    cat > "$out" <<EOF
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>Nothing mounted -- ${portal} demo portal</title>
<style>
  body { margin: 0; min-height: 100vh; display: flex; align-items: center; justify-content: center;
         font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
         background: #111418; color: #d7dce2; }
  main { max-width: 34rem; padding: 2rem; text-align: center; }
  h1 { font-size: 1.25rem; font-weight: 600; margin: 0 0 .5rem; }
  p { margin: .5rem 0; line-height: 1.5; color: #9aa3ad; }
  code { color: #d7dce2; }
</style>
</head>
<body>
<main>
<h1>Nothing is mounted on this preview right now.</h1>
<p>This is the <code>${portal}</code> demo portal of [operator LLC]. A preview site will appear here when one is mounted.</p>
<p>If you were sent this link, ask the person who sent it to mount the preview again.</p>
</main>
</body>
</html>
EOF
}

init_portal() {
    local portal="$1" dir port
    require_portal "$portal"
    dir="${PORTAL_ROOT}/${portal}"
    port="$(portal_port "$portal")"
    echo "[demo-portal-init] ${portal}: ${dir} (port ${port})"
    mkdir -p "${dir}/placeholder"
    # In-place rewrite (> truncates the same inode) -- see header.
    sed -e "s/__PORT__/${port}/g" \
        -e "s/__PORTAL__/${portal}/g" \
        -e "s/__DISPLAY_NAME__/$(portal_display_name "$portal")/g" \
        "$TEMPLATE" > "${dir}/nginx.conf"
    render_placeholder "$portal" "${dir}/placeholder/index.html"
    if [[ -L "${dir}/current" ]]; then
        echo "[demo-portal-init] ${portal}: current -> $(readlink "${dir}/current") (left as is)"
    else
        echo "[demo-portal-init] ${portal}: nothing mounted (placeholder will be served)"
    fi
}

main_init() {
    [[ -d "$DEMOS_ROOT" ]] || die "demos root does not exist: ${DEMOS_ROOT}"
    mkdir -p "$PORTAL_ROOT"
    if [[ $# -eq 0 ]]; then
        init_portal personal
        init_portal client
    else
        local p
        for p in "$@"; do init_portal "$p"; done
    fi
    echo "[demo-portal-init] done. Mount something with: scripts/demo-portal.sh <personal|client> mount <slug>"
}

main_init "$@"
