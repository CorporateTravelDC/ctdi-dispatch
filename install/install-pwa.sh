#!/usr/bin/env bash
# install-pwa.sh — Install the CS Executive Services Dispatch PWA
#
# What this does:
#   1. Creates /var/www/corporatetraveldc-pwa/
#   2. Copies index.html from src/pwa/
#   3. Installs the nginx vhost for ops.example.com
#   4. Splits ops off the shared dispatch/ops server block if still combined
#   5. Reloads nginx
#
# Run as: sudo bash /opt/corporatetraveldc/ctdi-dispatch-internal/install/install-pwa.sh
# Safe to re-run (idempotent).

set -euo pipefail

REPO="/opt/corporatetraveldc/ctdi-dispatch-internal"
PWA_DIR="/var/www/corporatetraveldc-pwa"
NGINX_CONF="/etc/nginx/conf.d/csexec-ops.conf"
DISPATCH_CONF="/etc/nginx/conf.d/csexec-dispatch.conf"

log()  { echo "[install-pwa] $*"; }
die()  { echo "[install-pwa] ERROR: $*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "Run with sudo"
[[ -f "${REPO}/src/pwa/index.html" ]] || die "src/pwa/index.html not found — run from repo root"

# ── 1. Create PWA web root ─────────────────────────────────────────────────────
log "Creating ${PWA_DIR}…"
mkdir -p "${PWA_DIR}"
chown root:nginx "${PWA_DIR}" 2>/dev/null || chown root:root "${PWA_DIR}"
chmod 755 "${PWA_DIR}"

# ── 2. Copy PWA files ──────────────────────────────────────────────────────────
log "Installing PWA index.html…"
cp "${REPO}/src/pwa/index.html" "${PWA_DIR}/index.html"
chmod 644 "${PWA_DIR}/index.html"
log "  → ${PWA_DIR}/index.html"

# Copy manifest if present
if [[ -f "${REPO}/src/pwa/manifest.json" ]]; then
    cp "${REPO}/src/pwa/manifest.json" "${PWA_DIR}/manifest.json"
    chmod 644 "${PWA_DIR}/manifest.json"
    log "  → ${PWA_DIR}/manifest.json"
fi

# ── 3. Remove ops from shared dispatch server block (if still combined) ─────────
if grep -q "ops\.csexecutiveservices\.com" "${DISPATCH_CONF}" 2>/dev/null; then
    log "Splitting ops off from dispatch server block…"
    # Remove 'ops.example.com' from the server_name line
    sed -i 's/ ops\.csexecutiveservices\.com//' "${DISPATCH_CONF}"
    log "  Updated ${DISPATCH_CONF}"
fi

# ── 4. Install ops nginx vhost ─────────────────────────────────────────────────
log "Installing nginx vhost: ${NGINX_CONF}…"
cp "${REPO}/config/nginx-csexec-ops.conf" "${NGINX_CONF}"
chmod 644 "${NGINX_CONF}"

# ── 5. Test and reload nginx ───────────────────────────────────────────────────
log "Testing nginx config…"
nginx -t || die "nginx config test failed — check ${NGINX_CONF}"

log "Reloading nginx…"
systemctl reload nginx
log ""
log "Done. PWA is live at https://ops.example.com"
log "API proxy: https://ops.example.com/api/v1/*"
