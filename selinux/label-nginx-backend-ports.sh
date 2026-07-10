#!/usr/bin/env bash
# selinux/label-nginx-backend-ports.sh
# Label every port host nginx (httpd_t) reverse-proxies to as http_port_t, so
# `name_connect` is allowed under SELinux enforcing.
#
# Why a port list instead of the httpd_can_network_connect boolean:
# COMPLIANCE_SECURITY.md #4 documents this platform's SELinux posture as
# "ready-to-use Type Enforcement modules ... targeted enforcement contexts" --
# i.e. scoped, auditable grants, not blanket domain-wide booleans.
# httpd_can_network_connect would let httpd_t connect to ANY port on the
# host, which is a materially larger grant than "nginx may reach the eight
# backends this repo actually defines." Each port below traces to exactly
# one proxy_pass in nginx/conf.d/ or config/, so `semanage port -l` stays a
# complete, self-explaining audit trail of what nginx is allowed to reach.
#
# Root cause this fixes: on 2026-07-09 SELinux enforcing denied nginx
# { name_connect } to port 8001 (the ops.csexecutiveservices.com backend),
# discovered live after the original SDR-USB/pihole-port-80 incident was
# already fixed -- confirming every proxy_pass target needs its own label,
# not just the ones that happened to be live during the incident.
#
# --- Scaling procedure: adding a new nginx vhost ---
# 1. Add the vhost's proxy_pass target to nginx/conf.d/ as usual.
# 2. Add one line to PORTS below: "<port>:<vhost file>:<what it is>".
# 3. Re-run this script (idempotent -- safe against ports already labeled).
# 4. Commit the PORTS change in the same PR as the vhost -- keeps the port
#    grant and the thing that needs it auditable as one unit of change.
#
# Fedora 44 -- all architectures
#
# Usage:
#   sudo bash selinux/label-nginx-backend-ports.sh [--dry-run]

set -euo pipefail

DRY_RUN=false
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run) DRY_RUN=true; shift ;;
        *) echo "[FAIL] Unknown argument: $1" >&2; exit 1 ;;
    esac
done

run() {
    if [[ "$DRY_RUN" == true ]]; then echo "[DRY]  $*"; else "$@"; fi
}

if [[ "$EUID" -ne 0 ]]; then
    echo "[FAIL] Must be run as root." >&2; exit 1
fi

command -v semanage &>/dev/null || {
    echo "[INFO] Installing policycoreutils-python-utils..."
    run dnf install -y policycoreutils-python-utils
}

# port:vhost file (nginx/conf.d/ unless noted):purpose
PORTS=(
    "2586:ntfy.csexecutiveservices.com.conf:ntfy push server"
    "3000:openwebui.csexecutiveservices.com.conf:Open WebUI"
    "8000:dispatch.csexecutiveservices.com.conf:dispatch REST API + SSE events"
    "8001:config/nginx-corporatetraveldc-ops.conf + tailscale-dispatch-runner.conf:dispatch-runner app"
    "8080:adsb.csexecutiveservices.com.conf:adsb-ultrafeeder web UI"
    "8091:pihole.csexecutiveservices.com.conf:Pi-hole webserver"
    "9081:acars.csexecutiveservices.com.conf:acarshub web UI"
    "11434:ollama.csexecutiveservices.com.conf:Ollama daemon"
)

echo "=== label-nginx-backend-ports.sh ==="
echo "[INFO] Dry run: ${DRY_RUN}"

for entry in "${PORTS[@]}"; do
    port="${entry%%:*}"
    rest="${entry#*:}"
    vhost="${rest%%:*}"
    purpose="${rest#*:}"

    if semanage port -l | grep -qE "http_port_t.*tcp.*\b${port}\b"; then
        echo "[OK]  ${port}/tcp already labeled http_port_t (${purpose})"
    else
        echo "[INFO] Labeling ${port}/tcp as http_port_t (${purpose} -- ${vhost})"
        run semanage port -a -t http_port_t -p tcp "${port}"
        echo "[OK]  ${port}/tcp labeled"
    fi
done

echo ""
echo "[OK]  Done. Verify with: semanage port -l | grep http_port_t"
