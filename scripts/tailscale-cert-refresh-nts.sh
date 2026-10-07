#!/bin/bash
# scripts/tailscale-cert-refresh-nts.sh
# Renews the Tailscale-issued cert chrony's NTS server uses (short-lived,
# ~90 days) and deploys it to /etc/pki/tls + restarts chronyd when it changed.
#
# 2026-10-05: runs as ROOT from the installed copy
# (corporatetraveldc-nts-cert-refresh.{service,timer}, system units, via
# install-root-copies.sh). It used to be an operator USER unit that called
# `sudo` for the deploy -- which needs a password, so the first real renewal
# (2026-10-05 02:15) fetched the new cert and then failed: "sudo: a terminal
# is required". Root runs `tailscale cert` itself (root may always talk to
# tailscaled), sources nothing, and logs to the journal only (nothing written
# under /var/lib/corporatetraveldc, the container mount -- 2026-10-04 lesson).
#
# Idempotent: `tailscale cert` no-ops if the current cert still has
# significant lifetime left, so safe to run on any cadence.
set -euo pipefail
(( EUID == 0 )) || { echo "tailscale-cert-refresh-nts: run as root (system unit)" >&2; exit 77; }

HOSTNAME="corporatetraveldc-dispatch.tailxxxxxxx.ts.net"
WORKDIR="$(mktemp -d)"
trap 'rm -rf "${WORKDIR}"' EXIT
chmod 0700 "${WORKDIR}"
log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

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

log "cert changed -- deploying"
install -o root -g root -m 0644 "${WORKDIR}/${HOSTNAME}.crt" "${CERT_DST}"
# root:chrony 0640 -- see setup-nts-server.sh's matching comment, 2026-09-18.
install -o root -g chrony -m 0640 "${WORKDIR}/${HOSTNAME}.key" "${KEY_DST}"
restorecon "${CERT_DST}" "${KEY_DST}"
systemctl restart chronyd
log "deployed and chronyd restarted"
