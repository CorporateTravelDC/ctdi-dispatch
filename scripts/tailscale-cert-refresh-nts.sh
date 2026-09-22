#!/bin/bash
# scripts/tailscale-cert-refresh-nts.sh
# Renews the Tailscale-issued cert chrony's NTS server uses (short-lived,
# ~90 days) and reloads chronyd. `tailscale cert` itself needs no
# elevated privileges; placing the renewed cert/key into /etc/pki/tls
# does, so this script re-execs itself under sudo for that part only.
#
# Idempotent: `tailscale cert` no-ops (fast, no reissue) if the current
# cert still has significant lifetime left, so safe to run on any cadence.
set -euo pipefail

HOSTNAME="corporatetraveldc-dispatch.tailxxxxxxx.ts.net"
WORKDIR="$(mktemp -d)"
trap 'rm -rf "${WORKDIR}"' EXIT
STATE_DIR="/var/lib/corporatetraveldc/tailscale-cert-refresh-nts"
LOG_FILE="${STATE_DIR}/refresh.log"
mkdir -p "${STATE_DIR}" 2>/dev/null || true

log() {
    local ts; ts=$(date '+%Y-%m-%d %H:%M:%S')
    echo "[${ts}] $*" | tee -a "${LOG_FILE}" 2>/dev/null
}

cd "${WORKDIR}"
log "requesting/renewing cert for ${HOSTNAME}"
tailscale cert "${HOSTNAME}"

CERT_DST="/etc/pki/tls/certs/${HOSTNAME}.crt"
KEY_DST="/etc/pki/tls/private/${HOSTNAME}.key"

# Only touch anything if the renewed cert actually differs from what's
# deployed -- avoids an unnecessary chronyd restart (and its brief NTS
# service gap) on every timer fire.
if [[ -f "${CERT_DST}" ]] && cmp -s "${WORKDIR}/${HOSTNAME}.crt" "${CERT_DST}"; then
    log "cert unchanged, nothing to deploy"
    exit 0
fi

log "cert changed -- deploying (requires sudo)"
sudo install -o root -g root -m 0644 "${WORKDIR}/${HOSTNAME}.crt" "${CERT_DST}"
# root:chrony 0640 -- see setup-nts-server.sh's matching comment, 2026-09-18.
sudo install -o root -g chrony -m 0640 "${WORKDIR}/${HOSTNAME}.key" "${KEY_DST}"
sudo restorecon "${CERT_DST}" "${KEY_DST}"
sudo systemctl restart chronyd
log "deployed and chronyd restarted"
