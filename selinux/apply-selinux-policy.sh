#!/usr/bin/env bash
# =============================================================================
# selinux/apply-selinux-policy.sh
# [operator LLC] -- SELinux policy remediation + directory bootstrap
#
# Run as root before starting any corporatetraveldc services.
# Idempotent -- safe to re-run after package updates or Pi migration.
#
# Usage:
#   sudo ./selinux/apply-selinux-policy.sh [--raw-image-dir <path>] [--dry-run]
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Signed-manifest integrity self-check (docs/COMPLIANCE_SECURITY.md "Signed
# Manifest Integrity") -- this script runs as root and builds/loads SELinux
# policy modules; worth the same "verify before doing anything" treatment as
# the automated fail2ban/skill entrypoints, even though it's operator-run.
if ! "${REPO_ROOT}/scripts/verify-manifest.sh"; then
    echo "[FAIL] Integrity check failed -- refusing to run apply-selinux-policy.sh" >&2
    exit 5
fi

DRY_RUN=false
RAW_IMAGE_DIR="${HOME}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run) DRY_RUN=true; shift ;;
        --raw-image-dir) RAW_IMAGE_DIR="$2"; shift 2 ;;
        *) echo "[FAIL] Unknown argument: $1" >&2; exit 1 ;;
    esac
done

run() {
    if [[ "$DRY_RUN" == true ]]; then echo "[DRY]  $*"; else "$@"; fi
}

require_root() {
    if [[ "$EUID" -ne 0 ]]; then
        echo "[FAIL] Must be run as root." >&2; exit 1
    fi
}

check_deps() {
    local missing=()
    for cmd in semodule checkmodule semanage restorecon; do
        command -v "$cmd" &>/dev/null || missing+=("$cmd")
    done
    if [[ ${#missing[@]} -gt 0 ]]; then
        echo "[INFO] Installing missing tools: ${missing[*]}"
        run dnf install -y policycoreutils-python-utils checkpolicy
    fi
}

build_and_load_module() {
    local name="$1"
    local te_src="${SCRIPT_DIR}/${name}.te"
    [[ -f "$te_src" ]] || { echo "[FAIL] Missing: ${te_src}" >&2; return 1; }

    local work_dir
    work_dir="$(mktemp -d /tmp/selinux-${name}-XXXXXX)"
    trap "rm -rf ${work_dir}" RETURN

    cp "${te_src}" "${work_dir}/${name}.te"
    echo "[INFO] Compiling: ${name}"
    run checkmodule -M -m -o "${work_dir}/${name}.mod" "${work_dir}/${name}.te"
    run semodule_package -o "${work_dir}/${name}.pp" -m "${work_dir}/${name}.mod"

    # `semodule -u`/--upgrade is deprecated -- `-i`/--install now handles
    # both fresh-install and update-in-place for an already-loaded module of
    # the same name, so the old install-vs-upgrade branch here is no longer
    # needed (confirmed 2026-08-09: -i cleanly updated an already-loaded
    # module with no separate upgrade step).
    run semodule -i "${work_dir}/${name}.pp"
    echo "[OK]  ${name}"
}

label_container_path() {
    local path="$1"
    local owner="${2:-corporatetraveldc:corporatetraveldc}"
    local mode="${3:-0755}"
    echo "[INFO] Labeling: ${path}"
    run mkdir -p "${path}"
    run chown "${owner}" "${path}"
    run chmod "${mode}" "${path}"
    run semanage fcontext -a -t container_file_t "${path}(/.*)?" 2>/dev/null \
        || run semanage fcontext -m -t container_file_t "${path}(/.*)?
"
    run restorecon -Rv "${path}"
    echo "[OK]  ${path}"
}

require_root
check_deps

echo "=== [operator LLC] -- SELinux Policy Apply ==="
echo "[INFO] Dry run: ${DRY_RUN}"
echo ""

# ---------------------------------------------------------------------------
# Step 1 -- Runtime directory (/run/corporatetraveldc)
# Recreated on each boot via tmpfiles.d; also ensure it exists now.
# ---------------------------------------------------------------------------
echo "--- Step 1: /run/corporatetraveldc ---"
run mkdir -p /run/corporatetraveldc
run chown corporatetraveldc:corporatetraveldc /run/corporatetraveldc
run chmod 755 /run/corporatetraveldc
echo "[OK]  /run/corporatetraveldc"

# ---------------------------------------------------------------------------
# Step 2 -- Data directory (/var/lib/corporatetraveldc)
# ---------------------------------------------------------------------------
echo "--- Step 2: /var/lib/corporatetraveldc ---"
label_container_path "/var/lib/corporatetraveldc"
label_container_path "/var/lib/corporatetraveldc/acarshub"

# ---------------------------------------------------------------------------
# Step 3 -- Config directory (/etc/corporatetraveldc)
# Read-only mounts -- owned root:corporatetraveldc, mode 640 on files.
# ---------------------------------------------------------------------------
echo "--- Step 3: /etc/corporatetraveldc ---"
run mkdir -p /etc/corporatetraveldc
run chown root:corporatetraveldc /etc/corporatetraveldc
run chmod 750 /etc/corporatetraveldc
run semanage fcontext -a -t container_file_t "/etc/corporatetraveldc(/.*)?" 2>/dev/null \
    || run semanage fcontext -m -t container_file_t "/etc/corporatetraveldc(/.*)?
"
run restorecon -Rv /etc/corporatetraveldc
echo "[OK]  /etc/corporatetraveldc"

# ---------------------------------------------------------------------------
# Step 4 -- ntfy directories
# ---------------------------------------------------------------------------
echo "--- Step 4: ntfy directories ---"
label_container_path "/var/lib/ntfy"
# /etc/ntfy is read-only config -- label but keep root ownership
run mkdir -p /etc/ntfy
run semanage fcontext -a -t container_file_t "/etc/ntfy(/.*)?" 2>/dev/null \
    || run semanage fcontext -m -t container_file_t "/etc/ntfy(/.*)?
"
run restorecon -Rv /etc/ntfy
echo "[OK]  /etc/ntfy"

# ---------------------------------------------------------------------------
# Step 5 -- Relabel .raw image files to virt_image_t
# ---------------------------------------------------------------------------
echo "--- Step 5: virt_image_t relabel for .raw files ---"
mapfile -t raw_files < <(find "${RAW_IMAGE_DIR}" -maxdepth 3 -name "*.raw" -type f 2>/dev/null)
if [[ ${#raw_files[@]} -eq 0 ]]; then
    echo "[SKIP] No .raw files found under ${RAW_IMAGE_DIR}"
else
    for f in "${raw_files[@]}"; do
        echo "[INFO] Relabeling: ${f}"
        run chcon -t virt_image_t "${f}"
    done
    run semanage fcontext -a -t virt_image_t "${RAW_IMAGE_DIR}/[^/]*\.raw" 2>/dev/null \
        || run semanage fcontext -m -t virt_image_t "${RAW_IMAGE_DIR}/[^/]*\.raw"
fi

# ---------------------------------------------------------------------------
# Step 6 -- tailscaled policy
# ---------------------------------------------------------------------------
echo "--- Step 6: tailscaled policy ---"
if seinfo -t 2>/dev/null | grep -q "tailscaled_t"; then
    echo "[OK]  upstream tailscaled_t present"
    if semodule -l 2>/dev/null | grep -q "^corporatetraveldc-tailscaled$"; then
        run semodule -r corporatetraveldc-tailscaled
    fi
else
    if dnf info tailscale-selinux &>/dev/null; then
        run dnf install -y tailscale-selinux
        run restorecon -v "$(command -v tailscaled 2>/dev/null || echo /usr/sbin/tailscaled)"
    else
        build_and_load_module "corporatetraveldc-tailscaled"
    fi
fi

# ---------------------------------------------------------------------------
# Step 7 -- SDR USB passthrough (dumpvdl2, ultrafeeder/readsb)
# Both bind-mount /dev/bus/usb and AddDevice= their RTL-SDR dongle directly
# into container_t. Without an explicit allow, enforcing denies
# { open ioctl read write } on the usb_device_t chr_file and both decoders
# spin in a tight reopen/retry loop -- on 2026-07-09 that produced 6,700+ AVC
# denials in 17 minutes and made the box appear to lock up. This is the
# missing piece; without it, re-enabling enforcing WILL reproduce the
# incident. Scoped to just usb_device_t chr_file (not the broader
# container_use_devices boolean) since only these two containers need it.
# ---------------------------------------------------------------------------
echo "--- Step 7: SDR USB passthrough ---"
build_and_load_module "corporatetraveldc-sdr-usb"

# ---------------------------------------------------------------------------
# Step 8 -- nginx backend port labels
# See selinux/label-nginx-backend-ports.sh for the full port inventory and
# the procedure for adding a port when a new vhost is added.
# ---------------------------------------------------------------------------
echo "--- Step 8: nginx backend port labels ---"
run bash "${SCRIPT_DIR}/label-nginx-backend-ports.sh" $([[ "$DRY_RUN" == true ]] && echo --dry-run)

# ---------------------------------------------------------------------------
# Step 9 -- nginx outbound connect (name_connect)
# Labeling a port http_port_t (Step 8) only governs name_bind under stock
# policy -- httpd_t's name_connect to http_port_t is boolean-gated
# (httpd_can_network_connect), and that boolean's scope is "port_type", not
# just http_port_t. This module grants the same permission unconditionally
# but scoped to http_port_t only. Must run after Step 8.
# ---------------------------------------------------------------------------
echo "--- Step 9: nginx outbound connect ---"
build_and_load_module "corporatetraveldc-nginx-proxy"

# ---------------------------------------------------------------------------
# Step 10 -- nginx SSL cert directory (cert_t)
# /etc/nginx/ssl holds the Tailscale HTTPS cert for
# tailscale-dispatch-runner.conf (see scripts/renew-tailscale-cert.sh).
# Files land there via `mv` from a /tmp staging dir -- same-filesystem mv
# doesn't relabel, so without this rule httpd_t is denied reading a cert
# still carrying user_tmp_t. cert_t is the standard reference-policy type
# for TLS certs (matches /etc/pki/tls/certs), not a custom type.
# ---------------------------------------------------------------------------
echo "--- Step 10: nginx SSL cert directory ---"
run mkdir -p /etc/nginx/ssl
run semanage fcontext -a -t cert_t "/etc/nginx/ssl(/.*)?" 2>/dev/null \
    || run semanage fcontext -m -t cert_t "/etc/nginx/ssl(/.*)?"
run restorecon -Rv /etc/nginx/ssl

# ---------------------------------------------------------------------------
# Step 11 -- TE modules
# ---------------------------------------------------------------------------
echo "--- Step 11: TE modules ---"
build_and_load_module "corporatetraveldc-virtqemud"
build_and_load_module "corporatetraveldc-logind-userns"
build_and_load_module "corporatetraveldc-fail2ban-lockdown"
build_and_load_module "corporatetraveldc-fail2ban-cf-egress"

# ---------------------------------------------------------------------------
# Step 12 -- Verify
# ---------------------------------------------------------------------------
echo ""
echo "--- Step 12: Verify ---"
for mod in corporatetraveldc-sdr-usb corporatetraveldc-nginx-proxy corporatetraveldc-virtqemud corporatetraveldc-logind-userns corporatetraveldc-fail2ban-lockdown corporatetraveldc-fail2ban-cf-egress; do
    semodule -l 2>/dev/null | grep -q "^${mod}$" \
        && echo "[OK]  module: ${mod}" \
        || echo "[FAIL] module: ${mod}" >&2
done

for path in /var/lib/corporatetraveldc /var/lib/ntfy /etc/corporatetraveldc /etc/ntfy /run/corporatetraveldc /etc/nginx/ssl; do
    [[ -d "${path}" ]] \
        && echo "[OK]  exists: ${path}" \
        || echo "[FAIL] missing: ${path}" >&2
done

echo ""
echo "[OK]  Apply complete."
echo "[INFO] Restart tailscaled if it was previously blocked: sudo systemctl restart tailscaled"
echo "[INFO] Also run pihole-unbound-selinux-internal/selinux/apply-selinux-policy.sh"
echo "       before re-enabling enforcing -- it fixes the nginx/pihole port-80"
echo "       and pihole-webserver/8091 denials on the same host."
