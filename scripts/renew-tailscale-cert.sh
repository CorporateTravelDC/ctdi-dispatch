#!/bin/bash
# /opt/corporatetraveldc/scripts/renew-tailscale-cert.sh
# Provision/renew the Tailscale HTTPS cert for
# corporatetraveldc-dispatch.tailxxxxxxx.ts.net, used by
# nginx/conf.d/tailscale-dispatch-runner.conf. Idempotent -- safe to run
# daily via corporatetraveldc-tailscale-cert-renew.timer; `tailscale cert
# --min-validity` only actually reissues when the current cert is within
# MIN_VALIDITY of expiring, so most runs are a no-op.
#
# Headscale note: this relies on Tailscale's HTTPS Certificates feature
# (must be enabled in the tailnet admin console -- already done here), which
# is brokered by tailscale.com's control plane. Headscale (self-hosted)
# does not implement the same automated ACME-issuance path as of this
# writing -- if this host's control server is ever switched to headscale,
# `tailscale cert` will fail here, and this script detects that case (via
# ControlURL) to say so explicitly rather than failing with a bare error.
# A headscale deployment needing HTTPS for this vhost would need its own
# externally-managed cert (e.g. DNS-01 ACME against a real public domain)
# and a change to how this vhost's cert files get populated.
#
# Usage: sudo renew-tailscale-cert.sh [--dry-run]
# ASCII output only

set -uo pipefail

CTDC_USER="corporatetraveldc"
DOMAIN="corporatetraveldc-dispatch.tailxxxxxxx.ts.net"
SSL_DIR="/etc/nginx/ssl"
CERT_FILE="${SSL_DIR}/tailscale-dispatch.crt"
KEY_FILE="${SSL_DIR}/tailscale-dispatch.key"
MIN_VALIDITY="720h" # 30 days
ENV_FILE="/etc/corporatetraveldc/dispatch.env"

DRY_RUN=0
for arg in "$@"; do
    case "${arg}" in
        --dry-run) DRY_RUN=1 ;;
    esac
done

[[ -f "${ENV_FILE}" ]] && source "${ENV_FILE}" 2>/dev/null || true
NTFY_BASE="${NTFY_BASE_URL:-http://127.0.0.1:2586}"
NTFY_OPS="${NTFY_OPS_TOPIC:-ops-health}"

say() {
    echo "[$(date '+%H:%M:%S')] $*"
}

run() {
    if (( DRY_RUN )); then
        say "  [DRY-RUN] $*"
        return 0
    fi
    "$@"
}

ntfy_send() {
    local topic="$1" title="$2" msg="$3"
    curl -sf --max-time 5 \
        -H "Title: ${title}" \
        -H "Priority: 3" \
        -d "${msg}" \
        "${NTFY_BASE}/${topic}" >/dev/null 2>&1 || true
}

if [[ $EUID -ne 0 ]]; then
    echo "ERROR: must run as root (sudo $0)"
    exit 1
fi

say "--------------------------------------"
say "  Tailscale HTTPS Cert Renewal"
say "  Domain: ${DOMAIN}"
say "--------------------------------------"

(( DRY_RUN )) && say "[DRY-RUN MODE -- no changes will be made]"

CONTROL_URL=$(sudo -u "${CTDC_USER}" tailscale debug prefs 2>/dev/null | grep -o '"ControlURL":"[^"]*"' | cut -d'"' -f4)
if [[ -n "${CONTROL_URL}" && "${CONTROL_URL}" != *".tailscale.com"* ]]; then
    say "[WARN] ControlURL is '${CONTROL_URL}', not tailscale.com -- this looks like"
    say "       headscale or a custom control server. 'tailscale cert' likely will not"
    say "       work here; see this script's header comment for the headscale caveat."
fi

run mkdir -p "${SSL_DIR}"

BEFORE_HASH=""
[[ -f "${CERT_FILE}" ]] && BEFORE_HASH=$(sha256sum "${CERT_FILE}" 2>/dev/null | cut -d' ' -f1)

say "Requesting cert (min-validity ${MIN_VALIDITY})..."
if (( DRY_RUN )); then
    say "  [DRY-RUN] tailscale cert --min-validity ${MIN_VALIDITY} (staged, then moved into ${SSL_DIR})"
else
    # tailscale cert runs as CTDC_USER (needs the user's tailscaled socket
    # access) but SSL_DIR is root-only -- stage in a location that user can
    # write, then have root move+chown into place. Keeps /etc/nginx/ssl
    # root-only rather than loosening it for this one write.
    STAGING_DIR=$(sudo -u "${CTDC_USER}" mktemp -d /tmp/corporatetraveldc-tailscale-cert-XXXXXX)
    trap 'rm -rf "${STAGING_DIR}"' RETURN

    if ! sudo -u "${CTDC_USER}" tailscale cert \
            --min-validity "${MIN_VALIDITY}" \
            --cert-file "${STAGING_DIR}/cert.crt" \
            --key-file "${STAGING_DIR}/cert.key" \
            "${DOMAIN}"; then
        say "[FAIL] tailscale cert failed -- see headscale note above if applicable"
        ntfy_send "${NTFY_OPS}" "Tailscale Cert Renewal Failed" \
            "renew-tailscale-cert.sh failed for ${DOMAIN}. Check journalctl -u corporatetraveldc-tailscale-cert-renew.service."
        rm -rf "${STAGING_DIR}"
        exit 1
    fi
    mv "${STAGING_DIR}/cert.crt" "${CERT_FILE}"
    mv "${STAGING_DIR}/cert.key" "${KEY_FILE}"
    rm -rf "${STAGING_DIR}"
    chown root:nginx "${CERT_FILE}" "${KEY_FILE}"
    chmod 644 "${CERT_FILE}"
    chmod 640 "${KEY_FILE}"
    # mv within the same filesystem doesn't relabel -- the staging dir under
    # /tmp carries user_tmp_t, which httpd_t can't read. semanage fcontext
    # for /etc/nginx/ssl (set once, see selinux/apply-selinux-policy.sh) only
    # defines the rule; restorecon is what actually applies it to this file.
    # Without this, every future scheduled renewal breaks nginx the same way
    # the first one did.
    restorecon "${CERT_FILE}" "${KEY_FILE}"
fi

AFTER_HASH=""
[[ -f "${CERT_FILE}" ]] && AFTER_HASH=$(sha256sum "${CERT_FILE}" 2>/dev/null | cut -d' ' -f1)

if [[ "${BEFORE_HASH}" == "${AFTER_HASH}" && -n "${BEFORE_HASH}" ]]; then
    say "  [OK] cert unchanged (still valid beyond ${MIN_VALIDITY}) -- no reload needed"
else
    say "  cert issued/renewed"
    say "Testing nginx config and reloading..."
    if run nginx -t; then
        run systemctl reload nginx
        say "  [OK] nginx reloaded"
        if (( ! DRY_RUN )); then
            ntfy_send "${NTFY_OPS}" "Tailscale Cert Renewed" \
                "New cert issued for ${DOMAIN}, nginx reloaded."
        fi
    else
        say "[FAIL] nginx config test failed after cert update -- not reloading"
        ntfy_send "${NTFY_OPS}" "Tailscale Cert Renewal -- nginx Test Failed" \
            "New cert written for ${DOMAIN} but nginx -t failed; nginx NOT reloaded, still running old cert."
        exit 1
    fi
fi

say ""
say "Done."
