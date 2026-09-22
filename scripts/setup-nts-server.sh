#!/bin/bash
# scripts/setup-nts-server.sh
# One-shot, idempotent setup of chrony as both an NTS server (for the
# tailnet) and an NTS client (authenticated upstream sync) -- see
# config/chrony-nts.conf's header for the full design rationale and the
# 2026-09-18 SELinux verification this relies on. Root required: this
# touches /etc/pki/tls, SELinux port labels, and /etc/chrony.conf.
#
# Usage: sudo scripts/setup-nts-server.sh [path-to-cert] [path-to-key]
#   Defaults to the cert/key already issued via:
#     tailscale cert corporatetraveldc-dispatch.tailxxxxxxx.ts.net
#   run from the operator's own shell (NOT this script -- Tailscale cert
#   issuance is an interactive/identity-bound operation, deliberately not
#   automated here on first run; scripts/tailscale-cert-refresh-nts.sh
#   handles unattended renewal once this initial setup is done).
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "XX must run as root (sudo)" >&2
    exit 1
fi

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
HOSTNAME="corporatetraveldc-dispatch.tailxxxxxxx.ts.net"
CERT_SRC="${1:-/home/corporatetraveldc/${HOSTNAME}.crt}"
KEY_SRC="${2:-/home/corporatetraveldc/${HOSTNAME}.key}"
CERT_DST="/etc/pki/tls/certs/${HOSTNAME}.crt"
KEY_DST="/etc/pki/tls/private/${HOSTNAME}.key"

if [[ ! -f "${CERT_SRC}" || ! -f "${KEY_SRC}" ]]; then
    echo "XX cert/key not found at ${CERT_SRC} / ${KEY_SRC}" >&2
    echo "   Issue one first (as the operator, not root):" >&2
    echo "     tailscale cert ${HOSTNAME}" >&2
    exit 2
fi

echo "[setup-nts] placing cert/key in standard PKI paths (cert_t by default)..."
install -o root -g root -m 0644 "${CERT_SRC}" "${CERT_DST}"
# 2026-09-18 fix: chronyd drops privilege to the 'chrony' group (PRIVDROP)
# after binding, so a root:root 0600 key is unreadable once dropped --
# root:chrony 0640 keeps it off-limits to everyone else while giving
# chronyd's actual runtime identity read access. Verified live: root:root
# 0600 produced "Missing read access ... Permission denied" in the NTS-KE
# log on first deploy.
install -o root -g chrony -m 0640 "${KEY_SRC}" "${KEY_DST}"
restorecon -v "${CERT_DST}" "${KEY_DST}"

echo "[setup-nts] registering NTS-KE port (TCP/4460) under ntske_port_t..."
if semanage port -l | grep -q "^ntske_port_t .*4460"; then
    echo "[setup-nts] already registered, skipping"
else
    semanage port -a -t ntske_port_t -p tcp 4460
fi

echo "[setup-nts] wiring confdir into /etc/chrony.conf..."
mkdir -p /etc/chrony.d
restorecon -v /etc/chrony.d
if ! grep -q "^confdir /etc/chrony.d" /etc/chrony.conf; then
    echo "" >> /etc/chrony.conf
    echo "# Drop-in configs (scripts/setup-nts-server.sh, 2026-09-18)" >> /etc/chrony.conf
    echo "confdir /etc/chrony.d" >> /etc/chrony.conf
    restorecon -v /etc/chrony.conf
fi

echo "[setup-nts] deploying tracked NTS config..."
install -o root -g root -m 0644 "${REPO_DIR}/config/chrony-nts.conf" /etc/chrony.d/50-nts.conf
restorecon -v /etc/chrony.d/50-nts.conf

echo "[setup-nts] restarting chronyd..."
systemctl restart chronyd

sleep 2
echo "[setup-nts] verification:"
echo "--- NTS server activity (should show ntske:4460 listening) ---"
ss -tlnp 2>/dev/null | grep 4460 || echo "  (not listening yet -- check journalctl -u chronyd)"
echo "--- NTS client sources (should show the 3 upstream NTS servers) ---"
chronyc -N authdata 2>&1 || true
echo "--- overall source status ---"
chronyc sources 2>&1 || true
echo
echo "[setup-nts] done. From another tailnet device, test with:"
echo "  chronyd -Q \"server corporatetraveldc-dispatch.tailxxxxxxx.ts.net iburst nts maxsamples 1\" -d"
